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
        df_features = features.add_technical_features(df) if df is not None and len(df) >= 50 else None
        
        try:
            # Prepare state for PPO RL Position Manager
            # State: [price_change, volatility, time_in_trade, regime, portfolio_pnl]
            direction_mult = 1.0 if position.type == 0 else -1.0
            price_change = ((current_price - entry_price) / entry_price) * 100.0 * direction_mult
            
            if df_features is not None:
                volatility = df_features['atr'].iloc[-1] / current_price * 100.0 if 'atr' in df_features.columns else 0.01
                adx = df_features['adx'].iloc[-1] if 'adx' in df_features.columns else 20
                sma20 = df_features['sma20'].iloc[-1] if 'sma20' in df_features.columns else current_price
                sma50 = df_features['sma50'].iloc[-1] if 'sma50' in df_features.columns else current_price
            else:
                volatility = 0.01
                adx = 20
                sma20 = current_price
                sma50 = current_price
                
            entry_time = position.time
            current_time = time.time()
            time_in_trade_minutes = (current_time - entry_time) / 60.0
            
            if adx > 25:
                base_regime = 1.0 if sma20 > sma50 else -1.0
            else:
                base_regime = 0.0
            aligned_regime = base_regime * direction_mult
            
            portfolio_pnl_pct = price_change * position.volume
            
            state = np.array([
                price_change, 
                volatility, 
                time_in_trade_minutes, 
                aligned_regime, 
                portfolio_pnl_pct
            ], dtype=np.float32)
            
            # Get discrete action from PPO
            from analysis.ppo_position_manager import get_ppo_manager
            ppo_manager = get_ppo_manager()
            ppo_action = ppo_manager.get_trade_signal(state)
            
            if ppo_action != "HOLD":
                # Map PPO action string to execution action matching the old logic
                action_type = "PARTIAL_CLOSE" if ppo_action == "REDUCE_50%" else ("EXPAND" if ppo_action == "INCREASE" else "CLOSE")
                return {
                    'ticket': ticket,
                    'symbol': symbol,
                    'action': action_type,
                    'reason': f"PPO Agent Signal: {ppo_action} | PnL: ${profit:.2f}",
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                
        except Exception as e:
            print(f"[ADAPTIVE] Error checking PPO for {symbol}: {e}")
            
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
        # Do not expand positions that are already the result of an expansion (prevents geometric ticket explosion)
        if hasattr(position, 'magic') and position.magic == 100:
            return False
            
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
            sym_info = mt5.symbol_info(symbol)
            
            if not tick or not sym_info:
                return False
                
            price = tick.ask if direction == 0 else tick.bid
            
            # Snap volume to correct valid steps dynamically (fixes XAGUSD errors)
            volume_step = sym_info.volume_step
            add_volume = new_lot - current_lot
            
            # Bound addition logic to step precision
            clipped_volume = max(sym_info.volume_min, round(add_volume / volume_step) * volume_step)
            
            if clipped_volume <= 0:
                print(f"[ADAPTIVE] Invalid minimal addition calculated for {symbol}")
                return False
            
            # Place the order
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(clipped_volume),  # Only the additional amount, properly constrained
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
            sym_info = mt5.symbol_info(symbol)
            
            if not tick or not sym_info:
                return False
                
            price = tick.bid if position.type == 0 else tick.ask
            volume_step = sym_info.volume_step
            # Snap partial close math to exact valid steps
            close_lot = max(sym_info.volume_min, round(close_lot / volume_step) * volume_step)
            
            if close_lot <= 0:
                return False
            
            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(close_lot),
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