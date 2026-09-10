import streamlit as st
import time
import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException

# -----------------------------------------------------------------------------
# Configuration & State Initialization
# -----------------------------------------------------------------------------
st.set_page_config(page_title="GoldM Smart Exit Monitor", layout="wide")

load_dotenv()
API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")
REQUEST_TOKEN = os.getenv("KITE_REQUEST_TOKEN")

if not API_KEY or not API_SECRET:
    st.error("Missing API Key or Secret. Please check your .env file.")
    st.stop()

if 'logs' not in st.session_state:
    st.session_state.logs = []
if 'is_monitoring' not in st.session_state:
    st.session_state.is_monitoring = False

def log_message(msg: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state.logs.insert(0, f"[{timestamp}] [{level}] {msg}")
    st.session_state.logs = st.session_state.logs[:50]

# -----------------------------------------------------------------------------
# Zerodha Authentication (Persistent)
# -----------------------------------------------------------------------------
def get_kite_client(api_key, api_secret, request_token):
    kite = KiteConnect(api_key=api_key)
    token_file = "token.txt"
    
    if os.path.exists(token_file):
        with open(token_file, "r") as f:
            saved_token = f.read().strip()
            if saved_token:
                kite.set_access_token(saved_token)
                return kite, True

    if not request_token:
        log_message("No request_token in .env and token.txt not found.", "ERROR")
        return None, False

    try:
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]
        kite.set_access_token(access_token)
        with open(token_file, "w") as f:
            f.write(access_token)
        return kite, True
    except Exception as e:
        log_message(f"Auth failed: {str(e)}", "ERROR")
        return None, False

kite, is_authenticated = get_kite_client(API_KEY, API_SECRET, REQUEST_TOKEN)

if is_authenticated and "auth_logged" not in st.session_state:
    log_message("Authenticated with Zerodha successfully.", "SUCCESS")
    st.session_state.auth_logged = True

# -----------------------------------------------------------------------------
# Dashboard UI
# -----------------------------------------------------------------------------
st.title("📈 Smart SL-M / Target Order Monitor")
st.markdown("Parks a native Stop-Loss (SL-M) or Target (Limit) exit order directly on Zerodha. **Auto-disarms safely on trade exit.**")

with st.sidebar:
    st.header("Trade Settings")
    target_symbol = st.text_input("Trading Symbol", value="GOLDM26OCTFUT")
    exchange = st.selectbox("Exchange", ["MCX", "NFO", "NSE"], index=0)
    
    exit_price = st.number_input(
        "Exit Price (₹)", 
        min_value=1.0, 
        value=151800.0, 
        step=1.0,
        help="Target or Stop-Loss level. Automatically selects Limit or SL-M based on market position."
    )
    
    st.markdown("---")
    if st.button("Toggle Monitoring"):
        st.session_state.is_monitoring = not st.session_state.is_monitoring
        status = "STARTED" if st.session_state.is_monitoring else "STOPPED"
        log_message(f"Monitoring {status}. Desired Exit: {exit_price:.2f}", "SYSTEM")

    st.markdown(f"**Status:** {'🟢 RUNNING' if st.session_state.is_monitoring else '🔴 STOPPED'}")

col1, col2 = st.columns([6, 4])

