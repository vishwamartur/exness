try:
    with open('verify_output.txt', 'r', encoding='utf-16') as f:
        content = f.read()
except Exception:
    with open('verify_output.txt', 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
        
print("Content length:", len(content))
for line in content.split('\n'):
    if "size mismatch" in line or "Error" in line:
        print(line)
