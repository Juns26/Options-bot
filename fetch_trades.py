# fetch_trade.py
import os
from datetime import datetime, timedelta
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# TigerOpen imports
try:
    from tigeropen.tiger_open_config import TigerOpenClientConfig
    from tigeropen.trade.trade_client import TradeClient
    from tigeropen.common.consts import OrderStatus
    TIGER_AVAILABLE = True
except ImportError:
    TIGER_AVAILABLE = False
    print("Warning: TigerOpen modules not available. Trade fetching disabled.")

from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

# Step 1: Define the scope
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def init_google_sheets():
    """Initialize Google Sheets connection"""
    load_dotenv()
    
    creds = Credentials.from_service_account_info(
        {
            "type": "service_account",
            "project_id": os.getenv("GOOGLE_PROJECT_ID"),
            "private_key_id": os.getenv("GOOGLE_PRIVATE_KEY_ID"), 
            "private_key": os.getenv("GOOGLE_PRIVATE_KEY").replace('\\n', '\n'),
            "client_email": os.getenv("GOOGLE_CLIENT_EMAIL"),
            "client_id": os.getenv('GOOGLE_CLIENT_ID'),
            "auth_uri": os.getenv('GOOGLE_AUTH_URI'),
            "token_uri": os.getenv('GOOGLE_TOKEN_URI'),
            "auth_provider_x509_cert_url": os.getenv('GOOGLE_AUTH_PROVIDER_X509_CERT_URL'),
            "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_X509_CERT_URL'),
            "universe_domain": "googleapis.com"
        }, 
        scopes=SCOPES
    )
    
    client = gspread.authorize(creds)
    spreadsheet = client.open('Options Tracker')
    return spreadsheet

