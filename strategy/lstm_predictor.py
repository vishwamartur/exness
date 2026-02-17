import torch
import numpy as np
import pandas as pd
import joblib
import os
from sklearn.preprocessing import MinMaxScaler
from strategy.lstm_model import BiLSTMWithAttention

class LSTMPredictor:
    def __init__(self, model_path, scaler_path, device=None, sequence_length=60, hidden_size=64, num_layers=2):
        if device is None:
            self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        else:
            self.device = device
            
        if self.device == 'cuda' and torch.cuda.is_available():
            print(f"LSTM initialized on GPU: {torch.cuda.get_device_name(0)}")
        else:
            print(f"LSTM initialized on device: {self.device}")

        self.sequence_length = sequence_length
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        
        self.model_path = model_path
        self.feature_scaler_path = scaler_path
        self.cols_path = scaler_path.replace('scaler.pkl', 'cols.pkl')
        self.target_scaler_path = scaler_path.replace('scaler.pkl', 'target_scaler.pkl')
        
        self.model = None
        self.feature_scaler = None
        self.target_scaler = None
        self.feature_cols = None
        
        self.load_artifacts()
        
    def load_artifacts(self):
        """Loads model and scalers."""
        try:
            # Scalers
            if os.path.exists(self.feature_scaler_path):
                self.feature_scaler = joblib.load(self.feature_scaler_path)
            else:
                print(f"Warning: Feature Scaler not found at {self.feature_scaler_path}")
                
            if os.path.exists(self.target_scaler_path):
                self.target_scaler = joblib.load(self.target_scaler_path)
            else:
                # If target scaler doesn't exist, maybe it's same as feature scaler (unlikely for multi-feature)
                # or not saved. For now, warn.
                print(f"Warning: Target Scaler not found at {self.target_scaler_path}")

            if os.path.exists(self.cols_path):
                self.feature_cols = joblib.load(self.cols_path)
            else:
                print(f"Warning: Feature Columns not found at {self.cols_path}")

            # Model
            if os.path.exists(self.model_path):
                # Calculate input size from scaler if available
                input_size = self.feature_scaler.n_features_in_ if self.feature_scaler else 1
                
                self.model = BiLSTMWithAttention(
                    input_size=input_size, 
                    hidden_size=self.hidden_size, 
                    num_layers=self.num_layers, 
                    device=self.device
                )
                self.model.load_state_dict(torch.load(self.model_path, map_location=self.device, weights_only=True))
                self.model.to(self.device)
                self.model.eval()
                print("LSTM Model loaded successfully.")
            else:
                print(f"Warning: LSTM Model not found at {self.model_path}")
                
        except Exception as e:
            print(f"Error loading LSTM artifacts: {e}")
            
    def preprocess(self, df):
        """
        Preprocesses dataframe into tensor for inference.
        Assumes df contains correct feature columns in correct order.
        """
        if not self.feature_scaler:
            raise ValueError("Feature scaler not loaded.")
            
        # Filter columns
        if self.feature_cols:
             try:
                 data_to_scale = df[self.feature_cols]
             except KeyError as e:
                 # Fallback code if cols missing, or maybe df is already numpy?
                 # Assuming df is DataFrame as per docstring
                 missing = [c for c in self.feature_cols if c not in df.columns]
                 raise ValueError(f"Missing feature columns: {missing}")
        else:
             # Fallback logic if cols not saved: try dropping known non-features
             drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume', 'target']
             cols_to_use = [c for c in df.columns if c not in drop_cols]
             data_to_scale = df[cols_to_use]
        
        # Scale
        # Convert to numpy to avoid feature name mismatch warning if scaler was fitted on numpy
        scaled_data = self.feature_scaler.transform(data_to_scale.values if hasattr(data_to_scale, 'values') else data_to_scale)
        
        # Create Sequence
        # We need the last 'sequence_length' rows
        if len(scaled_data) < self.sequence_length:
            raise ValueError(f"Not enough data. Needed {self.sequence_length}, got {len(scaled_data)}")
            
        seq = scaled_data[-self.sequence_length:]
        
        return torch.tensor(seq, dtype=torch.float32).unsqueeze(0).to(self.device) # [1, seq_len, features]

    def predict(self, df):
        """
        Predicts the next value based on the input dataframe.
        """
        if not self.model or not self.feature_scaler:
            return None
            
        try:
            input_tensor = self.preprocess(df)
            
            with torch.no_grad():
                prediction = self.model(input_tensor) # [1, 1]
                
            prediction_val = prediction.cpu().numpy()[0][0]
            
            # Inverse transform target
            if self.target_scaler:
                # Reshape for scalar inverse
                final_pred = self.target_scaler.inverse_transform([[prediction_val]])[0][0]
            else:
                final_pred = prediction_val
                
            return final_pred
            
        except Exception as e:
            print(f"LSTM Prediction Error: {e}")
            return None
