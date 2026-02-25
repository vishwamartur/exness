
from analysis.mistral_advisor import MistralAdvisor
from config import settings

class ResearcherAgent:
    """
    The 'Researcher' Agent (Async).
    Responsibilities:
    1. Conduct detailed Bull/Bear debate based on data.
    2. Synthesize Quant + Analyst outputs.
    3. Provide final conviction score (0-100).
    """
    def __init__(self):
        self.advisor = MistralAdvisor()
        print("[AGENT] ResearcherAgent initialized.")

    async def conduct_research(self, symbol, quant_data, analyst_data):
        """
        Conducts a debate analysis (Async).
        Returns: {
            'action': 'BUY'|'SELL'|'HOLD',
            'confidence': int, # 0-100
            'reason': str
        }
        """
        # 1. Prepare Facts
        direction = quant_data.get('direction', 'NEUTRAL') if isinstance(quant_data, dict) else 'NEUTRAL'
        score = quant_data.get('score', 0) if isinstance(quant_data, dict) else 0
        ml_prob = quant_data.get('ml_prob', 0) if isinstance(quant_data, dict) else 0
        regime = analyst_data.get('regime', 'NORMAL') if isinstance(analyst_data, dict) else 'NORMAL'
        h4_trend = quant_data.get('h4_trend', 0) if isinstance(quant_data, dict) else 0
        
        # 0. Check Requisites
        if not self.advisor.api_key:
            # Fallback for users without LLM
            print("[RESEARCHER] No API Key. Falling back to Technical Confidence.")
            return {
                'action': direction,
                'confidence': 85 if score >= 4 else 75,
                'reason': f"Technical Strategy (Score {score})"
            }

        # 2. Construct Debate Prompt
        system_prompt = """
        You are a Senior Implementation Researcher at a top Hedge Fund.
        Your job is to DEBATE the trade setup provided.
        
        methodology:
        1. Bull Case: Listing all factors supporting a long position.
        2. Bear Case: Listing all factors supporting a short position.
        3. Weighting: Assign importance to factors (Trend > ML > Oscillators).
        4. Synthesis: Conclusion based on the weight of evidence.
        
        Output Format:
        ACTION | CONFIDENCE | REASON
        """
        
        # Ensure variables are defined to prevent undefined name errors
        safe_direction = str(direction) if direction is not None else 'NEUTRAL'
        safe_score = float(score) if score is not None else 0
        safe_ml_prob = float(ml_prob) if ml_prob is not None else 0
        safe_regime = str(regime) if regime is not None else 'NORMAL'
        safe_h4_trend = float(h4_trend) if h4_trend is not None else 0
        safe_details = str(quant_data.get('details', 'N/A')) if isinstance(quant_data, dict) else 'N/A'
        safe_rsi = float(quant_data['features'].get('rsi', 0)) if isinstance(quant_data.get('features', {}), dict) else 0
        safe_adx = float(quant_data['features'].get('adx', 0)) if isinstance(quant_data.get('features', {}), dict) else 0
        safe_close = float(quant_data['features'].get('close', 0)) if isinstance(quant_data.get('features', {}), dict) else 0
        
        user_prompt = f"""
        Analyze this trade for {symbol} ({settings.TIMEFRAME}):
        
        Proposed Action: {safe_direction} (Score: {safe_score}/6)
        
        Factors:
        - ML Confidence: {safe_ml_prob:.2f} (Random Forest/XGBoost)
        - Market Regime: {safe_regime}
        - H4 Trend: {safe_h4_trend}
        - Technical Details: {safe_details}
        - Indicators:
          RSI: {safe_rsi:.1f}
          ADX: {safe_adx:.1f}
          Close: {safe_close:.5f}
        
        Debate the Bull and Bear cases. Then provide the final conclusion.
        Output strictly as: ACTION | CONFIDENCE | REASON
        Example: BUY | 85 | Strong trend alignment with high ML probability.
        """
        
        # 3. Call LLM (Async)
        response = await self.advisor.send_prompt(system_prompt, user_prompt)
        
        # 4. Parse Response
        return self._parse_response(response, direction)

    def _parse_response(self, text, proposed_direction):
        default = {'action': proposed_direction, 'confidence': 75, 'reason': 'Technical Fallback (No LLM Response)'}
        
        if not text: return default
        
        try:
            import re
            
            # Regex to find ACTION | CONFIDENCE | REASON
            # Looks for BUY/SELL/HOLD/NEUTRAL, followed by pipe, number (with optional %), pipe, reason
            # Handles **BUY** markdown and potentially **Reason**
            pattern = r"(?:[*]*)(BUY|SELL|HOLD|NEUTRAL)(?:[*]*)\s*\|\s*(\d+)\%?\s*\|\s*(?:[*]*)(.*)"
            
            # Search from the end of the string first (likely conclusion)
            matches = list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
            
            if matches:
                # Use the last match found
                last_match = matches[-1]
                action = last_match.group(1).upper()
                if action == 'NEUTRAL': action = 'HOLD' # Map NEUTRAL to HOLD
                
                conf = int(last_match.group(2))
                reason = last_match.group(3).strip().rstrip('*') # Remove trailing markdown
                
                return {
                    'action': action,
                    'confidence': conf,
                    'reason': reason
                }
            
            # Fallback: strict split if regex fails (legacy support)
            lines = text.strip().split('\n')
            for line in reversed(lines):
                if '|' in line and len(line.split('|')) >= 3:
                     parts = line.split('|')
                     action = parts[0].strip().upper()
                     if action in ['BUY', 'SELL', 'HOLD']:
                         return {
                             'action': action,
                             'confidence': int(''.join(filter(str.isdigit, parts[1]))),
                             'reason': parts[2].strip()
                         }

            return default
            
        except Exception as e:
            return default
