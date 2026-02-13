import torch
import sys
import os
import numpy as np

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from strategy.lag_llama_predictor import LagLlamaPredictor

def verify_lag_llama():
    print("--- Verifying Lag-Llama Predictor ---")
    
    # 1. Initialize Predictor
    try:
        print("Initializing LagLlamaPredictor...")
        predictor = LagLlamaPredictor(device="cpu") # Force CPU for simple verification
        print("Initialization Successful.")
    except Exception as e:
        print(f"FAIL: Initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return

    # 2. Generate Dummy Data
    # Shape: (batch_size, context_length)
    batch_size = 1
    context_length = 60
    
    # Create valid time series data (e.g., sine wave with trend)
    x = np.linspace(0, 10, context_length)
    y = np.sin(x) + 0.1 * x
    
    tensor_input = torch.tensor(y, dtype=torch.float32).unsqueeze(0) # (1, 60)
    print(f"Input Shape: {tensor_input.shape}")
    
    # 3. Predict
    try:
        print("Running Prediction...")
        forecast = predictor.predict(tensor_input, prediction_length=20)
        print("Prediction Complete.")
        
        print(f"Output Shape: {forecast.shape}")
        print(f"Output Values: {forecast}")
        
        if torch.isnan(forecast).any():
            print("FAIL: Prediction contains NaNs.")
        else:
            print("SUCCESS: Prediction contains valid numbers.")
            
    except Exception as e:
        print(f"FAIL: Prediction failed: {e}")
        import traceback
        traceback.print_exc()
        return

    print("--- Verification Complete ---")

if __name__ == "__main__":
    verify_lag_llama()