def fetch_orders_in_chunks(trade_client, start_date, end_date, chunk_days=30):
    """Fetch orders in chunks to handle API limits"""
    all_orders = []
    current_end = end_date
    
    while current_end > start_date:
        # Calculate chunk start
        chunk_start = current_end - timedelta(days=chunk_days)
        if chunk_start < start_date:
            chunk_start = start_date
            
        # Skip if chunk is invalid
        if chunk_start >= current_end:
            break
            
        # Convert to milliseconds
        start_ms = int(chunk_start.timestamp() * 1000)
        end_ms = int(current_end.timestamp() * 1000)
        
        print(f"Fetching orders from {chunk_start.strftime('%Y-%m-%d %H:%M:%S')} to {current_end.strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            orders = trade_client.get_orders(
                start_time=start_ms,
                end_time=end_ms,
                limit=300
            )
            
            if orders:
                all_orders.extend(orders)
                print(f" → Got {len(orders)} orders")
                
                # If we hit limit, reduce chunk size and retry
                if len(orders) == 300:
                    print(f" ⚠️ Hit limit, reducing chunk size from {chunk_days} to {chunk_days//2} days")
                    current_end = current_end
                    chunk_days = max(chunk_days // 2, 1)
                    continue
                    
            # Move to next chunk
            current_end = chunk_start
            
        except Exception as e:
            print(f" ❌ Error fetching chunk: {e}")
            current_end = chunk_start
    print(all_orders)
    return all_orders

def safe_parse_expiry(expiry):
    """Safely parse expiry date"""
    if expiry is None:
        return None
    if isinstance(expiry, datetime):
        return expiry.strftime("%Y-%m-%d")
    if isinstance(expiry, str):
        for fmt in ("%Y-%m-%d", "%Y%m%d", "%y%m%d"):
            try:
                return datetime.strptime(expiry, fmt).strftime("%Y-%m-%d")
            except:
                pass
        return expiry
    return None

def parse_tiger_order(order):
    """Parse TigerOpen Order object into clean dictionary"""
    def parse_timestamp(ts):
        return None if ts is None else datetime.fromtimestamp(ts / 1000, tz=SGT)

    contract = getattr(order, "contract", None)

    symbol = None
    expiry = None
    option_type = None
    strike = None
    raw_contract = None

    if contract:
        raw_contract = str(contract)
        symbol = getattr(contract, "symbol", None)
        option_type = getattr(contract, "right", None)
        strike = getattr(contract, "strike", None)
        
        expiry_raw = getattr(contract, "expiry", None)
        expiry = safe_parse_expiry(expiry_raw)
    parsed = {
        "action": order.action,
        "status": order.status,
        "order_type": order.order_type,
        "time_in_force": order.time_in_force,
        "quantity": order.quantity,
        "filled": order.filled,
        "avg_fill_price": order.avg_fill_price,
        "commission": order.commission,
        "gst": order.gst,
        "realized_pnl": order.realized_pnl,
        "order_time": parse_timestamp(order.order_time),
        "trade_time": parse_timestamp(order.trade_time),
        "update_time": parse_timestamp(order.update_time),
        "contract_legs": order.contract_legs,
        "combo_type": order.combo_type,
        "symbol": symbol,
        "expiry": expiry,
        "option_type": option_type,
        "strike": strike,
        "contract_raw": raw_contract,        
        "filled_cash_amount": order.filled_cash_amount,
    }

    return parsed

def compute_cash_flow(row):
    """Compute cash flow for a trade"""
    # Multi-leg trades already have correct sign from Tiger
    if "MLEG" in str(row["contract_raw"]):
        return row["avg_fill_price"] * row["quantity"] * 100 * -1

    # Single-leg trades: BUY = debit, SELL = credit
    if row["action"] == "BUY":
        return -abs(row["avg_fill_price"] * row["quantity"] * 100)
    else:  # SELL
        return abs(row["avg_fill_price"] * row["quantity"] * 100)

def calculate_collateral(row):
    """Calculate collateral for an option trade"""
    collateral = int(row["filled"]) * float(row["strike"]) * 100
    if row["action"] == "SELL" and row["option_type"] == "PUT":
        return collateral
    elif row["action"] == "BUY" and row["option_type"] == "PUT":
        return -collateral
    elif row["action"] == "SELL" and row["option_type"] == "CALL":
        return -collateral
    elif row["action"] == "BUY" and row["option_type"] == "CALL":
        return collateral
    return 0

def get_strategy(row):
    """Classify trading strategy"""
    # BPS, PMCC, CC, CSP 
    # 1. If VERTICAL and PUTS >> BPS
    if "VERTICAL" in str(row["combo_type"]) and row["option_type"] == "PUT":
        return "BPS"

    elif row["combo_type"] == "CUSTOM" and row["option_type"] == "PUT":
        return "BPS"
    
    # 2: after first filter, the remaining PUTS >> CSP
    elif row["option_type"] == "PUT":
        return "CSP"
    
    # 3. Unable to classify CALLS >> PMCC/CC/LEAPS
    elif row["option_type"] == "CALL":
        return "PMCC/CC/LEAPS"
    
    return "Other"

def fetch_and_update_trades(client_config):
    """Main function to fetch and update trades"""
    if not TIGER_AVAILABLE:
        print("Error: TigerOpen modules not available. Cannot fetch trades.")
        return False, "TigerOpen modules not available"
    
    try:
        # Initialize clients
        if not client_config:
            return False, "Failed to get TigerBroker configuration"
        
        trade_client = TradeClient(client_config)
        spreadsheet = init_google_sheets()
        sheet = spreadsheet.worksheet('Tiger Trade API Data')
        
        # Read last updated info
        last_updated_day = sheet.acell('B1').value
        last_updated_time = sheet.acell('C1').value
        
        if not last_updated_day or not last_updated_time:
            # If no last update, start from 30 days ago
            last_update = datetime.now(SGT) - timedelta(days=30)
        else:
            last_update = datetime.strptime(f'{last_updated_day} {last_updated_time}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=SGT)
        
        # Get row to add new data
        current_data = sheet.get_all_values()
        last_row = len(current_data) + 1
        
        # Extract new trade data
        print(f"Fetching trades since {last_update}")
        all_orders = fetch_orders_in_chunks(
            trade_client, 
            start_date=last_update, 
            end_date=datetime.now(SGT), 
            chunk_days=30
        )
        
        parsed_all = [parse_tiger_order(o) for o in all_orders]
        
        if not parsed_all:
            print("No new filled option orders since last update.")

            return True, "No new trades found"
        
        # Process orders
        df_allorders = pd.DataFrame(parsed_all)
        df_allorders = df_allorders.sort_values('order_time').reset_index(drop=True)
        
        # Filter option orders
        df_option_orders = df_allorders.loc[
            (df_allorders["status"] == OrderStatus.FILLED) 
            & (~df_allorders["contract_raw"].str.contains("STK", case=False, na=False))
        ].copy()
        
        # Filter stock orders
        df_stock_orders = df_allorders.loc[
            (df_allorders["status"] == OrderStatus.FILLED) 
            & (df_allorders["contract_raw"].str.contains("STK/USD", case=False, na=False))
            & (df_allorders["filled"] % 100 == 0)
        ].copy()
        
        # Process DataFrame
        final_df = pd.DataFrame()
        
        # Process option orders
        df_option_orders["premium"] = df_option_orders.apply(compute_cash_flow, axis=1)
        df_option_orders["fees"] = df_option_orders["commission"] + df_option_orders["gst"]
        
        # Single option contracts
        single_options = df_option_orders[~df_option_orders["contract_raw"].str.contains("MLEG", case=False, na=False)][
            ["action", "filled", "trade_time", "symbol", "expiry", "option_type", "combo_type", "strike", "premium", "fees"]
        ]
        final_df = pd.concat([final_df, single_options], ignore_index=True)
        
        # Multi-leg option contracts breakdown
        multi_leg_options_raw = df_option_orders[df_option_orders["contract_raw"].str.contains("MLEG", case=False, na=False)]
        for idx, row in multi_leg_options_raw.iterrows():
            legs = row['contract_legs']
            if legs is None:
                continue
            for leg in legs:
                leg_dict = {
                    "action": leg.action,
                    "filled": row["filled"] * leg.ratio,
                    "trade_time": row["trade_time"],
                    "symbol": leg.symbol,
                    "expiry": safe_parse_expiry(leg.expiry),
                    "option_type": leg.put_call,
                    "combo_type": row["combo_type"],
                    "strike": leg.strike,
                    "premium": leg.avg_filled_price * leg.ratio * leg.filled_quantity * leg.multiplier * (-1 if leg.action == "BUY" else 1),
                    "fees": row["fees"] / len(legs)
                }
                final_df = pd.concat([final_df, pd.DataFrame([leg_dict])], ignore_index=True)
        
        # Calculate net profit
        final_df["net_profit"] = final_df["premium"] - final_df["fees"]
        
        # Calculate collateral
        final_df["collateral"] = final_df.apply(calculate_collateral, axis=1)
        
        # Classify strategy
        final_df["strategy"] = final_df.apply(get_strategy, axis=1)
        
        # Append stock orders
        for idx, stock_row in df_stock_orders.iterrows():
            stock_dict = {
                "action": stock_row["action"],
                "filled": stock_row["filled"],
                "trade_time": stock_row["trade_time"],
                "symbol": stock_row["symbol"],
                "expiry": None,
                "option_type": "STK",
                "combo_type": None,
                "strike": stock_row["avg_fill_price"],
                "premium": stock_row["avg_fill_price"] * stock_row["filled"] * (-1 if stock_row["action"] == "BUY" else 1),
                "fees": stock_row["commission"] + stock_row["gst"],
                "net_profit": (stock_row["avg_fill_price"] * stock_row["filled"] * (-1 if stock_row["action"] == "BUY" else 1)) - (stock_row["commission"] + stock_row["gst"]),
                "collateral": 0,
                "strategy": "Stk"
            }
            final_df = pd.concat([final_df, pd.DataFrame([stock_dict])], ignore_index=True)
        
        # Force convert trade_time to stetring safely
        if "trade_time" in final_df.columns:
            final_df["trade_time"] = final_df["trade_time"].apply(
                lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) else None
            )

        # Update Google Sheet with new data
        if not final_df.empty:
            sheet.update(final_df.values.tolist(), f'A{last_row}')
            print(f"Added {len(final_df)} new trades to sheet")

            # Update last update timestamp
            sheet.update([[datetime.now(SGT).strftime('%Y-%m-%d')]], 'B1')
            sheet.update([[datetime.now(SGT).strftime('%H:%M:%S')]], 'C1')
        
        return True, f"Successfully added {len(final_df)} new trades"
        
    except Exception as e:
        print(f"Error fetching trades: {e}")
        return False, f"Error: {str(e)}"

def main():
    
    from dotenv import load_dotenv
    load_dotenv()

    def get_client_config():
        """
        https://quant.itigerup.com/#developer Get developer information
        """
        client_config = TigerOpenClientConfig()
        client_config.private_key = os.getenv("client_config.private_key")
        client_config.tiger_id = os.getenv("client_config.tiger_id")
        client_config.account = os.getenv("client_config.account")
        client_config.license = os.getenv("client_config.license")

        return client_config

    client_config = get_client_config()
    """Entry point for standalone execution"""
    success, message = fetch_and_update_trades(client_config)
    if success:
        print(f"✅ {message}")
    else:
        print(f"❌ {message}")

if __name__ == '__main__':
    main()