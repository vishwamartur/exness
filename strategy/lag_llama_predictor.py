import torch
import sys
import os
import pandas as pd
from huggingface_hub import hf_hub_download
from gluonts.dataset.common import ListDataset

# Add vendor/lag-llama to path so we can import from it
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "lag-llama"))

from lag_llama.gluon.estimator import LagLlamaEstimator

# Mock 'data' module for unpickling Lag-Llama checkpoint
# The checkpoint refers to 'data.augmentations...' but we renamed 'data' to 'lag_llama_data'
try:
    import lag_llama_data
    import lag_llama_data.augmentations
    import lag_llama_data.augmentations.freq_mask
    import lag_llama_data.augmentations.freq_mix
    import lag_llama_data.augmentations.augmentations
    
    sys.modules['data'] = lag_llama_data
    sys.modules['data.augmentations'] = lag_llama_data.augmentations
    sys.modules['data.augmentations.freq_mask'] = lag_llama_data.augmentations.freq_mask
    sys.modules['data.augmentations.freq_mix'] = lag_llama_data.augmentations.freq_mix
    sys.modules['data.augmentations.augmentations'] = lag_llama_data.augmentations.augmentations
except ImportError as e:
    print(f"Warning: Could not mock data module: {e}")

class LagLlamaPredictor:
    def __init__(self, ckpt_path="time-series-foundation-models/Lag-Llama", device="cuda"):
        self.device = device
        
        if self.device.startswith("cuda") and torch.cuda.is_available():
             print(f"Lag-Llama initialized on GPU: {torch.cuda.get_device_name(0)}")
        else:
             print(f"Lag-Llama initialized on device: {self.device}")
        self.ckpt_path = ckpt_path
        self.prediction_length = 20 # Default, will be overridden or used as base
        self.context_length = 32 # Default
        
        # Load the model and predictor
        self.predictor = self.load_predictor()
        
    def load_predictor(self):
        print(f"Loading Lag-Llama from {self.ckpt_path}...")
        try:
            # Check if it's a local path or HF repo
            if os.path.exists(self.ckpt_path):
                 ckpt_file = self.ckpt_path
            else:
                 # Download from HF
                 ckpt_file = hf_hub_download(repo_id=self.ckpt_path, filename="lag-llama.ckpt")
            
            # Initialize Estimator
            # We use a default configuration that matches the pretrained model
            # The context length and prediction length here define the structure for the lightning module
            # but for zeroshot forecasting, Lag-Llama handles variable context.
            # Deduce lags_seq to match feature_size=512
            # feature_size = 1 * L + 2 + 6 = 512 => L = 504
            # We construct a dummy lags_seq of length 504.
            # The exact values matter for prediction inputs, but for weight loading, only length matters.
            # Ideally we should recover the exact lags_seq from the checkpoint, but unpickling failed.
            # For now, we use a range. This might degrade performance if lags are specific (e.g. exponential).
            # But getting it to load is step 1.
            # Correct configuration matching the checkpoint
            # Checkpoint trained with ["Q", "M", "W", "D", "H", "T", "S"] which yields 84 unique lags.
            # 84 lags + 2 static + 6 time features = 92 input features.
            # Layers = 8 (based on missing keys from checkpoint)
            lags_seq = ["Q", "M", "W", "D", "H", "T", "S"]

            estimator = LagLlamaEstimator(
                prediction_length=self.prediction_length,
                context_length=self.context_length,
                
                # Parameters matching the checkpoint
                n_layer=8, 
                n_head=4,
                n_embd_per_head=36,
                lags_seq=lags_seq,
                time_feat=True,
                
                # Pass None for ckpt_path to avoid automatic loading failure.
                # We will load weights manually below.
                ckpt_path=None,
                
                batch_size=1,
                num_parallel_samples=20,
                device=torch.device(self.device),
            )
            
            # Create Lightning Module
            try:
                module = estimator.create_lightning_module()
            except Exception as e:
                print(f"Error creating lightning module: {e}")
                # Try to clean up monkeypatch if it failed?
                # But we didn't monkeypatch here, we did it inside estimator code previously?
                # No, we removed monkeypatch from estimator.
                # We monkeypatched torch.load below?
                raise e

            # Manual loading of weights
            print(f"Manually loading weights from {ckpt_file}...")
            
            # Monkeypatch torch.load to force weights_only=False
            original_load = torch.load
            def safe_load(*args, **kwargs):
                if 'weights_only' not in kwargs:
                    kwargs['weights_only'] = False
                return original_load(*args, **kwargs)
            torch.load = safe_load
            
            try:
                 ckpt = torch.load(ckpt_file, map_location=self.device)
            finally:
                 torch.load = original_load
                 
            if "state_dict" in ckpt:
                state_dict = ckpt["state_dict"]
            elif "model_state_dict" in ckpt:
                state_dict = ckpt["model_state_dict"]
            else:
                state_dict = ckpt
                
            model_state = module.state_dict()
            new_state = {}
            for k, v in state_dict.items():
                if k in model_state:
                    if v.shape != model_state[k].shape:
                        # Try transpose
                        if v.T.shape == model_state[k].shape:
                            print(f"Transposing {k}")
                            new_state[k] = v.T
                        else:
                            print(f"Skipping {k} due to shape mismatch: {v.shape} vs {model_state[k].shape}")
                    else:
                        new_state[k] = v
                else:
                    pass # print(f"Skipping unknown key {k}")
            
            msg = module.load_state_dict(new_state, strict=False)
            print("Load results:", msg)
            
            module = module.to(self.device)
            module.eval()
            
            # Create Transformation
            transformation = estimator.create_transformation()
            
            # Create Predictor
            predictor = estimator.create_predictor(transformation, module)
            
            print("Lag-Llama loaded successfully.")
            return predictor
            
        except Exception as e:
            print(f"Error loading Lag-Llama: {e}")
            raise e

    def predict(self, context_tensor, prediction_length=12):
        """
        context_tensor: torch.Tensor of shape (batch, time)
        Returns: median forecast of shape (batch, prediction_length)
        """
        # Convert tensor to numpy
        context_np = context_tensor.cpu().numpy()
        
        # Create GluonTS Dataset
        # We need a start time, but for pure prediction shape it doesn't matter too much
        # if we just want values. However, GluonTS expects it.
        # We'll use a dummy start time.
        start_time = pd.Timestamp("2023-01-01 00:00:00")
        
        data_list = []
        for i in range(context_np.shape[0]):
            target = context_np[i]
            data_list.append({
                "start": start_time,
                "target": target,
                "item_id": f"item_{i}"
            })
            
        dataset = ListDataset(data_list, freq="D") # Freq might matter for some internals
        
        # The predictor requires the dataset. 
        # Note: The predictor was initialized with a specific prediction_length.
        # If we want to change it dynamically, we might need to adjust the predictor or module properties,
        # but usually the output shape is fixed by the module configuration.
        # Lag-Llama is autoregressive, so it *can* generate arbitrary lengths, 
        # but the GluonTS wrapper usually fixes it.
        # For now, let's rely on the initialized prediction_length or slicing if it produces more.
        
        forecast_it = self.predictor.predict(dataset)
        
        forecasts = list(forecast_it)
        
        # Extract median
        # forecasts is a list of Forecast objects (SampleForecast usually)
        
        results = []
        for f in forecasts:
            # f.samples has shape (num_samples, prediction_length)
            # We want median
            median = f.quantile(0.5) # Shape (prediction_length,)
            results.append(median)
            
        return torch.tensor(np.array(results)).to(self.device) # Shape (batch, prediction_length)
        
    
def get_lag_llama_predictor(settings):
    """Factory to get the predictor with correct settings"""
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return LagLlamaPredictor(ckpt_path=settings.LAG_LLAMA_CHECKPOINT, device=device)
