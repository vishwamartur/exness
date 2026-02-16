"""
Gemini Advisor (REST API Version)
Uses Google Gemini 1.5 via REST API to avoid library conflicts.
"""
import os
import requests
import json
from config import settings

class GeminiAdvisor:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            print("[GEMINI] Warning: GEMINI_API_KEY not found in .env")
            return
            
        # Using gemini-3-pro-image-preview (Advanced/Experimental Model)
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent?key={self.api_key}"
        try:
            # Simple health check (dummy generation)
            # data = {"contents": [{"parts": [{"text": "Hello"}]}]}
            # response = requests.post(self.url, json=data)
            # if response.status_code == 200:
            print("[GEMINI] REST Client initialized (gemini-1.5-flash).")
        except Exception as e:
            print(f"[GEMINI] failed to init: {e}")

    def analyze_market(self, symbol, timeframe, indicators):
        """
        Sends technical data to Gemini for analysis via REST API.
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
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 100
            }
        }
        
        # Try Primary Model (Gemini 3 Pro)
        try:
            response = requests.post(self.url, json=payload, timeout=10)
            
            # If failed (e.g. 429 Quota, 404 Not Found), try fallback
            if response.status_code != 200:
                print(f"[GEMINI] Primary model failed ({response.status_code}). Switching to Fallback (Gemini 2.5)...")
                # Fallback to gemini-2.5-computer-use-preview-10-2025
                fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-computer-use-preview-10-2025:generateContent?key={self.api_key}"
                response = requests.post(fallback_url, json=payload, timeout=10)

            if response.status_code != 200:
                print(f"[GEMINI] API Error {response.status_code}: {response.text}")
                return "NEUTRAL", 0, f"HTTP {response.status_code}"
                
            result = response.json()
            # Parse Gemini response structure
            try:
                # Handle possible varying structure
                candidates = result.get('candidates', [])
                if not candidates:
                    return "NEUTRAL", 0, "No Candidates"
                    
                text = candidates[0]['content']['parts'][0]['text']
                data = text.strip().split('|')
                if len(data) == 3:
                    return data[0].strip(), int(data[1].strip()), data[2].strip()
                return "NEUTRAL", 0, "Format Error"
            except KeyError:
                return "NEUTRAL", 0, "Parse Error"
                
        except Exception as e:
            print(f"[GEMINI] Request Failed: {e}")
            return "NEUTRAL", 0, str(e)
