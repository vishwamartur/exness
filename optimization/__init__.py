"""
Optimization module for strategy parameter auto-tuning.

Provides walk-forward optimization with per-session parameter tuning,
guardrails to prevent overfitting, and audit logging.
"""

try:
    from optimization.parameter_tuner import ParameterTuner
except ImportError:
    ParameterTuner = None

__all__ = ["ParameterTuner"]
