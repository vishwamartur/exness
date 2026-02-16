"""
Mistral Advisor
Uses Mistral AI via API to provide qualitative market analysis.
"""
import os
import requests
import json
from config import settings

class MistralAdvisor:
    def __init__(self):
        self.api_key = os.getenv("MISTRAL_API_KEY")
        if not self.api_key:
            print("[MISTRAL] Warning: MISTRAL_API_KEY not found in .env")
            return
            
        self.url = "https://api.mistral.ai/v1/chat/completions"
        self.model = "mistral-small-latest"  # Cost-effective and smart
        
        try:
            print(f"[MISTRAL] Advisor initialized ({self.model}).")
        except Exception as e:
            print(f"[MISTRAL] failed to init: {e}")

    def analyze_market(self, symbol, timeframe, indicators):
        """
        Sends technical data to Mistral for analysis.
        """
        if not self.api_key:
            return "NEUTRAL", 0, "No API Key"

        prompt = f"""
        Act as a senior institutional trader. Analyze this setup for {symbol} ({timeframe}).
        
        Technical Data:
        - Price: {indicators.get('close')}
        - Trend (ADX): {indicators.get('adx')}
        - RSI: {indicators.get('rsi')}
        - Regime: {indicators.get('regime')}
        - ML Confidence: {indicators.get('ml_prob', 0)*100:.1f}%
        - H4 Trend: {indicators.get('h4_trend')}
        
        Task:
        1. Determine if this is a high-probability trade.
        2. Provide a sentiment (BULLISH/BEARISH/NEUTRAL).
        3. Rate confidence (0-100).
        4. Give a 1-sentence reason.
        
        Format response strictly as:
        SENTIMENT | CONFIDENCE | REASON
        Example: BULLISH | 85 | Strong uptrend confirmed by ADX and RSI not overbought.
        """
        
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 100
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.post(self.url, json=payload, headers=headers, timeout=10)
            if response.status_code != 200:
                print(f"[MISTRAL] API Error {response.status_code}: {response.text}")
                return "NEUTRAL", 0, f"HTTP {response.status_code}"
                
            result = response.json()
            # Parse Mistral response structure
            try:
                content = result['choices'][0]['message']['content']
                data = content.strip().split('|')
                if len(data) == 3:
                    return data[0].strip(), int(data[1].strip()), data[2].strip()
                return "NEUTRAL", 0, "Format Error"
            except (KeyError, IndexError):
                return "NEUTRAL", 0, "Parse Error"
                
        except Exception as e:
            print(f"[MISTRAL] Request Failed: {e}")
            return "NEUTRAL", 0, str(e)
