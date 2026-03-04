import asyncio
import os
from analysis.llm_advisor import get_advisor
from dotenv import load_dotenv

async def test_llm():
    # Load .env variables
    load_dotenv()
    
    print("="*50)
    print("  LLM ADVISOR DIAGNOSTICS")
    print("="*50)
    
    advisor = get_advisor()
    print(f"\n[INFO] Active LLM Backend: {advisor.__class__.__name__}")
    
    if advisor.__class__.__name__ == "DummyAdvisor":
        print("[ERROR] Neither Groq nor Mistral API keys were found in the environment.")
        return

    # Mock technical data for testing the structured output formatting
    mock_indicators = {
        'close': 1.1050,
        'adx': 45.0,
        'rsi': 65.0,
        'regime': 'TRENDING',
        'ml_prob': 0.88,
        'h4_trend': 1
    }
    
    print("\n[1] Testing `analyze_market` method (Structured Output)...")
    try:
        sentiment, conf, reason = await advisor.analyze_market("EURUSD", "M15", mock_indicators)
        print(f"  Sentiment  | {sentiment}")
        print(f"  Confidence | {conf}")
        print(f"  Reason     | {reason}")
    except Exception as e:
        print(f"  ❌ Error: {e}")
        
    print("\n[2] Testing `send_prompt` method (Raw Output)...")
    try:
        system = "You are a helpful AI logic checker."
        user = "Evaluate this statement: 2 + 2 = 5. Respond with exactly the word FALSE."
        response = await advisor.send_prompt(system, user)
        print(f"  Response   | {response.strip()}")
    except Exception as e:
        print(f"  ❌ Error: {e}")

    print("\n" + "="*50)
    print("  DIAGNOSTICS COMPLETE")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(test_llm())
