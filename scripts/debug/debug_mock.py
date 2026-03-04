import sys
import os
import pickle
import torch

# Add vendor path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor", "lag-llama"))

# Mock
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
    
    print("Mocking applied.")
    print(f"data.augmentations.augmentations: {sys.modules['data.augmentations.augmentations']}")
    print(f"Has ApplyAugmentations: {hasattr(sys.modules['data.augmentations.augmentations'], 'ApplyAugmentations')}")
    
except ImportError as e:
    print(f"Import Error: {e}")

# Test unpickling a dummy reference
# We can't easily generate a pickle that refers to 'data...' without creating a fake module first?
# Actually, we can assume if attribution works, unpickling works.

# Try to load the checkpoint
from huggingface_hub import hf_hub_download
try:
    ckpt_path = hf_hub_download(repo_id="time-series-foundation-models/Lag-Llama", filename="lag-llama.ckpt")
    print(f"Loading {ckpt_path}...")
    torch.load(ckpt_path, map_location="cpu", weights_only=False) # MUST BE FALSE for unpickling custom classes
    print("SUCCESS: Checkpoint loaded!")
except Exception as e:
    print(f"FAILURE: {e}")
