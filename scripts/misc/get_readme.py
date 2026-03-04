from huggingface_hub import hf_hub_download

def get_readme():
    repo_id = "time-series-foundation-models/Lag-Llama"
    print(f"Downloading README.md from {repo_id}...")
    try:
        path = hf_hub_download(repo_id=repo_id, filename="README.md")
        with open(path, "r", encoding="utf-8") as f:
            print(f.read())
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    get_readme()
