from edgar import set_identity, get_filings
from datetime import datetime, timedelta
import pytz

# ============================================================================
# CONFIGURATION PARAMETERS
# ============================================================================

# === DATA QUALITY PARAMETERS (Matches Backtest) ===
MAX_FILING_DELAY = 30      # Allow up to 30 days delay (captures ATTO)
MIN_PRICE = 1.00           # Backtest minimum
MAX_PRICE = 10000          # Backtest maximum

# === INSIDER BOT ORIGINAL PARAMS ===
MIN_BOT_PRICE = 10.00      # Minimum share price threshold
MIN_BOT_VALUE = 500000     # Minimum total transaction value

# === POSITION FILTER - Only track these insiders ===
VALID_POSITIONS = [
    # C-Suite
    'CEO', 'Chief Executive Officer',
    'CFO', 'Chief Financial Officer',
    'COO', 'Chief Operating Officer',
    'CIO', 'Chief Information Officer',
    'CTO', 'Chief Technology Officer',
    'CAO', 'Chief Accounting Officer',
    'CCO', 'Chief Compliance Officer',
    
    # Executive Leadership
    'President', 'Vice President', 'EVP', 'SVP',
    'Executive Chairman', 'Chairman', 'Chair',
    'Executive Director', 'Managing Director',
    
    # Board
    'Director', 'Board Member', 'Independent Director',
    
    # Major Shareholders
    '10% Owner', '10%', 'Ten Percent Owner',
    'Major Shareholder', 'Controlling Shareholder',
    
    # Other Key Roles
    'Chief', 'Principal', 'Partner',
    'Founder', 'Co-Founder',
    'General Counsel', 'Secretary',
    'Treasurer', 'Controller',
]

# ============================================================================
# SMART DATE DETECTION - Knows when filings are available
# ============================================================================
def get_last_trading_day():
    """
    Returns the most recent trading day with FILINGS AVAILABLE.
    
    LOGIC:
    - SEC filings are posted after 9pm ET on the filing day
    - If run before 9pm ET: search previous trading day
    - If run after 9pm ET: search today's trading day
    - Weekends: always search Friday
    """
    eastern = pytz.timezone('US/Eastern')
    now = datetime.now(eastern)
    current_hour = now.hour
    current_minute = now.minute
    
    today = now.date()
    weekday = today.weekday()
    
    # Get the most recent trading day (Mon-Fri)
    if weekday == 5:  # Saturday
        last_trading = today - timedelta(days=1)  # Friday
    elif weekday == 6:  # Sunday
        last_trading = today - timedelta(days=2)  # Friday
    else:  # Monday-Friday (trading day)
        last_trading = today
    
    # Check if filings are available yet
    # Filings posted AFTER 9pm ET
    if current_hour < 21:  # Before 9pm ET
        # Use previous trading day (filings not posted yet)
        last_trading = last_trading - timedelta(days=1)
        # If yesterday was weekend, go back to Friday
        while last_trading.weekday() >= 5:
            last_trading = last_trading - timedelta(days=1)
    
    return last_trading

def get_current_time_info():
    """Returns formatted current time info for display."""
    eastern = pytz.timezone('US/Eastern')
    now = datetime.now(eastern)
    return now.strftime('%Y-%m-%d %H:%M %Z')

# ============================================================================
# POSITION VALIDATION
# ============================================================================
def is_valid_insider(position):
    """Check if the insider's position qualifies."""
    if not position or position == 'Unknown':
        return False
    
    position_upper = position.upper()
    
    for valid in VALID_POSITIONS:
        if valid.upper() in position_upper:
            return True
    
    return False

# ============================================================================
# MAIN BOT
# ============================================================================

