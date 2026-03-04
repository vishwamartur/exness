"""Quick test to verify Gemini API connectivity and response format."""
import sys, os
sys.path.append(os.path.dirname(__file__))

# Load .env
from dotenv import load_dotenv
load_dotenv()

import asyncio
from analysis.gemini_news_analyzer import get_gemini_analyzer

async def test():
    gemini = get_gemini_analyzer()
    
    if not gemini.is_available():
        print("FAIL: Gemini not available. Check API key.")
        return
    
    print("Testing EURUSD...")
    result = await gemini.analyze("EURUSD")
    print(f"  Score: {result['score']:+.3f}")
    print(f"  Confidence: {result['confidence']:.0%}")
    print(f"  Bias: {result['direction_bias']}")
    print(f"  Risk: {result['risk_level']}")
    print(f"  Events: {result['key_events']}")
    print(f"  Reasoning: {result['reasoning']}")
    print()
    
    print("Testing BTCUSD...")
    result2 = await gemini.analyze("BTCUSD")
    print(f"  Score: {result2['score']:+.3f}")
    print(f"  Confidence: {result2['confidence']:.0%}")
    print(f"  Bias: {result2['direction_bias']}")
    print(f"  Events: {result2['key_events']}")
    print(f"  Reasoning: {result2['reasoning']}")
    print()

    # Test trade blocking
    print("Testing trade blocking logic...")
    block, reason = gemini.should_block_trade("BUY", result)
    print(f"  BUY EURUSD blocked? {block} | {reason}")
    block2, reason2 = gemini.should_block_trade("SELL", result2)
    print(f"  SELL BTCUSD blocked? {block2} | {reason2}")

    print("\n=== GEMINI TEST COMPLETE ===")

asyncio.run(test())
