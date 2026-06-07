# data_engine.py
import pandas as pd
import re

def parse_occ_symbol(symbol):
    if "_SHARES" in symbol:
        return {'type': 'equity', 'symbol': symbol.replace('_SHARES', '')}
    
    match = re.match(r'^([a-zA-Z\s]{6})(\d{6})([CP])(\d{8})$', symbol)
    if match:
        return {
            'ticker': match.group(1).strip(),
            'expiry': match.group(2),
            'opt_type': match.group(3),
            'strike': float(match.group(4)) / 1000.0,
            'type': 'option'
        }
    return {'type': 'unknown', 'symbol': symbol}

def identify_strategy(legs):
    equities = [l for l in legs if l['type'] == 'equity']
    options = [l for l in legs if l['type'] == 'option']
    
    if not options and equities:
        return "Long Stock" if equities[0]['qty'] > 0 else "Short Stock"
    if not options: return "Unknown Strategy"
    
    calls = [o for o in options if o['opt_type'] == 'C']
    puts = [o for o in options if o['opt_type'] == 'P']
    
    if equities:
        if equities[0]['qty'] > 0 and len(calls) == 1 and calls[0]['qty'] < 0: return "Covered Call"
        return "Complex Equity Hedged (Wheel)"

    if len(options) == 1:
        o = options[0]
        if o['opt_type'] == 'C': return "Long Call" if o['qty'] > 0 else "Short Call"
        if o['opt_type'] == 'P': return "Long Put" if o['qty'] > 0 else "Short Put"
        
    if len(options) == 2:
        if len(calls) == 2: return "Call Vertical" if calls[0]['expiry'] == calls[1]['expiry'] else "Call Calendar"
        if len(puts) == 2: return "Put Vertical" if puts[0]['expiry'] == puts[1]['expiry'] else "Put Calendar"
        if len(calls) == 1 and len(puts) == 1:
            if calls[0]['qty'] < 0 and puts[0]['qty'] < 0: return "Short Straddle" if calls[0]['strike'] == puts[0]['strike'] else "Short Strangle"
            if calls[0]['qty'] > 0 and puts[0]['qty'] > 0: return "Long Straddle" if calls[0]['strike'] == puts[0]['strike'] else "Long Strangle"
                
    if len(options) == 4 and len(calls) == 2 and len(puts) == 2: return "Iron Condor"
    if len(options) == 3: return "Butterfly (Or 3-Leg)"
    
    return "Multi-Leg"

def format_inventory(inventory_dict):
    items = []
    for t, q in inventory_dict.items():
        if abs(q) > 0.001:
            parsed = parse_occ_symbol(t)
            if parsed['type'] == 'equity':
                items.append(f"{'+' if q>0 else ''}{int(q)} {parsed['symbol']}")
            elif parsed['type'] == 'option':
                exp = pd.to_datetime(parsed['expiry'], format='%y%m%d').strftime('%m/%d/%y')
                opt_str = f"{parsed['ticker']} {exp} {parsed['strike']}{parsed['opt_type']}"
                items.append(f"{'+' if q>0 else ''}{int(q)} {opt_str}")
            else:
                items.append(f"{'+' if q>0 else ''}{int(q)} {t}")
    return ", ".join(items)

def calculate_capital_risked(parsed_legs, opening_cash):
    options = [l for l in parsed_legs if l['type'] == 'option']
    equities = [l for l in parsed_legs if l['type'] == 'equity']
    
    if equities and not options: return abs(opening_cash) if opening_cash != 0 else 1.0
    if not options: return abs(opening_cash) if opening_cash != 0 else 1.0
    if opening_cash < 0: return abs(opening_cash)
        
    calls = [o for o in options if o['opt_type'] == 'C']
    puts = [o for o in options if o['opt_type'] == 'P']
    
    call_width = max([c['strike'] for c in calls]) - min([c['strike'] for c in calls]) if len(calls) >= 2 else 0
    put_width = max([p['strike'] for p in puts]) - min([p['strike'] for p in puts]) if len(puts) >= 2 else 0
        
    max_width = max(call_width, put_width)
    
    if max_width > 0:
        contracts = abs(options[0]['qty'])
        total_risk = (max_width * 100 * contracts) - abs(opening_cash)
        return max(total_risk, abs(opening_cash))
    else:
        strike = options[0]['strike']
        contracts = abs(options[0]['qty'])
        return (strike * 100 * contracts) - abs(opening_cash)

def finalize_chain(chain, close_date, realized_strategies):
    initial_premium = chain["opening_cash"]
    gross_pnl = chain["Gross_PL"]
    fees_total = chain["Total_Commissions"] + chain["Total_Other_Fees"]
    net_pnl = chain["Running_PL"]
    
    parsed_legs = []
    for t, q in chain["opening_legs"].items():
        leg_data = parse_occ_symbol(t)
        leg_data['qty'] = q
        parsed_legs.append(leg_data)
        
    strat_label = identify_strategy(parsed_legs)
    capital_risked = calculate_capital_risked(parsed_legs, initial_premium)

    # USES GROSS P&L FOR ROI/PROFIT CALCS AS REQUESTED
    roc_pct = (gross_pnl / capital_risked * 100) if capital_risked > 0 else 0
    abs_opening = abs(initial_premium)
    profit_pct = (gross_pnl / abs_opening * 100) if abs_opening > 0 else 0
    
    days_open = max(0, (close_date - chain["opened_at"]).days)
    
    dtes = []
    for leg in parsed_legs:
        if leg['type'] == 'option' and leg.get('expiry'):
            exp_dt = pd.to_datetime(leg['expiry'], format='%y%m%d')
            dtes.append((exp_dt - chain["opened_at"]).days)
    
    dte_at_open = min(dtes) if dtes else None
    
    # Outcome remains tied to Net P&L so you know if fees ruined a winning trade
    if net_pnl > 0: outcome = "WIN"
    elif net_pnl < 0: outcome = "LOSS"
    else: outcome = "BREAKEVEN"

    realized_strategies.append({
        "Account": chain["account_id"],
        "Underlying": chain["underlying"],
        "Strategy": strat_label,
        "Open Date": chain["opened_at"],
        "Close Date": close_date,
        "Capital Risked": capital_risked,
        "Initial Premium": initial_premium, 
        "Gross P&L": gross_pnl,
        "Commissions": chain["Total_Commissions"], 
        "Other Fees": chain["Total_Other_Fees"],   
        "Fees": fees_total,                        
        "Net P&L": net_pnl,
        "ROI %": roc_pct, 
        "Profit %": profit_pct,
        "Days Open": days_open,
        "DTE at Open": dte_at_open,
        "Outcome": outcome
    })