def main():
    # Set identity for SEC EDGAR
    set_identity("benjamindannhoff2014@gmail.com")
    
    # Get the correct date to search
    last_trading_day = get_last_trading_day()
    date_str = last_trading_day.strftime("%Y-%m-%d")
    
    # Display status
    print("="*70)
    print("📊 INSIDER TRADING BOT - SMART DATE DETECTION")
    print("="*70)
    print(f"📅 Current Time: {get_current_time_info()}")
    print(f"📅 Searching filings for: {date_str} ({last_trading_day.strftime('%A')})")
    print("="*70)
    
    # Display active filters
    print("\n📋 ACTIVE FILTERS:")
    print(f"   🔥 Transaction Code: 'P' (Purchases only)")
    print(f"   🔥 Filing Delay: 0 - {MAX_FILING_DELAY} days")
    print(f"   🔥 Price: ≥ ${MIN_BOT_PRICE:.2f}")
    print(f"   🔥 Value: ≥ ${MIN_BOT_VALUE:,.0f}")
    print(f"   🔥 Position: CEO, CFO, COO, Director, 10% Owner, etc.")
    print("="*70)
    
    # Fetch filings
    print(f"\n📡 Fetching Form 4 filings for {date_str}...")
    
    try:
        filings = get_filings(form="4", filing_date=date_str)
        total = len(filings)
        print(f"✅ Got {total} filings\n")
    except Exception as e:
        print(f"❌ Error fetching filings: {e}")
        return
    
    if total == 0:
        print(f"⚠️ No filings found for {date_str}")
        print("   This could mean:")
        print("   - No filings were filed on this date")
        print("   - The date is incorrect")
        print("   - There's an issue with the SEC EDGAR connection")
        return
    
    # Initialize tracking variables
    purchase_count = 0
    filing_count = 0
    all_qualifying = []
    
    stats = {
        'total_filings': 0,
        'bad_ticker': 0,
        'bad_price': 0,
        'bad_delay': 0,
        'bad_transaction': 0,
        'bad_position': 0,
        'success': 0,
        'total_purchases': 0
    }
    
    # Process each filing
    for filing in filings:
        filing_count += 1
        stats['total_filings'] += 1
        
        # Show progress every 50 filings
        if filing_count % 50 == 0:
            print(f"📊 Progress: {filing_count}/{total} filings, {purchase_count} purchases found")
        
        try:
            obj = filing.obj()
            
            # ============================================================
            # Get ticker - must be valid
            # ============================================================
            ticker = "NO_TICKER"
            if hasattr(obj, 'issuer') and obj.issuer:
                if hasattr(obj.issuer, 'ticker'):
                    ticker = obj.issuer.ticker
            
            if not ticker or ticker == 'NO_TICKER' or ticker == 'nan' or ticker == 'None':
                stats['bad_ticker'] += 1
                continue
            
            # Get company name
            company = "Unknown"
            if hasattr(obj, 'issuer') and obj.issuer:
                if hasattr(obj.issuer, 'name'):
                    company = obj.issuer.name
            
            # ============================================================
            # Get transactions from non_derivative_table
            # ============================================================
            if not hasattr(obj, 'non_derivative_table'):
                continue
                
            table = obj.non_derivative_table
            if not table:
                continue
                
            if not hasattr(table, 'transactions'):
                continue
                
            trans_list = table.transactions
            if not trans_list:
                continue
            
            # ============================================================
            # Process ALL transactions in the filing
            # ============================================================
            for trans in trans_list:
                # --- Transaction Code: Must be 'P' (Purchase) ---
                code = trans.transaction_code if hasattr(trans, 'transaction_code') else ''
                code = code.upper().strip() if code else ''
                
                if not code or code != 'P':
                    stats['bad_transaction'] += 1
                    continue
                
                # --- Get shares and price ---
                shares = float(trans.shares) if hasattr(trans, 'shares') and trans.shares else 0
                price = float(trans.price) if hasattr(trans, 'price') and trans.price else 0
                
                # --- Price validation ---
                if price <= 0:
                    stats['bad_price'] += 1
                    continue
                
                if price < MIN_PRICE:
                    stats['bad_price'] += 1
                    continue
                
                if price > MAX_PRICE:
                    stats['bad_price'] += 1
                    continue
                
                if price < 0.50:
                    stats['bad_price'] += 1
                    continue
                
                # --- Get insider info ---
                insider_name = 'Unknown'
                position = 'Unknown'
                for owner in obj.reporting_owners:
                    if hasattr(owner, 'name'):
                        insider_name = owner.name
                    if hasattr(owner, 'position'):
                        position = owner.position
                    break
                
                # --- Position filter: Must be key insider ---
                if not is_valid_insider(position):
                    stats['bad_position'] += 1
                    continue
                
                # --- Calculate value ---
                value = shares * price
                
                # --- Filing delay check ---
                filing_delay = None
                try:
                    if hasattr(trans, 'date') and hasattr(obj, 'filing_date'):
                        trade_date = trans.date
                        filing_date = obj.filing_date
                        if trade_date and filing_date:
                            if hasattr(trade_date, 'date'):
                                trade_date_obj = trade_date.date() if hasattr(trade_date, 'date') else trade_date
                            else:
                                trade_date_obj = trade_date
                            
                            if hasattr(filing_date, 'date'):
                                filing_date_obj = filing_date.date() if hasattr(filing_date, 'date') else filing_date
                            else:
                                filing_date_obj = filing_date
                            
                            if not isinstance(trade_date_obj, datetime):
                                trade_date_obj = datetime.combine(trade_date_obj, datetime.min.time())
                            if not isinstance(filing_date_obj, datetime):
                                filing_date_obj = datetime.combine(filing_date_obj, datetime.min.time())
                            
                            filing_delay = (filing_date_obj - trade_date_obj).days
                            
                            if filing_delay < 0 or filing_delay > MAX_FILING_DELAY:
                                stats['bad_delay'] += 1
                                continue
                except:
                    pass
                
                # --- Record the purchase ---
                purchase_count += 1
                stats['success'] += 1
                
                # --- Check if qualifies for bot criteria ---
                if price >= MIN_BOT_PRICE and value >= MIN_BOT_VALUE:
                    delay_str = f" (delay: {filing_delay}d)" if filing_delay is not None else ""
                    print(f"✅ PURCHASE #{purchase_count}: {ticker} - {shares:,.0f} @ ${price:.2f} = ${value:,.2f} - {insider_name} ({position}){delay_str}")
                    print(f"   🎯 QUALIFIES!")
                    
                    all_qualifying.append({
                        'ticker': ticker,
                        'company': company,
                        'insider': insider_name,
                        'position': position,
                        'shares': shares,
                        'price': price,
                        'value': value,
                        'filing_delay': filing_delay,
                        'trade_date': trade_date if 'trade_date' in locals() else None,
                        'filing_date': filing_date if 'filing_date' in locals() else None,
                        'transaction_code': code
                    })
            
        except Exception as e:
            # Silently skip problematic filings
            continue
    
    # ============================================================================
    # DEDUPLICATE - Remove duplicates by ticker + insider
    # ============================================================================
    seen_trades = set()
    qualifying = []
    
    for trade in all_qualifying:
        key = (trade['ticker'], trade['insider'])
        if key not in seen_trades:
            seen_trades.add(key)
            qualifying.append(trade)
    
    # ============================================================================
    # DISPLAY RESULTS
    # ============================================================================
    
    print(f"\n{'='*70}")
    print("📊 DATA QUALITY STATISTICS")
    print("="*70)
    print(f"   Total filings processed: {stats['total_filings']:,}")
    print(f"   ✅ Valid purchases: {stats['success']:,}")
    print(f"   ❌ Bad ticker: {stats['bad_ticker']:,}")
    print(f"   ❌ Bad price: {stats['bad_price']:,}")
    print(f"   ❌ Filing delay > {MAX_FILING_DELAY} days: {stats['bad_delay']:,}")
    print(f"   ❌ Non-purchase transactions: {stats['bad_transaction']:,}")
    print(f"   ❌ Invalid position (filtered): {stats['bad_position']:,}")
    
    print(f"\n{'='*70}")
    print("📊 INSIDER BOT RESULTS")
    print("="*70)
    print(f"   Total purchases found (all types): {purchase_count}")
    print(f"   Qualifying purchases (≥ ${MIN_BOT_PRICE:.2f}, ≥ ${MIN_BOT_VALUE:,.0f}): {len(all_qualifying)}")
    print(f"   Qualifying after dedup: {len(qualifying)}")
    print("="*70)
    
    if qualifying:
        print("\n🎯 QUALIFYING PURCHASES (deduplicated by ticker + insider):")
        qualifying.sort(key=lambda x: x['value'], reverse=True)
        for q in qualifying:
            delay_str = f" (delay: {q['filing_delay']}d)" if q['filing_delay'] is not None else ""
            print(f"{q['ticker']:<8} ${q['value']:>14,.2f} - {q['shares']:,.0f} @ ${q['price']:.2f}{delay_str}")
            print(f"         {q['insider']} ({q['position']})")
            print()
    else:
        print("\n⚠️ No qualifying purchases found.")
    
    print("\n" + "="*70)
    print("✅ SCAN COMPLETE")
    print("="*70)
    
    return qualifying

# ============================================================================
# RUN THE BOT
# ============================================================================
if __name__ == "__main__":
    qualifying_trades = main()