# -----------------------------------------------------------------------------
# Position Monitoring & Smart Order Execution
# -----------------------------------------------------------------------------
if kite and st.session_state.is_monitoring:
    try:
        positions = kite.positions()
        net_positions = positions.get("net", [])
        # Find the active position where quantity is strictly NOT zero
        active_pos = next((p for p in net_positions if p['tradingsymbol'] == target_symbol and p['quantity'] != 0), None)
        
        with col1:
            st.subheader("Current Open Positions")
            if net_positions:
                df = pd.DataFrame(net_positions)[['tradingsymbol', 'product', 'quantity', 'average_price', 'last_price', 'pnl']]
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No open positions found.")
                
        # SAFETY CHECK 1: If there is no open position, disarm the bot immediately
        if not active_pos:
            log_message(f"No active position for {target_symbol}. Auto-disarming bot to prevent ghost orders.", "SYSTEM")
            st.session_state.is_monitoring = False
            st.rerun()
            
        else:
            qty = active_pos['quantity']
            avg_price = active_pos['average_price']
            product_type = active_pos['product']
            
            # Fetch LTP
            instrument = f"{exchange}:{target_symbol}"
            quote_data = kite.quote([instrument])
            ltp = quote_data[instrument]['last_price']
            
            # Determine Direction & Order Type
            is_long = qty > 0
            exit_action = kite.TRANSACTION_TYPE_SELL if is_long else kite.TRANSACTION_TYPE_BUY
            
            if is_long:
                is_target = exit_price >= ltp
            else:
                is_target = exit_price <= ltp
                
            required_order_type = kite.ORDER_TYPE_LIMIT if is_target else kite.ORDER_TYPE_SLM
            order_category = "TARGET (LIMIT)" if is_target else "STOP-LOSS (SL-M)"
            
            # Find active pending exit orders for this instrument
            all_orders = kite.orders()
            active_exit_order = next((o for o in all_orders 
                                     if o['tradingsymbol'] == target_symbol 
                                     and o['status'] in ['TRIGGER PENDING', 'OPEN']), None)
            
            # Place new order if none exists
            if not active_exit_order:
                log_message(f"Placing {exit_action} {order_category} exit order at {exit_price:.2f} (LTP: {ltp})...", "SYSTEM")
                try:
                    order_params = {
                        "variety": kite.VARIETY_REGULAR,
                        "exchange": exchange,
                        "tradingsymbol": target_symbol,
                        "transaction_type": exit_action,
                        "quantity": abs(qty),
                        "product": product_type,
                        "order_type": required_order_type
                    }
                    if required_order_type == kite.ORDER_TYPE_SLM:
                        order_params["trigger_price"] = exit_price
                        order_params["market_protection"] = -1
                    else:
                        order_params["price"] = exit_price

                    new_order_id = kite.place_order(**order_params)
                    log_message(f"✅ {order_category} Order Placed. ID: {new_order_id} at {exit_price:.2f}", "SUCCESS")
                
                except KiteException as e:
                    # SAFETY CHECK 2: If the order gets rejected (e.g., Margin shortfall), disarm the bot immediately
                    log_message(f"❌ Order Placement Failed: {e}", "ERROR")
                    log_message("Auto-disarming bot due to API rejection.", "SYSTEM")
                    st.session_state.is_monitoring = False
            
            # If order exists, modify it if the user changed the input slider
            else:
                current_order_type = active_exit_order['order_type']
                current_price = active_exit_order['price'] if current_order_type == kite.ORDER_TYPE_LIMIT else active_exit_order['trigger_price']
                order_id = active_exit_order['order_id']
                
                log_message(f"Tracking: LTP = {ltp} | Open Order: {current_order_type} at {current_price} | Desired Exit: {exit_price:.2f}")
                
                # Treat SL and SL-M as equivalent so we don't trigger an infinite cancel/replace loop
                needs_switch = False
                if required_order_type == kite.ORDER_TYPE_LIMIT and current_order_type != kite.ORDER_TYPE_LIMIT:
                    needs_switch = True
                elif required_order_type == kite.ORDER_TYPE_SLM and current_order_type not in [kite.ORDER_TYPE_SLM, kite.ORDER_TYPE_SL]:
                    needs_switch = True
                
                if needs_switch:
                    log_message(f"Switching order type from {current_order_type} to {required_order_type}. Replacing order...", "SYSTEM")
                    try:
                        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
                    except Exception as e:
                        log_message(f"❌ Cancel Failed: {e}", "ERROR")
                        st.session_state.is_monitoring = False
                
                elif float(current_price) != float(exit_price):
                    log_message(f"Modifying {current_order_type} order to {exit_price:.2f}...", "SYSTEM")
                    try:
                        modify_params = {
                            "variety": kite.VARIETY_REGULAR,
                            "order_id": order_id,
                            "order_type": current_order_type
                        }
                        if current_order_type in [kite.ORDER_TYPE_SLM, kite.ORDER_TYPE_SL]:
                            modify_params["trigger_price"] = exit_price
                        else:
                            modify_params["price"] = exit_price
                            
                        kite.modify_order(**modify_params)
                        log_message(f"✅ Order {order_id} successfully modified to {exit_price:.2f}", "SUCCESS")
                    
                    except KiteException as e:
                        # SAFETY CHECK 3: If order modification fails, disarm the bot
                        log_message(f"❌ Modification Failed: {e}", "ERROR")
                        st.session_state.is_monitoring = False
            
    except Exception as e:
        log_message(f"System Error: {e}", "ERROR")
        st.session_state.is_monitoring = False

# -----------------------------------------------------------------------------
# Display Logs & Auto-refresh
# -----------------------------------------------------------------------------
with col2:
    st.subheader("System Logs")
    log_box = st.container(height=500)
    for log in st.session_state.logs:
        if "[ERROR]" in log or "[WARNING]" in log:
            log_box.error(log)
        elif "[SUCCESS]" in log:
            log_box.success(log)
        elif "[SYSTEM]" in log:
            log_box.info(log)
        else:
            log_box.text(log)

# Auto-refresh cycle
if st.session_state.is_monitoring:
    time.sleep(5)
    st.rerun()