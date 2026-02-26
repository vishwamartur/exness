"""
LLM Advisor Factory
Dynamically provides the fastest/best available LLM Advisor
based on configured API keys. Prioritizes Groq for inference speed
in the rapid MT5 scalping environment, falling back to Mistral.
"""

from analysis.groq_advisor import GroqAdvisor
from analysis.mistral_advisor import MistralAdvisor
import os

_advisor_instance = None

def get_advisor():
    """
    Returns a unified Advisor instance, favoring Groq if configured.
    Implements a singleton pattern to avoid redundant initializations.
    """
    global _advisor_instance
    if _advisor_instance is not None:
        return _advisor_instance

    groq_key = os.getenv("GROQ_API_KEY")
    mistral_key = os.getenv("MISTRAL_API_KEY")

    if groq_key:
        print("[LLM-FACTORY] Groq API Key found. Routing AI workload to LLaMA (Fast Inference).")
        _advisor_instance = GroqAdvisor()
    elif mistral_key:
        print("[LLM-FACTORY] Mistral API Key found. Routing AI workload to Mistral.")
        _advisor_instance = MistralAdvisor()
    else:
        print("[LLM-FACTORY] WARNING: No API keys found. AI Agents will provide neutral fallbacks.")
        # Return a dummy matching interface
        class DummyAdvisor:
            async def analyze_market(self, *args, **kwargs):
                return "NEUTRAL", 0, "No API Key configured"
            async def send_prompt(self, *args, **kwargs):
                return "NEUTRAL | 0 | No API Key configured"
        _advisor_instance = DummyAdvisor()

    return _advisor_instance
