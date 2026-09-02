# ============================================================================
# INSIDER TRADING BOT v2.1 - FRACTIONAL SHARES + NONE FILTER
# ============================================================================
import os
import time
import json
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from edgartools import get_filings
import pytz

# ============================================================================
# CONFIGURATION
# ============================================================================
HOLDING_PERIOD = 25
STOP_LOSS_PCT = 0.23
PROFIT_TRIGGER = 0.20
SELL_PERCENTAGE = 0.55
MIN_HOLD_DAYS = 1
KELLY_FRACTION = 0.10
ROLLING_WINDOW = 200
MAX_FILING_DELAY = 30
MIN_PRICE = 1.00
MAX_PRICE = 10000
MIN_BOT_PRICE = 10.00
MIN_BOT_VALUE = 500000

VALID_POSITIONS = [
    'CEO', 'Chief Executive Officer', 'CFO', 'Chief Financial Officer',
    'COO', 'Chief Operating Officer', 'Director', 'Board Member',
    '10% Owner', 'Ten Percent Owner', 'President', 'Chairman',
    'EVP', 'SVP', 'Chief', 'Principal', 'Partner', 'Founder'
]

# ============================================================================
# SETUP LOGGING
# ============================================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

load_dotenv()
trading_client = TradingClient(
    os.getenv('ALPACA_API_KEY'),
    os.getenv('ALPACA_SECRET_KEY'),
    paper=True
)

# ============================================================================
# BOT STATE
# ============================================================================
class BotState:
    def __init__(self, state_file='bot_state.json'):
        self.state_file = state_file
        self.trade_history = []
        self.open_positions = []
        self.load()
    
    def load(self):
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
                self.trade_history = data.get('trade_history', [])
                self.open_positions = data.get('open_positions', [])
            logger.info(f"Loaded: {len(self.trade_history)} trades, {len(self.open_positions)} open")
        except FileNotFoundError:
            logger.info("Fresh start")
    
    def save(self):
        with open(self.state_file, 'w') as f:
            json.dump({
                'trade_history': self.trade_history,
                'open_positions': self.open_positions
            }, f, indent=2)
        logger.info("Saved")
    
    def add_open_position(self, pos):
        self.open_positions.append(pos)
        self.save()

state = BotState()

# ============================================================================
# POSITION SIZING
# ============================================================================
def calculate_position_size(cash, price, kelly):
    return round((cash * kelly) / price, 3)

def get_stop_price(entry, highest):
    if highest > entry:
        return highest * (1 - STOP_LOSS_PCT)
    return entry * (1 - STOP_LOSS_PCT)

# ============================================================================
# SEC FILINGS
# ============================================================================
def get_last_trading_day():
    now = datetime.now(pytz.timezone('US/Eastern'))
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        target = now - timedelta(days=1)
    else:
        target = now
    while target.weekday() >= 5:
        target = target - timedelta(days=1)
    return target

def fetch_filings(target_date):
    date_str = target_date.strftime('%Y-%m-%d')
    logger.info(f"Fetching filings for {date_str}")
    try:
        filings = get_filings(form="4", filing_date=date_str)
        return filings
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return []

def is_valid_insider(position):
    if not position:
        return False
    pos_upper = position.upper()
    return any(v.upper() in pos_upper for v in VALID_POSITIONS)

