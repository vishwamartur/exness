"""Tests for ATR trailing stop logic in execution/mt5_client.py."""

from unittest.mock import patch, MagicMock
import pytest


class TestUpdateTrailingStop:
    """Test update_trailing_stop() and _modify_sl() methods."""

    def _make_client(self):
        """Create an MT5Client with mocked settings and mt5 module."""
        with patch("execution.mt5_client.settings") as mock_settings, \
             patch("execution.mt5_client.mt5"):
            mock_settings.SYMBOL = "EURUSD"
            mock_settings.LOT_SIZE = 0.01
            mock_settings.DEVIATION = 20
            mock_settings.TRAILING_ATR_MULTIPLIER = 1.5
            from execution.mt5_client import MT5Client
            return MT5Client()

    # ── update_trailing_stop ─────────────────────────────────────────────

    @patch("execution.mt5_client.mt5")
    def test_buy_trailing_stop_moves_up(self, mock_mt5):
        """BUY: SL should move up when price moves in our favour."""
        client = self._make_client()

        # Existing position: BUY at 1.1000, current SL=1.0950
        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_BUY
        pos.sl = 1.0950
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]

        # Current bid moved up to 1.1080
        tick = MagicMock()
        tick.bid = 1.1080
        tick.ask = 1.1082
        mock_mt5.symbol_info_tick.return_value = tick

        # ATR=0.0020, multiplier=1.5 → trail_distance=0.003
        # new_sl = 1.1080 - 0.003 = 1.1050 > current SL(1.0950) → should move
        mock_mt5.order_send.return_value = MagicMock(retcode=mock_mt5.TRADE_RETCODE_DONE)

        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020, multiplier=1.5)
        assert result is True

    @patch("execution.mt5_client.mt5")
    def test_buy_trailing_stop_no_move_down(self, mock_mt5):
        """BUY: SL should NOT move down (loosen)."""
        client = self._make_client()

        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_BUY
        pos.sl = 1.1050  # Already high
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]

        tick = MagicMock()
        tick.bid = 1.1060  # Price retraced
        tick.ask = 1.1062
        mock_mt5.symbol_info_tick.return_value = tick

        # new_sl = 1.1060 - 0.003 = 1.1030 < current SL(1.1050) → should NOT move
        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020, multiplier=1.5)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_sell_trailing_stop_moves_down(self, mock_mt5):
        """SELL: SL should move down when price moves in our favour."""
        client = self._make_client()

        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_SELL
        pos.sl = 1.1050  # Current SL above entry
        pos.tp = 1.0900
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]

        tick = MagicMock()
        tick.bid = 1.0918
        tick.ask = 1.0920  # Price moved down
        mock_mt5.symbol_info_tick.return_value = tick

        # new_sl = 1.0920 + 0.003 = 1.0950 < current SL(1.1050) → should move
        mock_mt5.order_send.return_value = MagicMock(retcode=mock_mt5.TRADE_RETCODE_DONE)

        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020, multiplier=1.5)
        assert result is True

    @patch("execution.mt5_client.mt5")
    def test_sell_trailing_stop_no_move_up(self, mock_mt5):
        """SELL: SL should NOT move up (loosen)."""
        client = self._make_client()

        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_SELL
        pos.sl = 1.0950
        pos.tp = 1.0900
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]

        tick = MagicMock()
        tick.bid = 1.0958
        tick.ask = 1.0960  # Price retraced up
        mock_mt5.symbol_info_tick.return_value = tick

        # new_sl = 1.0960 + 0.003 = 1.0990 > current SL(1.0950) → should NOT move
        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020, multiplier=1.5)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_trailing_stop_position_not_found(self, mock_mt5):
        """Returns False when position doesn't exist."""
        client = self._make_client()
        mock_mt5.positions_get.return_value = None
        result = client.update_trailing_stop(ticket=99999, atr_value=0.0020)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_trailing_stop_no_tick(self, mock_mt5):
        """Returns False when tick data unavailable."""
        client = self._make_client()

        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_BUY
        pos.sl = 1.0950
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]
        mock_mt5.symbol_info_tick.return_value = None

        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_trailing_stop_uses_default_multiplier(self, mock_mt5):
        """Uses settings.TRAILING_ATR_MULTIPLIER when multiplier not given."""
        client = self._make_client()

        pos = MagicMock()
        pos.type = mock_mt5.ORDER_TYPE_BUY
        pos.sl = 1.0900  # Low SL
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]

        tick = MagicMock()
        tick.bid = 1.1080
        tick.ask = 1.1082
        mock_mt5.symbol_info_tick.return_value = tick

        mock_mt5.order_send.return_value = MagicMock(retcode=mock_mt5.TRADE_RETCODE_DONE)

        # default multiplier is 1.5 → trail = 0.0020 * 1.5 = 0.003
        # new_sl = 1.1080 - 0.003 = 1.1050 > 1.0900 → should update
        result = client.update_trailing_stop(ticket=12345, atr_value=0.0020)
        assert result is True

    # ── _modify_sl ────────────────────────────────────────────────────────

    @patch("execution.mt5_client.mt5")
    def test_modify_sl_success(self, mock_mt5):
        """Successful SL modification."""
        client = self._make_client()

        pos = MagicMock()
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]
        mock_mt5.order_send.return_value = MagicMock(retcode=mock_mt5.TRADE_RETCODE_DONE)

        result = client._modify_sl(ticket=12345, new_sl=1.1050)
        assert result is True

    @patch("execution.mt5_client.mt5")
    def test_modify_sl_preserves_tp(self, mock_mt5):
        """TP should be preserved when not explicitly passed."""
        client = self._make_client()

        pos = MagicMock()
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]
        mock_mt5.order_send.return_value = MagicMock(retcode=mock_mt5.TRADE_RETCODE_DONE)

        client._modify_sl(ticket=12345, new_sl=1.1050)
        call_args = mock_mt5.order_send.call_args[0][0]
        assert call_args["tp"] == round(1.1100, 5)

    @patch("execution.mt5_client.mt5")
    def test_modify_sl_position_not_found(self, mock_mt5):
        """Returns False when position doesn't exist."""
        client = self._make_client()
        mock_mt5.positions_get.return_value = None
        result = client._modify_sl(ticket=99999, new_sl=1.1050)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_modify_sl_order_send_fails(self, mock_mt5):
        """Returns False when order_send fails."""
        client = self._make_client()

        pos = MagicMock()
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]
        mock_mt5.order_send.return_value = MagicMock(retcode=99999)  # Failure

        result = client._modify_sl(ticket=12345, new_sl=1.1050)
        assert result is False

    @patch("execution.mt5_client.mt5")
    def test_modify_sl_exception_handling(self, mock_mt5):
        """Returns False on unexpected exceptions."""
        client = self._make_client()

        pos = MagicMock()
        pos.tp = 1.1100
        pos.symbol = "EURUSD"
        mock_mt5.positions_get.return_value = [pos]
        mock_mt5.order_send.side_effect = Exception("Connection lost")

        result = client._modify_sl(ticket=12345, new_sl=1.1050)
        assert result is False
