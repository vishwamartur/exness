import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint
import statsmodels.api as sm
from config import settings
from execution.mt5_client import MT5Client

class StatArbManager:
    """
    Statistical Arbitrage (Pairs Trading) Engine
    Designed for Market-Neutral Hedging.
    """
    def __init__(self, client: MT5Client):
        self.client = client
        self.pairs = settings.STAT_ARB_PAIRS
        self.max_zscore = settings.STAT_ARB_MAX_ZSCORE
        self.lot_size = settings.STAT_ARB_LOT_SIZE
        
        # Track active spread positions to avoid duplicate entries
        # Key: (Symbol_A, Symbol_B), Value: position_direction (e.g. 'LONG_A_SHORT_B')
        self.active_spreads = {} 

    def check_cointegration(self, df_A, df_B, p_value_threshold=0.05):
        """
        Engle-Granger Two-Step Cointegration Test.
        Ensures the two pairs are statistically tethered.
        """
        # Ensure identical lengths and indices
        common_idx = df_A.index.intersection(df_B.index)
        if len(common_idx) < 100:
            return False, 1.0 # Not enough overlapping data
            
        series_A = df_A.loc[common_idx, 'close']
        series_B = df_B.loc[common_idx, 'close']

        # Engle-Granger Test
        score, p_value, _ = coint(series_A, series_B)
        
        is_cointegrated = p_value < p_value_threshold
        return is_cointegrated, p_value

    def calculate_spread(self, df_A, df_B):
        """
        Calculates the Spread and Z-Score using Ordinary Least Squares (OLS) Regression.
        Spread = Price_A - (Hedge_Ratio * Price_B)
        """
        common_idx = df_A.index.intersection(df_B.index)
        if len(common_idx) < 50:
            return None, None
            
        y = df_A.loc[common_idx, 'close']
        x = df_B.loc[common_idx, 'close']
        
        # Add constant for OLS
        X = sm.add_constant(x)
        
        # Linear Regression to find Hedge Ratio (Beta)
        model = sm.OLS(y, X).fit()
        hedge_ratio = model.params['close']
        
        # Calculate Spread
        spread = y - (hedge_ratio * x)
        
        # Calculate Z-Score of the Spread
        mean_spread = spread.mean()
        std_spread = spread.std()
        
        if std_spread == 0:
            return None, None
            
        current_spread = spread.iloc[-1]
        z_score = (current_spread - mean_spread) / std_spread
        
        return hedge_ratio, z_score

    def analyze_pair(self, symbol_A, symbol_B, df_A, df_B):
        """
        Main analysis loop to test pairs and generate execution signals.
        """
        pair_key = (symbol_A, symbol_B)
        
        if df_A is None or df_B is None or df_A.empty or df_B.empty:
            return None

        # 1. Are they currently cointegrated?
        is_coint, p_val = self.check_cointegration(df_A, df_B)
        if not is_coint:
            # If a previously active spread loses cointegration abruptly, we should probably close it
            if pair_key in self.active_spreads:
                print(f"[STAT-ARB] {symbol_A}/{symbol_B} lost cointegration (p={p_val:.3f}). Consider closing spread.")
            return None

        # 2. Calculate current divergence (Z-Score)
        hedge_ratio, z_score = self.calculate_spread(df_A, df_B)
        
        if z_score is None:
            return None
            
        print(f"[STAT-ARB] {symbol_A} vs {symbol_B} | Cointegrated (p={p_val:.3f}) | Z-Score: {z_score:.2f}")

        # 3. Generate Trading Signals based on Z-Score extremes

        # If Z-Score > +2.0: The spread is too wide. 
        # Symbol A is overvalued relative to Symbol B. 
        # Action: Short A, Long B.
        if z_score > self.max_zscore:
            if pair_key not in self.active_spreads:
                return {
                    "action": "OPEN_SPREAD",
                    "direction_A": "SELL",
                    "direction_B": "BUY",
                    "hedge_ratio": hedge_ratio,
                    "z_score": z_score
                }

        # If Z-Score < -2.0: The spread is too narrow.
        # Symbol A is undervalued relative to Symbol B.
        # Action: Long A, Short B.
        elif z_score < -self.max_zscore:
            if pair_key not in self.active_spreads:
                return {
                    "action": "OPEN_SPREAD",
                    "direction_A": "BUY",
                    "direction_B": "SELL",
                    "hedge_ratio": hedge_ratio,
                    "z_score": z_score
                }

        # If Z-Score reverts near 0 (Mean Reversion)
        elif abs(z_score) < 0.5:
            if pair_key in self.active_spreads:
                return {
                    "action": "CLOSE_SPREAD",
                    "z_score": z_score
                }

        return None

    def execute_spread_trade(self, symbol_A, symbol_B, direction_A, direction_B, hedge_ratio):
        """
        Deploy simultaneous Limit Orders on both legs of the cointegrated pair.
        """
        pair_key = (symbol_A, symbol_B)
        
        # Guard
        if pair_key in self.active_spreads:
            return False

        print(f"\n[STAT-ARB] DEPLOYING SPREAD: {direction_A} {symbol_A} | {direction_B} {symbol_B}")
        
        # Calculate Sizes based on Hedge Ratio to maintain delta-neutral weighting
        # e.g., if Hedge Ratio is 1.2, we need 1.2x more of asset B to hedge 1.0x of asset A
        lot_A = self.lot_size
        lot_B = max(0.01, round(self.lot_size * abs(hedge_ratio), 2))
        
        # Place Pending Limit Trap for Leg A
        tick_A = self.client.get_tick(symbol_A) if hasattr(self.client, 'get_tick') else __import__('MetaTrader5').symbol_info_tick(symbol_A)
        if not tick_A: return False
        
        limit_A = tick_A.bid if direction_A == "BUY" else tick_A.ask
        cmd_A = __import__('MetaTrader5').ORDER_TYPE_BUY if direction_A == "BUY" else __import__('MetaTrader5').ORDER_TYPE_SELL
        
        # Wide arbitrary SL/TP as stat-arb is managed dynamically by Z-Score, not static pips
        # (Though hard stops are smart to prevent catastrophic decoupling)
        sl_dist_A = 0.0100 # Approx 100 pips hard stop
        sl_A = limit_A - sl_dist_A if direction_A == "BUY" else limit_A + sl_dist_A
        tp_A = limit_A + sl_dist_A if direction_A == "BUY" else limit_A - sl_dist_A
        
        from datetime import datetime, timedelta
        exp_ts = int((datetime.now() + timedelta(minutes=15)).timestamp())

        res_A = self.client.place_order(cmd_A, symbol_A, lot_A, sl_A, tp_A, limit_price=limit_A, expiration=exp_ts)
        
        if not res_A:
            print(f"[STAT-ARB] Failed to open Leg A ({symbol_A}). Aborting spread.")
            return False
            
        # Place Pending Limit Trap for Leg B
        tick_B = __import__('MetaTrader5').symbol_info_tick(symbol_B)
        limit_B = tick_B.bid if direction_B == "BUY" else tick_B.ask
        cmd_B = __import__('MetaTrader5').ORDER_TYPE_BUY if direction_B == "BUY" else __import__('MetaTrader5').ORDER_TYPE_SELL
        
        sl_dist_B = 0.0100
        sl_B = limit_B - sl_dist_B if direction_B == "BUY" else limit_B + sl_dist_B
        tp_B = limit_B + sl_dist_B if direction_B == "BUY" else limit_B - sl_dist_B
        
        res_B = self.client.place_order(cmd_B, symbol_B, lot_B, sl_B, tp_B, limit_price=limit_B, expiration=exp_ts)
        
        if not res_B:
            print(f"[STAT-ARB] Failed to open Leg B ({symbol_B}). MUST FLATTEN LEG A MANUALLY!")
            # In a true HFT firm, you'd immediately send a market kill order to Leg A here.
            return False
            
        self.active_spreads[pair_key] = {
            "ticket_A": res_A.order,
            "ticket_B": res_B.order,
            "direction_A": direction_A,
            "direction_B": direction_B
        }
        
        print(f"[STAT-ARB] SPREAD TRAPPED SUCCESSFULLY. Waiting for fills.")
        return True
        
    def close_spread_trade(self, symbol_A, symbol_B):
        """
        Close both legs of the spread immediately upon mean reversion.
        """
        pair_key = (symbol_A, symbol_B)
        if pair_key not in self.active_spreads:
            return False
            
        spread_info = self.active_spreads[pair_key]
        
        print(f"\n[STAT-ARB] MEAN REVERSION REACHED! Flattening spread: {symbol_A}/{symbol_B}")
        
        # Kill Leg A
        self.client.close_position(spread_info["ticket_A"], symbol_A)
        # Kill Leg B
        self.client.close_position(spread_info["ticket_B"], symbol_B)
        
        del self.active_spreads[pair_key]
        return True
