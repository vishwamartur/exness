import asyncio
import time
import sys
import os
from datetime import datetime, timezone, date

import warnings
warnings.filterwarnings("ignore", category=UserWarning, message=".*X has feature names.*") 
warnings.filterwarnings("ignore", category=UserWarning, module="gluonts")
warnings.filterwarnings("ignore", message=".*Using a non-tuple sequence for multidimensional indexing.*")

from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
from utils.auto_trainer import AutoTrainer
from api import stream_server as stream


def main():
    print("=" * 60)
    print("  INSTITUTIONAL SURESHOT SCANNER v2.3")
    print("  Self-Learning | Parallel | Adaptive | Journal")
    print("=" * 60)
    
    # 1. Connect to MT5
    client = MT5Client()
    if not client.connect():
        print("Could not connect to MT5. Exiting...")
        return

    # 2. Auto-detect available Exness symbols
    if not client.detect_available_symbols():
        print("No instruments found. Check your Exness account. Exiting...")
        client.shutdown()
        return

    # Account info
    info = client.get_account_info_dict()
    print(f"\n  Account Balance: ${info.get('balance', 0):.2f}")
    print(f"  Account Equity:  ${info.get('equity', 0):.2f}")
    print(f"  Leverage:        1:{info.get('leverage', 0)}")
    
    print(f"\n  Instruments:     {len(settings.SYMBOLS)} "
          f"({len(settings.SYMBOLS_FOREX_MAJORS)}M "
          f"{len(settings.SYMBOLS_FOREX_MINORS)}m "
          f"{len(settings.SYMBOLS_CRYPTO)}C "
          f"{len(settings.SYMBOLS_COMMODITIES)}Co)")
    print(f"  Timeframe:       {settings.TIMEFRAME}")
    print(f"  Risk/Trade:      {settings.RISK_PERCENT}% → {settings.MAX_RISK_PERCENT}%")
    print(f"  Sureshot:        Score ≥ {settings.SURESHOT_MIN_SCORE}/6 (adaptive)")
    print(f"  R:R Target:      1:{settings.ATR_TP_MULTIPLIER/settings.ATR_SL_MULTIPLIER:.1f}")
    print(f"  Max Daily:       {settings.MAX_DAILY_TRADES} trades")
    print(f"  Session:         {'London/NY' if settings.SESSION_FILTER else 'All'}")
    print(f"  Self-Learning:   RF/4h | LSTM/8h | Emergency/<40% WR")
    print("=" * 60)

    # 3. Initialize Strategy
    strategy = InstitutionalStrategy(client)
    # Models loaded by QuantAgent internally

    # 4. Start Auto-Trainer (background self-learning)
    trainer = AutoTrainer(strategy, strategy.journal)
    trainer.start()
    

    # 5. Start Stream Server (auto-port 8000-8009)
    try:
        stream.start_server(base_port=8000)
    except Exception as e:
        print(f"[ERROR] Stream Server failed: {e}")
        
    print(f"\nScanner ready. {len(settings.SYMBOLS)} instruments | "
          f"Self-learning active | Trade journal active\n")
    
    from utils.telegram_notifier import get_notifier as _tg

    # Run async scan loop
    loop = asyncio.get_event_loop()
    last_daily_summary_date: date | None = None   # fires Telegram once per day @ 21:00 UTC

    try:
        cycle_num = 0
        while True:
            cycle_num += 1
            cycle_start = time.time()

            # Scan all agents concurrently
            loop.run_until_complete(strategy.run_scan_loop())

            elapsed = time.time() - cycle_start
            now_utc = datetime.now(timezone.utc)
            now_str = now_utc.strftime('%H:%M:%S')
            trades_left = settings.MAX_DAILY_TRADES - strategy.daily_trade_count

            # Auto-trainer status
            ts = trainer.get_status()

            print(f"\n{'─'*60}")
            print(f"  Cycle #{cycle_num} @ {now_str} UTC | {elapsed:.1f}s | "
                  f"Trades Left: {trades_left}")
            print(f"  Auto-Train: RF in {ts['next_rf_in']:.0f}min | "
                  f"XGB in {ts.get('next_xgb_in', 0):.0f}min | "
                  f"LSTM in {ts['next_lstm_in']:.0f}min | "
                  f"Retrains: {ts['rf_retrains']}RF {ts['lstm_retrains']}LSTM "
                  f"{ts['emergency_retrains']}E")
            print(f"{'─'*60}\n")

            # ── Daily Summary Telegram @ 21:00 UTC ───────────────────────
            today = now_utc.date()
            if now_utc.hour == 21 and last_daily_summary_date != today:
                try:
                    info = client.get_account_info_dict()
                    journal_stats = strategy.journal.get_daily_stats()
                    _tg().daily_summary(
                        balance=info.get('balance', 0),
                        equity=info.get('equity', 0),
                        day_pl=info.get('profit', 0),
                        total_trades=journal_stats.get('total', 0),
                        win_trades=journal_stats.get('wins', 0),
                    )
                    last_daily_summary_date = today
                    print(f"[TELEGRAM] Daily summary sent.")
                except Exception as _e:
                    print(f"[TELEGRAM] Daily summary error: {_e}")
            # ────────────────────────────────────────────────────────────

            # Adaptive sleep: faster during overlap, slower off-hours
            sleep_time = max(10.0, 45.0 - elapsed)
            time.sleep(sleep_time) 
            
    except KeyboardInterrupt:
        print("\nStopping bot...")
        trainer.stop()
        strategy.journal.print_summary()
        trainer.print_status()
    finally:
        client.shutdown()
        print("MT5 connection closed.")


if __name__ == "__main__":
    # Ensure asyncio policy for Windows if needed, though standard loop usually works
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
