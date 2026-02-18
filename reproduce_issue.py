
import pandas as pd
import sys
import os

# Reconfigure stdout for utf-8 (Windows fix)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# Mock settings
from config import settings

# Mock QuantAgent
from analysis.quant_agent import QuantAgent

def main():
    print("Testing QuantAgent output for non-ASCII characters...")
    
    agent = QuantAgent()
    
    # Create dummy data
    data = {
        'open': [1.0] * 100,
        'high': [1.1] * 100,
        'low': [0.9] * 100,
        'close': [1.05] * 100,
        'tick_volume': [100] * 100,
        'spread': [1] * 100,
        'real_volume': [100] * 100,
        'time': range(100)
    }
    df = pd.DataFrame(data)
    
    data_dict = {
        settings.TIMEFRAME: df,
        'H1': df,
        'H4': df
    }
    
    try:
        res = agent.analyze("TEST", data_dict)
        if res:
            details = res['details']
            det_str = ' '.join(f"{k}:{v}" for k,v in details.items())
            print(f"Details string: {det_str}")
            
            # Check for non-ascii
            try:
                det_str.encode('ascii')
                print("String is ASCII safe.")
            except UnicodeEncodeError:
                print("String contains NON-ASCII characters!")
                for char in det_str:
                    if ord(char) > 127:
                        print(f"Non-ASCII char: {char} (U+{ord(char):04X})")
        else:
            print("Analyze returned None (might be due to lack of indicators or models)")
            
    except Exception as e:
        print(f"Error during analysis: {e}")

if __name__ == "__main__":
    main()
