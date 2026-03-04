import os

FILE_PATH = r'f:\mt5\strategy\institutional_strategy.py'

replacements = {
    '─': '-',
    '═': '=',
    '│': '|',
    '┌': '+',
    '┐': '+',
    '└': '+',
    '┘': '+',
    '✅': '[OK]',
    '❌': '[X]',
    '✓': '[YES]',
    '✗': '[NO]',
    '—': '-', # em-dash
    '’': "'",
    '“': '"',
    '”': '"'
}

try:
    with open(FILE_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for old, new in replacements.items():
        new_content = new_content.replace(old, new)
        
    # Also clean any other non-ascii just in case
    # valid_chars = set(chr(i) for i in range(128))
    # new_content = ''.join(c if c in valid_chars else '?' for c in new_content)
    
    if new_content != content:
        with open(FILE_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Fixed {len(replacements)} types of unicode characters in {FILE_PATH}")
    else:
        print("No changes needed.")
        
except Exception as e:
    print(f"Error: {e}")