def process_filings(filings, target_date):
    qualifying = []
    stats = {'total': 0, 'bad_transaction': 0, 'bad_price': 0, 'bad_position': 0, 'bad_delay': 0, 'bad_ticker': 0, 'success': 0}
    
    for filing in filings:
        stats['total'] += 1
        try:
            for tx in filing.transactions:
                if tx.get('transaction_code', '').upper() != 'P':
                    stats['bad_transaction'] += 1
                    continue
                
                try:
                    price = float(tx.get('price', 0))
                except:
                    stats['bad_price'] += 1
                    continue
                
                if price < MIN_PRICE or price > MAX_PRICE:
                    stats['bad_price'] += 1
                    continue
                
                if not is_valid_insider(tx.get('position', '')):
                    stats['bad_position'] += 1
                    continue
                
                trade_date = tx.get('trade_date')
                if not trade_date:
                    stats['bad_delay'] += 1
                    continue
                
                delay = (filing.date - trade_date).days
                if delay < 0 or delay > MAX_FILING_DELAY:
                    stats['bad_delay'] += 1
                    continue
                
                value = price * tx.get('shares', 0)
                if price >= MIN_BOT_PRICE and value >= MIN_BOT_VALUE:
                    ticker = filing.company.ticker
                    if not ticker or ticker.upper() == 'NONE':
                        stats['bad_ticker'] += 1
                        continue
                    
                    qualifying.append({
                        'ticker': ticker,
                        'price': price,
                        'shares': tx.get('shares', 0),
                        'value': value,
                        'insider': tx.get('insider_name', 'Unknown'),
                        'position': tx.get('position', ''),
                        'trade_date': trade_date,
                        'filing_date': filing.date,
                        'delay': delay
                    })
                    stats['success'] += 1
        except Exception as e:
            continue
    
    seen = set()
    unique = []
    for t in qualifying:
        key = (t['ticker'], t['insider'])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    logger.info(f"Processed {stats['total']} filings, found {len(unique)} qualifying trades")
    return unique

# ============================================================================
# PLACE ORDERS
# ============================================================================
def place_buy_order(ticker, shares, price, trade_info):
    try:
        logger.info(f"BUY {shares} shares of {ticker} @ ${price:.2f}")
        account = trading_client.get_account()
        cash = float(account.cash)
        
        if shares * price > cash:
            max_shares = round(cash / price, 3)
            if max_shares < 0.001:
                return None
            shares = max_shares
        
        order = MarketOrderRequest(
            symbol=ticker,
            qty=shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        result = trading_client.submit_order(order)
        logger.info(f"✅ BUY order placed: {result.id}")
        
        state.add_open_position({
            'id': result.id,
            'ticker': ticker,
            'entry_price': price,
            'shares': shares,
            'entry_date': datetime.now().date(),
            'highest_price': price,
            'stop_price': price * (1 - STOP_LOSS_PCT),
            'partial_sold': False,
            'kelly_used': trade_info.get('kelly_used', 0.10)
        })
        return result
    except Exception as e:
        logger.error(f"Buy error: {e}")
        return None

# ============================================================================
# MAIN
# ============================================================================
def run_bot():
    logger.info("="*60)
    logger.info("🚀 INSIDER TRADING BOT v2.1")
    logger.info("="*60)
    
    kelly = KELLY_FRACTION
    logger.info(f"Kelly: {kelly*100:.1f}%")
    
    try:
        account = trading_client.get_account()
        cash = float(account.cash)
        logger.info(f"Cash: ${cash:.2f}")
    except Exception as e:
        logger.error(f"Account error: {e}")
        return
    
    if cash < 1:
        logger.warning(f"Cash ${cash:.2f} < $1 - skipping")
        return
    
    target_date = get_last_trading_day()
    logger.info(f"Filings for: {target_date.strftime('%Y-%m-%d')}")
    
    filings = fetch_filings(target_date)
    if not filings:
        logger.info("No filings")
        return
    
    qualifying = process_filings(filings, target_date)
    if not qualifying:
        logger.info("No qualifying trades")
        return
    
    logger.info(f"\n📈 Placing trades for {len(qualifying)} signals...")
    
    for trade in qualifying[:5]:
        shares = calculate_position_size(cash, trade['price'], kelly)
        if shares < 0.001:
            logger.info(f"Skipping {trade['ticker']} - too small")
            continue
        
        result = place_buy_order(trade['ticker'], shares, trade['price'], trade)
        if result:
            cash -= shares * trade['price']
            logger.info(f"✅ {trade['ticker']}: {shares} shares @ ${trade['price']:.2f}")
        else:
            logger.error(f"❌ {trade['ticker']}: Order failed")
        time.sleep(1)
    
    state.save()
    logger.info("✅ Bot complete")

if __name__ == "__main__":
    run_bot()