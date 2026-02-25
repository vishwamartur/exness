"""
Adaptive Position Manager - Real-time ML-based Position Management

This module implements intelligent position management that:
1. Uses real-time ML predictions to evaluate if positions should be held or closed
2. Expands winning positions when profits are running
3. Protects capital by closing losing positions early
4. Dynamically adjusts position sizes based on market conditions
"""

import time
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import MetaTrader5 as mt5

from config import settings
from market_data import loader
from strategy import features
from analysis.quant_agent import QuantAgent


class AdaptivePositionManager:
    """
    Manages open positions using real-time ML analysis to optimize profits.
    """
    
    def __init__(self, mt5_client, quant_agent: QuantAgent):
        self.client = mt5_client
        self.quant = quant_agent
        self.last_analysis_time = {}
        self.position_performance = {}  # Track individual position performance
        self.analysis_interval = 60  # Analyze positions every 60 seconds
        
    def manage_positions(self) -> List[Dict]:
        """
        Main position management loop.
        Returns list of actions taken.
        """
        actions = []
        
        # Get all open positions
        positions = self.client.get_all_positions()
        if not positions:
            return actions
            
        current_time = time.time()
        
        for position in positions:
            symbol = position.symbol
            ticket = position.ticket
            
            # Skip if recently analyzed
            last_analysis = self.last_analysis_time.get(ticket, 0)
            if current_time - last_analysis < self.analysis_interval:
                continue
                
            self.last_analysis_time[ticket] = current_time
            
            try:
                action = self._analyze_position(position)
                if action:
                    actions.append(action)
            except Exception as e:
                print(f"[ADAPTIVE] Error analyzing position {ticket}: {e}")
                
        return actions
    
    def _analyze_position(self, position) -> Optional[Dict]:
        """
        Analyze a single position using real-time ML predictions.
        Returns action dictionary or None if no action needed.
        """
        symbol = position.symbol
        ticket = position.ticket
        direction = "BUY" if position.type == 0 else "SELL"
        current_price = position.price_current
        entry_price = position.price_open
        profit = position.profit
        
        # Calculate key metrics
        price_diff = current_price - entry_price if direction == "BUY" else entry_price - current_price
        pip_value = self._get_pip_value(symbol)
        pips_pnl = price_diff / pip_value
        
        # Fetch latest market data
        df = loader.get_historical_data(symbol, settings.TIMEFRAME, 100)
        if df is None or len(df) < 50:
            return None
            
        df_features = features.add_technical_features(df)
        
        # Get ML prediction for current market conditions
        try:
            ml_prob, ml_pred = self.quant._get_rf_prediction(df_features, symbol)
            
            # Get trend analysis
            trend_score = self._analyze_trend(df_features, direction)
            
            # Get volatility assessment
            volatility_score = self._assess_volatility(df_features)
            
            # Decision logic
            action = self._make_decision(
                position=position,
                ml_prob=ml_prob,
                ml_pred=ml_pred,
                trend_score=trend_score,
                volatility_score=volatility_score,
                pips_pnl=pips_pnl,
                profit=profit
            )
            
            if action:
                action['ticket'] = ticket
                action['symbol'] = symbol
                action['timestamp'] = datetime.now(timezone.utc).isoformat()
                return action
                
        except Exception as e:
            print(f"[ADAPTIVE] Error in ML analysis for {symbol}: {e}")
            
        return None
    
    def _analyze_trend(self, df_features, direction: str) -> float:
        """
        Analyze current trend strength and alignment with position.
        Returns score between -1 (strong opposite trend) and 1 (strong aligned trend).
        """
        last_row = df_features.iloc[-1]
        
        # Simple trend indicators
        sma_20 = df_features['close'].rolling(20).mean().iloc[-1]
        sma_50 = df_features['close'].rolling(50).mean().iloc[-1]
        current_price = df_features['close'].iloc[-1]
        
        # Trend direction
        if sma_20 > sma_50:
            trend_direction = "BUY"
        else:
            trend_direction = "SELL"
            
        # Trend strength (0-1)
        trend_strength = abs(sma_20 - sma_50) / sma_50
        
        # Alignment score
        if trend_direction == direction:
            return min(trend_strength, 1.0)  # Positive if aligned
        else:
            return -min(trend_strength, 1.0)  # Negative if opposite
            
    def _assess_volatility(self, df_features) -> float:
        """
        Assess current market volatility.
        Returns volatility score between 0 (low) and 1 (high).
        """
        if 'atr' in df_features.columns:
            atr = df_features['atr'].iloc[-1]
            atr_normalized = atr / df_features['close'].iloc[-1]  # Normalize by price
            return min(atr_normalized * 1000, 1.0)  # Scale appropriately
        return 0.5  # Default medium volatility
        
    def _make_decision(self, position, ml_prob: float, ml_pred: int, 
                      trend_score: float, volatility_score: float,
                      pips_pnl: float, profit: float) -> Optional[Dict]:
        """
        Make decision based on all factors.
        """
        symbol = position.symbol
        direction = "BUY" if position.type == 0 else "SELL"
        
        # Current position metrics
        # Calculate SL distance from current price
        entry_price = position.price_open
        sl_price = position.sl
        if sl_price > 0:  # SL is set
            if direction == "BUY":
                sl_distance = abs(entry_price - sl_price)
            else:  # SELL
                sl_distance = abs(sl_price - entry_price)
        else:
            sl_distance = 0.001  # Default small value if no SL
        
        risk_reward = abs(pips_pnl) / max(sl_distance * self._get_pip_value(symbol), 0.1)
        
        # Decision factors
        factors = {
            'ml_confidence': ml_prob,
            'trend_alignment': trend_score,
            'volatility': volatility_score,
            'current_pnl_pips': pips_pnl,
            'current_profit': profit,
            'risk_reward': risk_reward
        }
        
        # Hold conditions (strong trend alignment + positive ML signal)
        if (trend_score > 0.3 and ml_prob > 0.6 and pips_pnl > 5):
            # Consider expanding position
            if self._should_expand_position(position, factors):
                return {
                    'action': 'EXPAND',
                    'reason': f'Strong trend ({trend_score:.2f}) + ML confidence ({ml_prob:.2f})',
                    'factors': factors
                }
            else:
                return {
                    'action': 'HOLD',
                    'reason': f'Positive trend ({trend_score:.2f}) + ML confidence ({ml_prob:.2f})',
                    'factors': factors
                }
        
        # Close conditions (opposing trend or weak ML signal)
        elif (trend_score < -0.3 or ml_prob < 0.4) and pips_pnl < -2:
            return {
                'action': 'CLOSE',
                'reason': f'Opposing trend ({trend_score:.2f}) or weak ML ({ml_prob:.2f})',
                'factors': factors
            }
            
        # Partial close for profit protection
        elif pips_pnl > 10 and risk_reward > 2.0:
            return {
                'action': 'PARTIAL_CLOSE',
                'reason': f'Lock in profits (RR: {risk_reward:.1f})',
                'factors': factors
            }
            
        # Default: hold position
        return None
    
    def _should_expand_position(self, position, factors: Dict) -> bool:
        """
        Determine if we should expand a winning position.
        """
        # Check if we're already at max positions
        all_positions = self.client.get_all_positions()
        if len(all_positions) >= settings.MAX_OPEN_POSITIONS:
            return False
            
        # Check if position is significantly profitable
        if factors['current_pnl_pips'] < 15:
            return False
            
        # Check trend strength
        if factors['trend_alignment'] < 0.5:
            return False
            
        # Check volatility (avoid expanding in very high volatility)
        if factors['volatility'] > 0.8:
            return False
            
        # Check ML confidence
        if factors['ml_confidence'] < 0.7:
            return False
            
        return True
    
    def _get_pip_value(self, symbol: str) -> float:
        """
        Get pip value for a symbol.
        """
        # Simple pip value calculation - could be enhanced
        if 'JPY' in symbol:
            return 0.01
        elif symbol in ['XAUUSD', 'XAGUSD']:
            return 0.01  # Precious metals
        else:
            return 0.0001
    
    def _expand_position(self, position) -> bool:
        """
        Expand a winning position by adding to it.
        """
        try:
            symbol = position.symbol
            direction = position.type  # 0=BUY, 1=SELL
            current_lot = position.volume
            
            # Calculate new lot size (25% increase)
            new_lot = current_lot * 1.25
            
            # Place new order in same direction
            cmd = mt5.ORDER_TYPE_BUY if direction == 0 else mt5.ORDER_TYPE_SELL
            tick = mt5.symbol_info_tick(symbol)
            
            if not tick:
                return False
                
            price = tick.ask if direction == 0 else tick.bid
            
            # Place the order
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": new_lot - current_lot,  # Only the additional amount
                "type": cmd,
                "price": price,
                "deviation": settings.DEVIATION,
                "magic": 100,  # Magic number for expanded positions
                "comment": "Expanded Position",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"[ADAPTIVE] Expanded {symbol} position to {new_lot:.2f} lots")
                return True
            else:
                print(f"[ADAPTIVE] Failed to expand {symbol} position: {result.comment if result else 'Unknown error'}")
                return False
                
        except Exception as e:
            print(f"[ADAPTIVE] Error expanding position: {e}")
            return False
    
    def _close_position(self, ticket: int, reason: str) -> bool:
        """
        Close a position.
        """
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return False
                
            position = position[0]
            symbol = position.symbol
            lot = position.volume
            
            # Determine close direction
            cmd = mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY
            
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return False
                
            price = tick.bid if position.type == 0 else tick.ask
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": lot,
                "type": cmd,
                "position": ticket,
                "price": price,
                "deviation": settings.DEVIATION,
                "magic": 200,
                "comment": f"Adaptive Close: {reason}",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                print(f"[ADAPTIVE] Closed position {ticket} ({symbol}): {reason}")
                return True
            else:
                print(f"[ADAPTIVE] Failed to close position {ticket}: {result.comment if result else 'Unknown error'}")
                return False
                
        except Exception as e:
            print(f"[ADAPTIVE] Error closing position: {e}")
            return False
    
    def _partial_close_position(self, ticket: int, percentage: float = 0.5) -> bool:
        """
        Partially close a position to lock in profits.
        """
        try:
            position = mt5.positions_get(ticket=ticket)
            if not position:
                return False
                
            position = position[0]
            symbol = position.symbol
            current_lot = position.volume
            close_lot = current_lot * percentage
            
            # Determine close direction
            cmd = mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY
            
            tick = mt5.symbol_info_tick(symbol)
            if not tick:
                return False
                
            price = tick.bid if position.type == 0 else tick.ask
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": close_lot,
                "type": cmd,
                "position": ticket,
                "price": price,
                "deviation": settings.DEVIATION,
                "magic": 300,
                "comment": f"Partial Close {percentage*100}%",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                remaining_lot = current_lot - close_lot
                print(f"[ADAPTIVE] Partially closed {ticket} ({symbol}): {percentage*100}% ({close_lot:.2f} lots, {remaining_lot:.2f} remaining)")
                return True
            else:
                print(f"[ADAPTIVE] Failed to partially close position {ticket}: {result.comment if result else 'Unknown error'}")
                return False
                
        except Exception as e:
            print(f"[ADAPTIVE] Error in partial close: {e}")
            return False
    
    def execute_actions(self, actions: List[Dict]) -> int:
        """
        Execute all position management actions.
        Returns number of successful actions.
        """
        success_count = 0
        
        for action in actions:
            try:
                ticket = action['ticket']
                action_type = action['action']
                reason = action['reason']
                
                if action_type == 'EXPAND':
                    if self._expand_position(mt5.positions_get(ticket=ticket)[0]):
                        success_count += 1
                elif action_type == 'CLOSE':
                    if self._close_position(ticket, reason):
                        success_count += 1
                elif action_type == 'PARTIAL_CLOSE':
                    if self._partial_close_position(ticket, 0.5):  # Close 50%
                        success_count += 1
                elif action_type == 'HOLD':
                    # Just log the decision
                    print(f"[ADAPTIVE] Holding position {ticket} ({action['symbol']}): {reason}")
                    
            except Exception as e:
                print(f"[ADAPTIVE] Error executing action: {e}")
                
        return success_count