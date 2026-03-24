"""
Download Massive.com Flat Files
=================================
CLI script to bulk download historical CSV minute/day aggregates from Massive.com S3.
Saves data to `data/massive/` for model training and backtesting.
"""

import sys
import os
import argparse
from datetime import datetime, timedelta, timezone

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from market_data.massive_client import get_s3_client

def main():
    parser = argparse.ArgumentParser(description="Download historical Massive.com flat files.")
    parser.add_argument("--asset", choices=["crypto", "forex"], required=True,
                        help="Asset class to download")
    parser.add_argument("--type", choices=["minute_aggs_v1", "day_aggs_v1"], default="minute_aggs_v1",
                        help="Data frequency type")
    parser.add_argument("--days", type=int, default=30,
                        help="Number of days looking back from today to download")
    parser.add_argument("--start", type=str,
                        help="Explicit start date YYYY-MM-DD (overrides --days)")
    parser.add_argument("--end", type=str,
                        help="Explicit end date YYYY-MM-DD (overrides today)")
    parser.add_argument("--out", type=str, default="data/massive",
                        help="Output directory base path")
    
    args = parser.parse_args()
    
    s3_client = get_s3_client()
    if not s3_client._get_client():
        print("❌ S3 client failed to initialize. Check if boto3 is installed and credentials are set.")
        return
    
    # Calculate dates
    end_date = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    if args.start:
        start_date = args.start
    else:
        start_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
        start_date = start_dt.strftime("%Y-%m-%d")
        
    print(f"📥 Downloading Massive.com {args.asset} {args.type}")
    print(f"📅 Range: {start_date} to {end_date}")
    
    downloaded_files = s3_client.download_date_range(
        asset_type=args.asset,
        data_type=args.type,
        start_date=start_date,
        end_date=end_date,
        output_dir=args.out
    )
    
    print("\n✅ Download Summary:")
    if downloaded_files:
        print(f"  Successfully downloaded/verified {len(downloaded_files)} objects.")
        print(f"  Directory: {os.path.abspath(os.path.join(args.out, args.asset, args.type))}")
    else:
        print("  No files downloaded (they might already exist locally or none match the criteria).")


if __name__ == "__main__":
    main()
