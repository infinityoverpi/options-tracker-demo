# api_client.py
import pandas as pd
import asyncio
import sqlite3
import requests
import streamlit as st
import re
import logging
import warnings
from datetime import timedelta, date
from tastytrade import Session, Account

# Suppress SnapTrade API Deprecation console spam
logging.getLogger("snaptrade_client").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", module="snaptrade_client")

from data_engine import parse_occ_symbol, identify_strategy, format_inventory, finalize_chain

# =====================================================================
# THE NUCLEAR FIX: Bumping to V17 completely abandons the corrupted
# database file and forces a 100% clean rebuild from Tastytrade!
# =====================================================================
DB_PATH = ".streamlit/tasty_cache_v17.db"

try:
    import yfinance as yf
    HAS_YFINANCE = True
except ImportError:
    HAS_YFINANCE = False

try:
    from snaptrade_client import SnapTrade
    HAS_SNAPTRADE = True
except ImportError:
    HAS_SNAPTRADE = False

def safe_dict(obj):
    if isinstance(obj, dict): return obj
    if hasattr(obj, 'to_dict'):
        try: return obj.to_dict()
        except: pass
    if hasattr(obj, 'attribute_map'):
        return {k: getattr(obj, k) for k in obj.attribute_map.keys() if hasattr(obj, k)}
    try: return vars(obj)
    except: return {}

