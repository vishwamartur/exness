from huggingface_hub import hf_hub_download
import os

def check_sizes():
    repo_id = "time-series-foundation-models/Lag-Llama"
    print(f"Checking sizes in {repo_id}...")
    try:
        ckpt_path = hf_hub_download(repo_id=repo_id, filename="lag-llama.ckpt")
        print(f"lag-llama.ckpt size: {os.path.getsize(ckpt_path) / (1024*1024):.2f} MB")
        
        sf_path = hf_hub_download(repo_id=repo_id, filename="model.safetensors")
        print(f"model.safetensors size: {os.path.getsize(sf_path) / (1024*1024):.2f} MB")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_sizes()
