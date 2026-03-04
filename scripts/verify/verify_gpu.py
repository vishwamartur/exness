import torch
import sys

def check_gpu():
    print(f"Python version: {sys.version}")
    print(f"PyTorch version: {torch.__version__}")
    
    cuda_available = torch.cuda.is_available()
    print(f"CUDA available: {cuda_available}")
    
    if cuda_available:
        current_device = torch.cuda.current_device()
        print(f"Current CUDA device index: {current_device}")
        print(f"Device name: {torch.cuda.get_device_name(current_device)}")
        print(f"CUDA capability: {torch.cuda.get_device_capability(current_device)}")
        
        # Test tensor operation
        try:
            x = torch.tensor([1.0, 2.0]).cuda()
            y = torch.tensor([3.0, 4.0]).cuda()
            z = x + y
            print(f"Test tensor operation succeeded: {z}")
        except Exception as e:
            print(f"Test tensor operation failed: {e}")
    else:
        print("CUDA is NOT available. PyTorch is using CPU.")
        print("Please ensure you have installed PyTorch with CUDA support.")
        print("Visit https://pytorch.org/get-started/locally/ for installation commands.")

if __name__ == "__main__":
    check_gpu()
