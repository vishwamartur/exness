import torch
import torch.nn as nn
import torch.nn.functional as F

class Attention(nn.Module):
    def __init__(self, hidden_dim):
        super(Attention, self).__init__()
        self.hidden_dim = hidden_dim
        self.linear = nn.Linear(hidden_dim, 1)

    def forward(self, lstm_output):
        # lstm_output: [batch_size, seq_len, hidden_dim * num_directions]
        # We assume bidirectional=True, so hidden_dim is effectively doubled in input if passed directly,
        # but here we expect the user to pass the correct dimension size.
        
        # weights: [batch_size, seq_len, 1]
        weights = torch.tanh(self.linear(lstm_output))
        # attention_weights: [batch_size, seq_len, 1]
        attention_weights = F.softmax(weights, dim=1)
        
        # context_vector: [batch_size, hidden_dim * num_directions]
        # Sum over sequence length weighted by attention
        context_vector = torch.sum(attention_weights * lstm_output, dim=1)
        
        return context_vector, attention_weights

class BiLSTMWithAttention(nn.Module):
    def __init__(self, input_size, hidden_size, num_layers=1, dropout=0.2, device='cpu'):
        super(BiLSTMWithAttention, self).__init__()
        self.hidden_size = hidden_size
        self.num_layers = num_layers
        self.device = device
        
        # Bidirectional LSTM
        self.lstm = nn.LSTM(
            input_size, 
            hidden_size, 
            num_layers=num_layers, 
            batch_first=True, 
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0
        )
        
        # Attention Layer
        # Input to attention is hidden_size * 2 (because bidirectional)
        self.attention = Attention(hidden_size * 2)
        
        # Fully Connected Layer
        self.fc = nn.Linear(hidden_size * 2, 1) # Output 1 value (price/return)
        
    def forward(self, x):
        # x: [batch_size, seq_len, input_size]
        
        # Initialize hidden state and cell state
        h0 = torch.zeros(self.num_layers * 2, x.size(0), self.hidden_size).to(self.device)
        c0 = torch.zeros(self.num_layers * 2, x.size(0), self.hidden_size).to(self.device)
        
        # Forward propagate LSTM
        # out: [batch_size, seq_len, hidden_size * 2]
        out, _ = self.lstm(x, (h0, c0))
        
        # Apply Attention
        # context: [batch_size, hidden_size * 2]
        context, attn_weights = self.attention(out)
        
        # Final Prediction
        out = self.fc(context)
        
        return out
