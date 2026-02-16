
"""
Mistral Advisor (Async)
Uses Mistral AI via API to provide qualitative market analysis.
"""
import os
import aiohttp
import asyncio
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

    async def analyze_market(self, symbol, timeframe, indicators):
        """
        Sends technical data to Mistral for analysis (Async).
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
        
        return await self._send_request(payload, default_response=("NEUTRAL", 0, "Request Failed"))

    async def send_prompt(self, system_prompt, user_prompt):
        """Generic method to send prompts to Mistral (Async)."""
        if not self.api_key:
            return None
            
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 500
        }
        
        return await self._send_request(payload, parse_pipe=False)

    async def _send_request(self, payload, default_response=None, parse_pipe=True):
        """Helper to handle aiohttp requests."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.url, json=payload, headers=headers, timeout=15) as response:
                    if response.status != 200:
                        text = await response.text()
                        print(f"[MISTRAL] API Error {response.status}: {text}")
                        return default_response if default_response else None
                    
                    result = await response.json()
                    
                    if not parse_pipe:
                        return result['choices'][0]['message']['content']
                        
                    # Parse Pipe Format
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
            return default_response if default_response else None