def save_daily_balances(updates_dict, source_label):
    if not updates_dict: return
    
    today_str = pd.Timestamp.now().strftime('%Y-%m-%d')
    db_conn = sqlite3.connect(DB_PATH)
    c = db_conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS daily_balances 
                 (date TEXT, account_name TEXT, balance REAL, source TEXT)''')
                 
    for acc_name, bal in updates_dict.items():
        c.execute("SELECT COUNT(*) FROM daily_balances WHERE date=? AND account_name=?", (today_str, acc_name))
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO daily_balances VALUES (?, ?, ?, ?)", (today_str, acc_name, bal, source_label))
        else:
            c.execute("UPDATE daily_balances SET balance=?, source=? WHERE date=? AND account_name=?", (bal, source_label, today_str, acc_name))
            
    db_conn.commit()
    db_conn.close()

def sync_external_apis(_secrets):
    updates = {}
    
    plaid_client = _secrets.get("PLAID_CLIENT_ID")
    plaid_secret = _secrets.get("PLAID_SECRET")
    
    if plaid_client and plaid_secret:
        plaid_url = "https://production.plaid.com/accounts/balance/get"
        ally_token = _secrets.get("PLAID_ALLY_ACCESS_TOKEN")
        if ally_token:
            try:
                res = requests.post(plaid_url, json={"client_id": plaid_client, "secret": plaid_secret, "access_token": ally_token}).json()
                if "accounts" in res:
                    for a in res['accounts']:
                        acc_name = f"Ally - {a.get('name', 'Account')} ({a.get('mask', '****')})"
                        updates[acc_name] = a['balances'].get('current', 0)
            except Exception: pass
            
        voya_token = _secrets.get("PLAID_VOYA_ACCESS_TOKEN")
        if voya_token:
            try:
                res = requests.post(plaid_url, json={"client_id": plaid_client, "secret": plaid_secret, "access_token": voya_token}).json()
                if "accounts" in res:
                    for a in res['accounts']:
                        acc_name = f"Voya - {a.get('name', 'Account')} ({a.get('mask', '****')})"
                        updates[acc_name] = a['balances'].get('current', 0)
            except Exception: pass

    if HAS_SNAPTRADE and _secrets.get("SNAPTRADE_CLIENT_ID") and _secrets.get("SNAPTRADE_USER_SECRET"):
        try:
            snaptrade = SnapTrade(
                client_id=_secrets.get("SNAPTRADE_CLIENT_ID"),
                consumer_key=_secrets.get("SNAPTRADE_CONSUMER_KEY")
            )
            u_id = _secrets.get("SNAPTRADE_USER_ID", "dashboard_admin")
            u_sec = _secrets.get("SNAPTRADE_USER_SECRET")
            
            accs = snaptrade.account_information.list_user_accounts(user_id=u_id, user_secret=u_sec)
            accs_data = getattr(accs, 'body', accs)
            acc_list = accs_data if isinstance(accs_data, list) else [accs_data]
            
            for acc in acc_list:
                try:
                    a_dict = safe_dict(acc)
                    acc_id = a_dict.get('id')
                    if not acc_id: continue
                    
                    raw_name = a_dict.get('name') or a_dict.get('nickname')
                    acc_name = str(raw_name).strip() if raw_name else "Account"
                    
                    raw_num = a_dict.get('number')
                    acc_num = str(raw_num).strip() if raw_num else "****"
                    if len(acc_num) > 4 and acc_num != "****":
                        acc_num = acc_num[-4:]
                        
                    acc_label = f"Fidelity - {acc_name} ({acc_num})"
                    total_val = None
                    
                    # TIER 1: Safely unpack the SDK object to grab the True Fidelity Balance
                    bal_obj = a_dict.get('balance')
                    if bal_obj:
                        bal_dict = safe_dict(bal_obj)
                        total_obj = bal_dict.get('total')
                        if total_obj:
                            total_dict = safe_dict(total_obj)
                            val = total_dict.get('amount')
                            if val is not None:
                                try: total_val = float(val)
                                except Exception: pass
                    
                    # TIER 2: Deep Account Details (Great for 529s)
                    if total_val is None:
                        try:
                            details_res = snaptrade.account_information.get_user_account_details(user_id=u_id, user_secret=u_sec, account_id=acc_id)
                            details_data = getattr(details_res, 'body', details_res)
                            d_dict = safe_dict(details_data)
                            
                            bal_obj = d_dict.get('balance')
                            if bal_obj:
                                bal_dict = safe_dict(bal_obj)
                                total_obj = bal_dict.get('total')
                                if total_obj:
                                    total_dict = safe_dict(total_obj)
                                    val = total_dict.get('amount')
                                    if val is not None:
                                        try: total_val = float(val)
                                        except: pass
                        except Exception: pass

                    # TIER 3: Legacy Balance Endpoint
                    if total_val is None:
                        try:
                            bal_res = snaptrade.account_information.get_user_account_balance(user_id=u_id, user_secret=u_sec, account_id=acc_id)
                            bal_data = getattr(bal_res, 'body', bal_res)
                            bal_list = bal_data if isinstance(bal_data, list) else [bal_data]
                            for b in bal_list:
                                b_dict = safe_dict(b)
                                total_obj = b_dict.get('total')
                                if total_obj:
                                    total_dict = safe_dict(total_obj)
                                    val = total_dict.get('amount')
                                    if val is not None:
                                        try:
                                            total_val = float(val)
                                            break
                                        except: pass
                        except Exception: pass

                    # TIER 4: Synthetic Cash + Equity Calculation
                    if total_val is None:
                        cash_val = 0.0
                        equity_val = 0.0
                        
                        try:
                            bal_res = snaptrade.account_information.get_user_account_balance(user_id=u_id, user_secret=u_sec, account_id=acc_id)
                            bal_data = getattr(bal_res, 'body', bal_res)
                            bal_list = bal_data if isinstance(bal_data, list) else [bal_data]
                            for b in bal_list:
                                b_dict = safe_dict(b)
                                cash_val = float(b_dict.get('cash', 0.0))
                                break
                        except Exception: pass
                        
                        try:
                            if hasattr(snaptrade.account_information, 'get_all_account_positions'):
                                pos_res = snaptrade.account_information.get_all_account_positions(user_id=u_id, user_secret=u_sec, account_id=acc_id)
                            else:
                                pos_res = snaptrade.account_information.get_user_account_positions(user_id=u_id, user_secret=u_sec, account_id=acc_id)
                            
                            pos_data = getattr(pos_res, 'body', pos_res)
                            pos_list = pos_data if isinstance(pos_data, list) else [pos_data]
                            for p in pos_list:
                                p_dict = safe_dict(p)
                                
                                sym_obj = p_dict.get('symbol', {})
                                sym_str = ""
                                if isinstance(sym_obj, dict):
                                    sym_str = sym_obj.get('symbol', '') or sym_obj.get('ticker', '') or sym_obj.get('raw_symbol', '')
                                elif isinstance(sym_obj, str):
                                    sym_str = sym_obj
                                sym_str = str(sym_str).upper()
                                
                                units = float(p_dict.get('units', 0.0) or 0.0)
                                price = float(p_dict.get('price', 0.0) or 0.0)
                                pos_val = units * price
                                
                                if sym_str in ["SPAXX", "FDRXX", "FCASH", "FZFXX", "QZQQX"]:
                                    continue
                                
                                if cash_val > 10.0 and abs(pos_val - cash_val) < 1.0:
                                    continue
                                    
                                equity_val += pos_val
                        except Exception: pass
                        
                        total_val = cash_val + equity_val
                    
                    if total_val is not None and total_val > 0:
                        updates[acc_label] = total_val
                except Exception: pass
        except Exception: pass

    save_daily_balances(updates, "External API")
    return True

def fetch_unified_balances():
    conn = sqlite3.connect(DB_PATH)
    try:
        df_bals = pd.read_sql("SELECT * FROM daily_balances", conn, parse_dates=['date'])
    except Exception:
        df_bals = pd.DataFrame(columns=['date', 'account_name', 'balance', 'source'])
    conn.close()
    return df_bals

def load_manual_balances():
    conn = sqlite3.connect(DB_PATH)
    try:
        df_man = pd.read_sql("SELECT * FROM manual_balances", conn, parse_dates=['date'])
    except Exception:
        df_man = pd.DataFrame(columns=['date', 'account_name', 'balance', 'contributions'])
    conn.close()
    return df_man

@st.cache_data(ttl=3600, show_spinner=False)
def get_spy_return(start_date, end_date):
    if not HAS_YFINANCE: return 0.0
    try:
        start_str = (start_date - pd.Timedelta(days=7)).strftime('%Y-%m-%d')
        end_str = (end_date + pd.Timedelta(days=3)).strftime('%Y-%m-%d')
        hist = yf.Ticker("SPY").history(start=start_str, end=end_str)
        if len(hist) == 0: return 0.0
        hist.index = hist.index.tz_localize(None).normalize()
        mask = (hist.index >= start_date.normalize()) & (hist.index <= end_date.normalize())
        period_hist = hist.loc[mask]
        if len(period_hist) == 0: return 0.0
        first_price = period_hist['Open'].iloc[0]
        last_price = period_hist['Close'].iloc[-1]
        return ((last_price - first_price) / first_price) * 100
    except Exception:
        return 0.0

async def get_all_accounts_history(secret, token):
    try:
        session = Session(secret, token, is_test=False)
        accounts = await Account.get(session)
        if not accounts: return None, "No accounts found.", None, None, None
        
        conn = sqlite3.connect(DB_PATH)
        
        try:
            df_cached_tx = pd.read_sql("SELECT * FROM transactions", conn)
            # Enforce string keys on cache baseline load immediately
            df_cached_tx['tx_id'] = df_cached_tx['tx_id'].astype(str)
            df_cached_tx['complex_id'] = df_cached_tx['complex_id'].astype(str)
            df_cached_tx['asset_token'] = df_cached_tx['asset_token'].astype(str)
            df_cached_tx['date'] = pd.to_datetime(df_cached_tx['date'])
            
            if 'force_close' in df_cached_tx.columns:
                df_cached_tx['force_close'] = df_cached_tx['force_close'].apply(lambda x: True if str(x).lower() in ['true', '1'] else False)
                
            if not df_cached_tx.empty:
                last_date = df_cached_tx['date'].max()
                fetch_start = (last_date - timedelta(days=2)).date()
            else:
                fetch_start = date(2020, 1, 1)
        except Exception:
            df_cached_tx = pd.DataFrame()
            fetch_start = date(2020, 1, 1)
            
        try:
            df_cached_deps = pd.read_sql("SELECT * FROM deposits", conn)
            df_cached_deps['tx_id'] = df_cached_deps['tx_id'].astype(str)
        except Exception:
            df_cached_deps = pd.DataFrame()
            
        try:
            df_bpu = pd.read_sql("SELECT * FROM bpu_cache", conn)
            bpu_cache = dict(zip(df_bpu['account_id'], df_bpu['bpu_val']))
        except Exception:
            bpu_cache = {}
            
        all_tx = []
        all_deposits = []
        account_balances = {}
        maintenance_reqs = {}
        
        for acc in accounts:
            nickname = str(getattr(acc, 'nickname', '')).strip()
            acc_num = str(getattr(acc, 'account_number', '')).strip()
            acc_label = f"{nickname} - {acc_num}" if nickname else acc_num
            
            try:
                bals = await acc.get_balances(session)
                bal_obj = bals[0] if isinstance(bals, list) else bals
                
                account_balances[acc_label] = float(getattr(bal_obj, 'net_liquidating_value', 0.0) or 0.0)
                raw_maint = float(getattr(bal_obj, 'maintenance_requirement', 0.0) or 0.0)
                
                if raw_maint > 0.01:
                    maintenance_reqs[acc_label] = raw_maint
                    bpu_cache[acc_label] = raw_maint
                else:
                    maintenance_reqs[acc_label] = bpu_cache.get(acc_label, 0.0)
            except Exception:
                account_balances[acc_label] = 0.0
                maintenance_reqs[acc_label] = 0.0
            
            page_offset = 0
            
            while True:
                history_page = await acc.get_history(session, start_date=fetch_start, page_offset=page_offset)
                if not history_page: break
                
                for tx in history_page:
                    t_type = getattr(tx, 'transaction_type', None)
                    t_type_str = str(t_type.value) if hasattr(t_type, 'value') else str(t_type)
                    
                    sub_type = getattr(tx, 'transaction_sub_type', None)
                    sub_type_str = str(sub_type.value) if hasattr(sub_type, 'value') else str(sub_type)
                    
                    desc = str(getattr(tx, 'description', '')).lower()
                    tx_id_str = str(getattr(tx, 'id', '')).strip()
                    
                    if t_type_str == 'Money Movement':
                        val_raw = float(getattr(tx, 'value', 0.0) or 0.0)
                        if sub_type_str == 'Deposit':
                            all_deposits.append({"tx_id": tx_id_str, "account_id": acc_label, "net_deposit": abs(val_raw)})
                        elif sub_type_str == 'Withdrawal':
                            all_deposits.append({"tx_id": tx_id_str, "account_id": acc_label, "net_deposit": -abs(val_raw)})
                        continue 

                    if not t_type_str or t_type_str not in ['Trade', 'Receive Deliver']: continue

                    underlying = getattr(tx, 'underlying_symbol', None)
                    if underlying == "" or underlying is None:
                        underlying = getattr(tx, 'symbol', None)
                    if not underlying: continue

                    qty_raw = float(tx.quantity) if getattr(tx, 'quantity', None) is not None else 0.0
                    val_raw = float(tx.value) if getattr(tx, 'value', None) is not None else 0.0
                    abs_qty = abs(qty_raw)
                    
                    is_removal_event = False
                    if abs_qty == 0:
                        is_assignment = "assignment" in desc
                        is_expiry = "expir" in desc
                        is_removal = "removal" in desc
                        is_settlement = "settlement" in desc
                        is_removal_event = (is_assignment or is_expiry or is_removal or is_settlement)
                        
                        if " @ " in desc:
                            match = re.search(r'([\d,.]+)\s+(?:[A-Za-z]+\s+)*@', desc)
                            if match: abs_qty = float(match.group(1).replace(',', ''))
                        
                        if abs_qty == 0 and val_raw == 0 and not is_removal_event: 
                            continue

                    inst_type = getattr(tx, 'instrument_type', 'Unknown')
                    inst_type_str = str(inst_type.value) if hasattr(inst_type, 'value') else str(inst_type)
                    asset_token = getattr(tx, 'symbol', underlying) if 'Option' in inst_type_str else f"{underlying}_SHARES"
                    dt_obj = pd.to_datetime(tx.executed_at).tz_convert('US/Eastern').tz_localize(None)

                    c_id_raw = getattr(tx, 'complex_order_id', None) or getattr(tx, 'order_id', None)
                    c_id = str(c_id_raw).strip() if c_id_raw is not None else ""
                    
                    if not c_id or c_id.lower() == "none" or c_id.lower() == "nan":
                        c_id = f"fallback_{tx_id_str}"

                    if t_type_str == 'Receive Deliver' and 'Option' not in inst_type_str:
                        c_id = f"delivery_{dt_obj.strftime('%Y%m%d%H%M%S')}_{asset_token}"
                    
                    effect = getattr(tx, 'value_effect', None)
                    effect_str = str(effect.value).upper() if hasattr(effect, 'value') else str(effect).upper()
                    
                    if effect_str == 'CREDIT':
                        signed_val, signed_qty = abs(val_raw), -abs_qty
                    elif effect_str == 'DEBIT':
                        signed_val, signed_qty = -abs(val_raw), abs_qty
                    else:
                        raw_action = getattr(tx, 'action', '')
                        raw_action_str = str(raw_action.value).upper() if hasattr(raw_action, 'value') else str(raw_action).upper()
                        is_buy = any(word in raw_action_str for word in ["BUY", "RECEIVE"])
                        signed_val = -abs(val_raw) if is_buy else abs(val_raw)
                        signed_qty = abs_qty if is_buy else -abs_qty

                    comm = abs(float(getattr(tx, 'commission', 0.0) or 0.0))
                    clear = abs(float(getattr(tx, 'clearing_fees', 0.0) or 0.0))
                    reg = abs(float(getattr(tx, 'regulatory_fees', 0.0) or 0.0))
                    prop = abs(float(getattr(tx, 'proprietary_index_option_fees', 0.0) or 0.0))
                    ext = abs(float(getattr(tx, 'ext_exchange_fee', 0.0) or 0.0))
                    
                    all_tx.append({
                        "tx_id": str(tx_id_str), "account_id": str(acc_label), "date": dt_obj, "complex_id": str(c_id), "underlying": str(underlying),
                        "asset_token": str(asset_token), "signed_qty": signed_qty, "signed_val": signed_val, 
                        "commissions": comm, "other_fees": clear + prop + reg + ext, "force_close": (abs_qty == 0 and is_removal_event)
                    })
                
                if len(history_page) < 250: break
                page_offset += 1

        df_new_tx = pd.DataFrame(all_tx)
        
        # Lock types on incoming data to match the database
        if not df_new_tx.empty:
            df_new_tx['tx_id'] = df_new_tx['tx_id'].astype(str)
            df_new_tx['complex_id'] = df_new_tx['complex_id'].astype(str)
            df_new_tx['asset_token'] = df_new_tx['asset_token'].astype(str)
            
        df_new_deps = pd.DataFrame(all_deposits)
        
        if not df_new_deps.empty:
            df_new_deps['tx_id'] = df_new_deps['tx_id'].astype(str)
        
        # Merge old tables and new fetches while strictly preserving identity attributes
        if not df_cached_tx.empty and not df_new_tx.empty:
            df_combined_tx = pd.concat([df_cached_tx, df_new_tx], ignore_index=True)
            df_combined_tx['tx_id'] = df_combined_tx['tx_id'].astype(str)
            df_combined_tx = df_combined_tx.drop_duplicates(subset=['tx_id'], keep='last')
        elif not df_new_tx.empty:
            df_combined_tx = df_new_tx
        else:
            df_combined_tx = df_cached_tx
            
        if not df_cached_deps.empty and not df_new_deps.empty:
            df_combined_deps = pd.concat([df_cached_deps, df_new_deps], ignore_index=True)
            df_combined_deps['tx_id'] = df_combined_deps['tx_id'].astype(str)
            df_combined_deps = df_combined_deps.drop_duplicates(subset=['tx_id'], keep='last')
        elif not df_new_deps.empty:
            df_combined_deps = df_new_deps
        else:
            df_combined_deps = df_cached_deps
            
        # Re-verify datatypes before committing to DB file
        if not df_combined_tx.empty:
            df_combined_tx['tx_id'] = df_combined_tx['tx_id'].astype(str)
            df_combined_tx['complex_id'] = df_combined_tx['complex_id'].astype(str)
            df_combined_tx['asset_token'] = df_combined_tx['asset_token'].astype(str)
            df_combined_tx.to_sql("transactions", conn, if_exists="replace", index=False)
            
        if not df_combined_deps.empty:
            df_combined_deps['tx_id'] = df_combined_deps['tx_id'].astype(str)
            df_combined_deps.to_sql("deposits", conn, if_exists="replace", index=False)
            
        if bpu_cache:
            df_bpu_save = pd.DataFrame(list(bpu_cache.items()), columns=['account_id', 'bpu_val'])
            df_bpu_save.to_sql("bpu_cache", conn, if_exists="replace", index=False)
            
        conn.close()
        
        if account_balances:
            tasty_updates = {f"Tastytrade - {k}": v for k, v in account_balances.items()}
            save_daily_balances(tasty_updates, "Tastytrade API")

        return df_combined_tx, None, df_combined_deps, account_balances, maintenance_reqs
    except Exception as e:
        return None, str(e), None, None, None

def process_data(secret, token):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        df, error, df_deps, live_bals, maint_reqs = loop.run_until_complete(get_all_accounts_history(secret, token))
    finally:
        loop.close()

    if error: return None, error, None, None, None
    if df is None or df.empty: return pd.DataFrame(), None, None, pd.DataFrame(), {}, {}

    df['complex_id'] = df['complex_id'].fillna(df['date'].dt.strftime('%Y%m%d%H%M%S') + '_' + df.index.astype(str))
    df['complex_id'] = df['complex_id'].astype(str)
    df['asset_token'] = df['asset_token'].astype(str)
    
    realized_strategies = []
    open_strategies = [] 
    
    for (acc_id, ticker), data in df.groupby(['account_id', 'underlying']):
        active_chains = [] 
        data = data.sort_values('date') 
        events = data.groupby('complex_id', sort=False)
        
        for comp_id, event_df in events:
            event_tokens = event_df['asset_token'].tolist()
            matched_chain = None
            for chain in active_chains:
                if any(tok in chain["Inventory"] for tok in event_tokens):
                    matched_chain = chain
                    break
                    
            if matched_chain is None:
                matched_chain = {
                    "account_id": acc_id, "underlying": ticker, "Status": "OPEN", "opened_at": event_df['date'].iloc[0],
                    "Running_PL": 0.0, "Gross_PL": 0.0, "Total_Commissions": 0.0, "Total_Other_Fees": 0.0,
                    "opening_cash": event_df['signed_val'].sum(), "Inventory": {}, "opening_legs": {}, "initial_legs": {},
                    "is_rolled": False, "first_event_id": str(comp_id)  
                }
                active_chains.append(matched_chain)

            for _, row in event_df.iterrows():
                token = str(row['asset_token'])
                qty = row['signed_qty']
                val = row['signed_val']
                force_close = row.get('force_close', False)
                
                current_qty = matched_chain["Inventory"].get(token, 0.0)
                if str(comp_id) != str(matched_chain["first_event_id"]):
                    if current_qty == 0 or (current_qty > 0 and qty > 0) or (current_qty < 0 and qty < 0):
                        matched_chain["opening_cash"] += val
                
                if token not in matched_chain["opening_legs"] and token not in matched_chain["Inventory"]:
                    if not force_close:
                        matched_chain["opening_legs"][token] = qty
                        if str(comp_id) == str(matched_chain["first_event_id"]): matched_chain["initial_legs"][token] = qty
                        else: matched_chain["is_rolled"] = True

                matched_chain["Gross_PL"] += val
                matched_chain["Total_Commissions"] += row.get('commissions', 0.0)
                matched_chain["Total_Other_Fees"] += row.get('other_fees', 0.0)
                matched_chain["Running_PL"] += (val - row.get('commissions', 0.0) - row.get('other_fees', 0.0))

                if force_close: matched_chain["Inventory"][token] = 0.0
                else: matched_chain["Inventory"][token] = current_qty + qty
                
                if abs(matched_chain["Inventory"][token]) < 0.001: matched_chain["Inventory"].pop(token)
                    
            if not matched_chain["Inventory"]:
                matched_chain["Status"] = "CLOSED"
                finalize_chain(matched_chain, event_df['date'].max(), realized_strategies)
                active_chains.remove(matched_chain)

        now_dt = pd.Timestamp.now()
        for chain in reversed(active_chains):
            all_expired = True
            has_options = False
            for tok in list(chain["Inventory"].keys()):
                parsed = parse_occ_symbol(tok)
                if parsed['type'] != 'option':
                    all_expired = False 
                    break
                exp = parsed.get('expiry')
                if not exp:
                    all_expired = False
                    break
                has_options = True
                exp_dt = pd.to_datetime(exp, format='%y%m%d')
                
                if now_dt > exp_dt + timedelta(days=1): pass 
                else:
                    all_expired = False
                    break
                    
            if has_options and all_expired and chain["Inventory"]:
                chain["Inventory"].clear()
                chain["Status"] = "CLOSED"
                exp_dates = [pd.to_datetime(parse_occ_symbol(t)['expiry'], format='%y%m%d') for t in chain["opening_legs"] if parse_occ_symbol(t)['type'] == 'option']
                finalize_chain(chain, max(exp_dates) if exp_dates else now_dt, realized_strategies)
                active_chains.remove(chain)

        for active in active_chains:
            initial_parsed_legs = []
            for t, q in active.get("initial_legs", active["opening_legs"]).items():
                if abs(q) > 0.001:
                    l_data = parse_occ_symbol(t)
                    l_data['qty'] = q
                    initial_parsed_legs.append(l_data)
            
            open_strat = identify_strategy(initial_parsed_legs)
            if active.get("is_rolled", False): open_strat += " - Rolled"
            
            open_strategies.append({
                "Account": active["account_id"], "Underlying": active["underlying"], "Strategy": open_strat,
                "Open Date": active["opened_at"], "Inventory": format_inventory(active["Inventory"]),
                "Floating Gross P&L": active["Gross_PL"], "Floating Net P&L": active["Running_PL"],
                "Commissions Paid": active["Total_Commissions"], "Other Fees Paid": active["Total_Other_Fees"],
                "Fees Paid": active["Total_Commissions"] + active["Total_Other_Fees"]
            })
                
    return pd.DataFrame(realized_strategies), error, pd.DataFrame(open_strategies), df_deps, live_bals, maint_reqs