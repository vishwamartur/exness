import torch
from huggingface_hub import hf_hub_download
import sys
import os

# Add vendor path BEFORE imports
sys.path.append(os.path.join(os.path.dirname(__file__), "vendor", "lag-llama"))

# Need to mock data module if pickling fails due to 'data'
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
    print(f"Import error during mocking: {e}")

from lag_llama.gluon.lightning_module import LagLlamaLightningModule

def inspect_config():
    repo_id = "time-series-foundation-models/Lag-Llama"
    print(f"Inspecting checkpoint from {repo_id}...")
    try:
        ckpt_path = hf_hub_download(repo_id=repo_id, filename="lag-llama.ckpt")
        print(f"Loading checkpoint from {ckpt_path}...")
        
        # Load with map_location
        ckpt = torch.load(ckpt_path, map_location="cpu")
        
        # Check for hyperparameters in PL checkpoint
        if "hyper_parameters" in ckpt:
            print("Found hyper_parameters in checkpoint:")
            hparams = ckpt["hyper_parameters"]
            keys_to_print = ["n_layer", "n_head", "n_embd_per_head", "input_size", "lags_seq"]
            for k in keys_to_print:
                if k in hparams:
                    print(f"{k}: {hparams[k]}")
                elif "model_kwargs" in hparams and k in hparams["model_kwargs"]:
                    print(f"{k}: {hparams['model_kwargs'][k]}")
                    
        # Check keys to count layers if hparams missing
        if "state_dict" in ckpt:
            keys = list(ckpt["state_dict"].keys())
            print(f"Found {len(keys)} keys in state_dict.")
            
            # Count layers
            layers = set()
            for k in keys:
                if "model.transformer.h." in k:
                    # format: model.transformer.h.0.attn...
                    parts = k.split(".")
                    try:
                        idx = parts.index("h")
                        layer_num = int(parts[idx+1])
                        layers.add(layer_num)
                    except:
                        pass
            print(f"Estimated n_layer from keys: {len(layers)}")
            
            # Check weight shapes for dims
            if "model.transformer.wte.weight" in ckpt["state_dict"]:
                wte_shape = ckpt["state_dict"]["model.transformer.wte.weight"].shape
                print(f"wte.weight shape: {wte_shape}")
                
    except Exception as e:
        print(f"Could not inspect checkpoint: {e}")

if __name__ == "__main__":
    inspect_config()
