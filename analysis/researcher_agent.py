
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
        direction = quant_data.get('direction', 'NEUTRAL')
        score = quant_data.get('score', 0)
        ml_prob = quant_data.get('ml_prob', 0)
        regime = analyst_data.get('regime', 'NORMAL')
        h4_trend = quant_data.get('h4_trend', 0)
        
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
        
        user_prompt = f"""
        Analyze this trade for {symbol} ({settings.TIMEFRAME}):
        
        Proposed Action: {direction} (Score: {score}/6)
        
        Factors:
        - ML Confidence: {ml_prob:.2f} (Random Forest/XGBoost)
        - Market Regime: {regime}
        - H4 Trend: {h4_trend}
        - Technical Details: {quant_data.get('details')}
        - Indicators:
          RSI: {quant_data['features'].get('rsi', 0):.1f}
          ADX: {quant_data['features'].get('adx', 0):.1f}
          Close: {quant_data['features'].get('close', 0):.5f}
        
        Debate the Bull and Bear cases. Then provide the final conclusion.
        Output strictly as: ACTION | CONFIDENCE | REASON
        Example: BUY | 85 | Strong trend alignment with high ML probability.
        """
        
        # 3. Call LLM (Async)
        response = await self.advisor.send_prompt(system_prompt, user_prompt)
        
        # 4. Parse Response
        return self._parse_response(response, direction)

    def _parse_response(self, text, proposed_direction):
        default = {'action': 'HOLD', 'confidence': 0, 'reason': 'Error parsing'}
        
        if not text: return default
        
        try:
            # Mistral might be chatty, try to find the last line or the pipe format
            lines = text.strip().split('\n')
            final_line = lines[-1]
            
            # Look for the pipe
            if '|' in final_line:
                parts = final_line.split('|')
            else:
                # Maybe it's in the text somewhere?
                # Fallback: Just look for ACTION | CONFIDENCE
                found = False
                for line in reversed(lines):
                    if '|' in line and len(line.split('|')) == 3:
                        parts = line.split('|')
                        found = True
                        break
                if not found: return default

            action = parts[0].strip().upper()
            conf = int(parts[1].strip().replace('%',''))
            reason = parts[2].strip()
            
            return {
                'action': action,
                'confidence': conf,
                'reason': reason
            }
            
        except Exception as e:
            print(f"[RESEARCHER] Parse Error: {e}")
            return default
