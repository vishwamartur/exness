"""
Verify Sequence Transformer Implementation
==========================================

Runs a quick test on the Sequence Transformer implementation 
to ensure tensor shapes and forwarding passes work correctly.
"""

import torch
import numpy as np
import pandas as pd
import os
import sys

# Add project root
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from strategy.sequence_transformer import SequenceTransformerPredictor

def run_verification():
    print("="*60)
    print(" SEQUENCE TRANSFORMER VERIFICATION ")
    print("="*60)
    
    # 1. Create Dummy Sequence Data
    # Shape: (batch_size, seq_len, num_features)
    BATCH_SIZE = 16
    SEQ_LEN = 60
    NUM_FEATURES = 50
    NUM_SAMPLES = 100
    
    print(f"\n[1] Creating dummy data...")
    print(f"  Samples: {NUM_SAMPLES} | Seq Len: {SEQ_LEN} | Features: {NUM_FEATURES}")
    
    X_dummy = np.random.randn(NUM_SAMPLES, SEQ_LEN, NUM_FEATURES)
    y_dummy = np.random.randint(0, 2, size=(NUM_SAMPLES,))
    
    # 2. Initialize Predictor
    print(f"\n[2] Initializing SequenceTransformerPredictor...")
    predictor = SequenceTransformerPredictor(
        input_features=NUM_FEATURES,
        seq_len=SEQ_LEN,
        embed_dim=32,
        num_layers=2,
        num_heads=4,
        ffn_dim=64,
        device='cpu', # Force CPU for quick test
        lr=0.001
    )
    
    print("  Initialization Successful.")
    
    # 3. Test Fit (1 epoch)
    print(f"\n[3] Testing short fit (1 epoch)...")
    try:
        predictor.fit(X_dummy, y_dummy, epochs=1, batch_size=32, verbose=True)
        print("  Fit method completed successfully.")
    except Exception as e:
        print(f"  ❌ Fit method failed: {e}")
        return False

    # 4. Test Single Inference with Attention Extraction
    print(f"\n[4] Testing live inference & attention extraction...")
    try:
        # Create a single dataframe window like the live bot would
        df_window = pd.DataFrame(X_dummy[0], columns=[f"feat_{i}" for i in range(NUM_FEATURES)])
        
        probs, attentions = predictor.predict(df_window)
        
        print(f"  Prediction Probabilities: {probs} (shape: {probs.shape})")
        print(f"  Attention Layers extracted: {len(attentions)}")
        if len(attentions) > 0:
            print(f"  Attention Tensor [Layer 0] shape: {attentions[0].shape} -> (batch, num_heads, seq_len, seq_len)")
        print("  Inference method completed successfully.")
        
    except Exception as e:
        print(f"  ❌ Inference method failed: {e}")
        return False
        
    print("\n" + "="*60)
    print(" ✅ ALL SEQUENCE TRANSFORMER CHECKS PASSED!")
    print("="*60 + "\n")
    return True

if __name__ == "__main__":
    run_verification()
