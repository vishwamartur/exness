import torch
import pandas as pd
import numpy as np

# We assume chronos is installed. If not, this module will fail to import 'chronos'
# To make it robust, we wrap imports.

try:
    from chronos import ChronosPipeline
    CHRONOS_INSTALLED = True
except ImportError:
    CHRONOS_INSTALLED = False
    print("Warning: 'chronos' not installed. Please install via: pip install git+https://github.com/amazon-science/chronos-forecasting.git")

class HFPredictor:
    def __init__(self, model_name="amazon/chronos-t5-tiny"):
        if not CHRONOS_INSTALLED:
            raise ImportError("Chronos library not found.")
            
        print(f"Loading HF Model: {model_name}...")
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda":
            print(f"HF Predictor using acceleration: {torch.cuda.get_device_name(0)}")
        else:
            print(f"HF Predictor using device: {self.device}")
        
        self.pipeline = ChronosPipeline.from_pretrained(
            model_name,
            device_map=self.device,
            torch_dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
        )
        print("Chronos Pipeline loaded.")

    def predict(self, context_tensor, prediction_length=12):
        """
        context_tensor: torch.Tensor of shape (batch_size, context_length)
        """
        # Ensure tensor is on correct device
        # The pipeline handles device placement usually, but context needs to be compatible
        # if passed as tensor.
        
        # Predict
        forecast = self.pipeline.predict(
            context_tensor,
            prediction_length=prediction_length,
            num_samples=20,
        )
        # forecast shape: (batch_size, num_samples, prediction_length)
        
        # Return median path
        median = torch.quantile(forecast, 0.5, dim=1)
        return median
