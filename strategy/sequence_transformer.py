"""
Sequence Transformer Architecture for Time-Series Trading
=========================================================

An Attention-based Transformer designed to replace or augment LSTMs.
It processes a sequence of past market data (e.g. 60 bars) to predict 
future direction by learning temporal dependencies and feature interactions.

Features:
- Multi-Head Attention to capture complex sequential patterns
- Attention weight extraction to see *which* historical bars influenced the prediction
- Supports ATR-based multi-class classification (Win/Loss)
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import pandas as pd
import joblib
import os
from sklearn.preprocessing import StandardScaler
from pathlib import Path

class PositionalEncoding(nn.Module):
    """
    Injects information about the relative or absolute position of the tokens in the sequence.
    """
    def __init__(self, d_model, max_len=5000):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
        
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe.unsqueeze(0)) # Shape: (1, max_len, d_model)

    def forward(self, x):
        """
        Args:
            x: Tensor, shape [batch_size, seq_len, embedding_dim]
        """
        seq_len = x.size(1)
        x = x + self.pe[:, :seq_len, :]
        return x

class SequenceTransformerBlock(nn.Module):
    """Single transformer block with multi-head attention + FFN tailored for sequences."""
    def __init__(self, embed_dim, num_heads, ffn_dim, dropout=0.1):
        super().__init__()
        self.attention = nn.MultiheadAttention(
            embed_dim, num_heads, dropout=dropout, batch_first=True
        )
        self.ln1 = nn.LayerNorm(embed_dim)
        self.ln2 = nn.LayerNorm(embed_dim)
        self.ffn = nn.Sequential(
            nn.Linear(embed_dim, ffn_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(ffn_dim, embed_dim),
            nn.Dropout(dropout)
        )
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        """
        x: (batch, seq_len, embed_dim)
        returns: (output, attention_weights)
        """
        # Self-attention
        attn_out, attn_weights = self.attention(x, x, x)
        x = x + self.dropout(attn_out)
        x = self.ln1(x)
        
        # FFN
        ffn_out = self.ffn(x)
        x = x + self.dropout(ffn_out)
        x = self.ln2(x)
        return x, attn_weights

class SequenceTransformer(nn.Module):
    """
    Full Architecture for Sequence Prediction.
    Maps raw tabular features into an embedding dimension, adds positional encoding,
    and passes through Transformer layers.
    """
    def __init__(self, input_features, seq_len=60, embed_dim=64, num_layers=2, num_heads=4, ffn_dim=128, dropout=0.1, num_classes=2):
        super().__init__()
        self.seq_len = seq_len
        self.embed_dim = embed_dim
        
        # Initial projection to embedding space
        self.feature_embedder = nn.Sequential(
            nn.Linear(input_features, embed_dim),
            nn.LayerNorm(embed_dim),
            nn.GELU()
        )
        
        self.pos_encoder = PositionalEncoding(embed_dim, max_len=max(500, seq_len * 2))
        
        # Transformer Stack
        self.layers = nn.ModuleList([
            SequenceTransformerBlock(embed_dim, num_heads, ffn_dim, dropout)
            for _ in range(num_layers)
        ])
        
        # Classification Head (processes the final sequence state)
        self.head = nn.Sequential(
            nn.Linear(embed_dim, 64),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes)
        )

    def forward(self, x, return_attention=False):
        """
        x: (batch, seq_len, input_features)
        """
        # Embed and Add Positional Encoding
        x = self.feature_embedder(x)
        x = self.pos_encoder(x)
        
        all_attentions = []
        for layer in self.layers:
            x, attn = layer(x)
            if return_attention:
                all_attentions.append(attn)
                
        # Aggregate temporal sequence: We can use Global Average Pooling over the time dimension
        # Or just take the last sequence token representation: x[:, -1, :] 
        # Using the last representation as it holds the accumulated context
        context = x[:, -1, :]
        
        logits = self.head(context)
        
        if return_attention:
            return logits, all_attentions
        return logits

class SequenceTransformerPredictor:
    """Wrapper for training and inference, similarly designed to the TabTransformer wrapper."""
    def __init__(self, input_features, seq_len=60, embed_dim=64, num_layers=2, num_heads=4, ffn_dim=128, dropout=0.1, device='cpu', lr=0.001):
        self.device = device if torch.cuda.is_available() else 'cpu'
        self.seq_len = seq_len
        self.scaler = StandardScaler()
        self.feature_cols = None
        self.is_fitted = False
        
        self.model = SequenceTransformer(
            input_features=input_features,
            seq_len=seq_len,
            embed_dim=embed_dim,
            num_layers=num_layers,
            num_heads=num_heads,
            ffn_dim=ffn_dim,
            dropout=dropout,
            num_classes=2
        ).to(self.device)
        
        self.optimizer = optim.AdamW(self.model.parameters(), lr=lr, weight_decay=1e-5)
        self.criterion = nn.CrossEntropyLoss()
        self.lr_scheduler = optim.lr_scheduler.ReduceLROnPlateau(self.optimizer, mode='max', factor=0.5, patience=5)

    def fit(self, X_seq, y, X_val_seq=None, y_val=None, epochs=50, batch_size=32, verbose=True):
        """
        Train the model using Sequence Windows.
        X_seq shape: (num_samples, seq_len, num_features)
        y shape: (num_samples,)
        """
        self.is_fitted = True
        
        num_samples, seq_len, num_features = X_seq.shape
        X_flat = X_seq.reshape(-1, num_features)
        total_rows = X_flat.shape[0]
        
        # Memory-efficient scaling: fit `StandardScaler` incrementally in chunks
        chunk_size = 500_000
        for start_idx in range(0, total_rows, chunk_size):
            end_idx = min(start_idx + chunk_size, total_rows)
            # We must use partial_fit to avoid OOM
            self.scaler.partial_fit(X_flat[start_idx:end_idx])
            
        # Transform incrementally as well to avoid allocating a massive contiguous scaled block
        X_scaled_flat = np.empty_like(X_flat, dtype=np.float32)
        for start_idx in range(0, total_rows, chunk_size):
            end_idx = min(start_idx + chunk_size, total_rows)
            X_scaled_flat[start_idx:end_idx] = self.scaler.transform(X_flat[start_idx:end_idx])
            
        X_scaled = X_scaled_flat.reshape(num_samples, seq_len, num_features)
        
        X_tensor = torch.tensor(X_scaled, dtype=torch.float32)
        y_tensor = torch.tensor(y, dtype=torch.long)
        
        # Validation data (Handle scaling securely in chunks if large)
        has_val = X_val_seq is not None and y_val is not None
        if has_val:
            val_samples, _, _ = X_val_seq.shape
            X_val_flat = X_val_seq.reshape(-1, num_features)
            val_total_rows = X_val_flat.shape[0]
            
            X_val_scaled_flat = np.empty_like(X_val_flat, dtype=np.float32)
            for start_idx in range(0, val_total_rows, chunk_size):
                end_idx = min(start_idx + chunk_size, val_total_rows)
                X_val_scaled_flat[start_idx:end_idx] = self.scaler.transform(X_val_flat[start_idx:end_idx])
                
            X_val_scaled = X_val_scaled_flat.reshape(val_samples, seq_len, num_features)
            
            X_val_tensor = torch.tensor(X_val_scaled, dtype=torch.float32)
            y_val_tensor = torch.tensor(y_val, dtype=torch.long)
            
        best_val_acc = 0
        patience_counter = 0
        
        for epoch in range(epochs):
            self.model.train()
            indices = np.random.permutation(len(X_tensor))
            total_loss = 0
            batch_count = 0
            
            for i in range(0, len(X_tensor), batch_size):
                batch_indices = indices[i:i+batch_size]
                X_batch = X_tensor[batch_indices].to(self.device)
                y_batch = y_tensor[batch_indices].to(self.device)
                
                self.optimizer.zero_grad()
                logits = self.model(X_batch)
                loss = self.criterion(logits, y_batch)
                
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_norm=1.0)
                self.optimizer.step()
                
                total_loss += loss.item()
                batch_count += 1
                
            avg_loss = total_loss / batch_count
            
            if has_val:
                self.model.eval()
                with torch.no_grad():
                    X_val_batch = X_val_tensor.to(self.device)
                    y_val_batch = y_val_tensor.to(self.device)
                    val_logits = self.model(X_val_batch)
                    val_acc = (val_logits.argmax(dim=1) == y_val_batch).float().mean().item()
                    
                    self.lr_scheduler.step(val_acc)
                    
                    if verbose and (epoch + 1) % 5 == 0:
                        print(f"Epoch {epoch+1}/{epochs} | Loss: {avg_loss:.4f} | Val Acc: {val_acc:.4f}")
                        
                    if val_acc > best_val_acc:
                        best_val_acc = val_acc
                        patience_counter = 0
                    else:
                        patience_counter += 1
                        if patience_counter >= 10:
                            if verbose: print(f"Early stopping at epoch {epoch+1}")
                            break
            elif verbose and (epoch + 1) % 5 == 0:
                print(f"Epoch {epoch+1}/{epochs} | Loss: {avg_loss:.4f}")

    def predict(self, X_seq_df):
        """
        Predicts using a dataframe representing the sequence.
        X_seq_df shape: (seq_len, num_features)
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before prediction.")
            
        if len(X_seq_df) < self.seq_len:
            raise ValueError(f"Expected seq_len >= {self.seq_len}, got {len(X_seq_df)}")
            
        # Optional: clip to exactly seq_len if a longer df is passed
        df_window = X_seq_df.iloc[-self.seq_len:]
        
        # Scale
        X_scaled = self.scaler.transform(df_window.values)
        X_tensor = torch.tensor(X_scaled, dtype=torch.float32).unsqueeze(0).to(self.device) # shape: (1, seq_len, num_features)
        
        self.model.eval()
        with torch.no_grad():
            logits, attentions = self.model(X_tensor, return_attention=True)
            probabilities = torch.softmax(logits, dim=1).cpu().numpy()[0]
            
        return probabilities, attentions

    def save(self, model_path):
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.model.state_dict(), model_path)
        
        scaler_path = model_path.replace('.pth', '_scaler.pkl')
        metadata_path = model_path.replace('.pth', '_metadata.pkl')
        
        joblib.dump(self.scaler, scaler_path)
        joblib.dump({
            'feature_cols': self.feature_cols,
            'is_fitted': self.is_fitted,
            'seq_len': self.seq_len
        }, metadata_path)
        
        print(f"[SEQ-TRANSFORMER] Model saved to {model_path}")

    def load(self, model_path):
        self.model.load_state_dict(torch.load(model_path, map_location=self.device, weights_only=True))
        
        scaler_path = model_path.replace('.pth', '_scaler.pkl')
        metadata_path = model_path.replace('.pth', '_metadata.pkl')
        
        if os.path.exists(scaler_path):
            self.scaler = joblib.load(scaler_path)
            
        if os.path.exists(metadata_path):
            metadata = joblib.load(metadata_path)
            self.feature_cols = metadata.get('feature_cols')
            self.is_fitted = metadata.get('is_fitted', False)
            self.seq_len = metadata.get('seq_len', self.seq_len)
            
        self.model.eval()
        print(f"[SEQ-TRANSFORMER] Model loaded from {model_path}")

def load_sequence_transformer(model_path, device='cpu'):
    """Utility to load an existing Sequence Transformer model dynamically."""
    state_dict = torch.load(model_path, map_location=device, weights_only=True)
    
    # Infer architecture dimensions
    embedder_weight = state_dict['feature_embedder.0.weight']
    embed_dim = embedder_weight.shape[0]
    num_features = embedder_weight.shape[1]
    
    predictor = SequenceTransformerPredictor(
        input_features=num_features,
        embed_dim=embed_dim,
        device=device
    )
    predictor.load(model_path)
    return predictor
