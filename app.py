# app.py
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import sqlite3
from datetime import timedelta
from dateutil.relativedelta import relativedelta

# Import our custom modules
from ui_components import get_custom_css, get_tooltip_html, format_currency, render_metric
from api_client import get_spy_return, DB_PATH, HAS_YFINANCE

# -------------------------------------------------------------------
# CONFIGURATION & CSS STYLING
# -------------------------------------------------------------------
config_dir = ".streamlit"
config_path = os.path.join(config_dir, "config.toml")

if not os.path.exists(config_path):
    os.makedirs(config_dir, exist_ok=True)
    with open(config_path, "w") as f:
        f.write("""[theme]\nbase="dark"\nprimaryColor="#15ace3"\nbackgroundColor="#0e1117"\nsecondaryBackgroundColor="#262730"\ntextColor="#fafafa"\nfont="sans serif"\n""")

st.set_page_config(page_title="Options Desk Analytics", layout="wide", page_icon="📊")
st.markdown(get_custom_css(), unsafe_allow_html=True)

# -------------------------------------------------------------------
# SESSION STATE & HELPER FUNCTIONS
# -------------------------------------------------------------------
if 'anchor_date' not in st.session_state:
    st.session_state.anchor_date = pd.Timestamp.now().normalize()
if 'current_tf' not in st.session_state:
    st.session_state.current_tf = "YTD"
if 'end_date_cache' not in st.session_state:
    st.session_state.end_date_cache = pd.Timestamp.now().normalize()

def step_date_window(direction, step):
    now = pd.Timestamp.now().normalize()
    if direction == "prev":
        st.session_state.anchor_date -= step
    elif direction == "next":
        proposed = st.session_state.anchor_date + step
        if proposed <= now:
            st.session_state.anchor_date = proposed
        else:
            st.session_state.anchor_date = now

# -------------------------------------------------------------------
# MAIN DASHBOARD DATA LOADING SWITCHES (DEMO MODE)
# -------------------------------------------------------------------

@st.cache_data
def generate_demo_data():
    """Generates fake portfolio data matching the API output structures."""
    
    # 1. Fake Realized Trades (df_res)
    dates = pd.date_range(start=pd.Timestamp.now() - pd.Timedelta(days=180), periods=20, freq='W')
    res_data = {
        'Account': ['Tastytrade - Demo Margin'] * 20,
        'Close Date': dates,
        'Underlying': ['SPY', 'QQQ', 'AAPL', 'TSLA', 'MSFT'] * 4,
        'Strategy': ['Iron Condor', 'Vertical Spread', 'Covered Call', 'Short Put', 'Straddle'] * 4,
        'Capital Risked': [1000, 500, 2000, 1500, 800] * 4,
        'Initial Premium': [150, 80, 250, 300, 120] * 4,
        'Gross P&L': [100, -50, 200, 150, -80] * 4,
        'Commissions': [4.0] * 20,
        'Other Fees': [1.5] * 20,
    }
    df_res = pd.DataFrame(res_data)
    df_res['Net P&L'] = df_res['Gross P&L'] - df_res['Commissions'] - df_res['Other Fees']
    df_res['ROI %'] = (df_res['Net P&L'] / df_res['Capital Risked']) * 100
    df_res['Profit %'] = (df_res['Gross P&L'] / df_res['Initial Premium']) * 100
    df_res['Days Open'] = [14, 7, 21, 30, 5] * 4
    df_res['DTE at Open'] = [45, 30, 60, 45, 14] * 4
    df_res['Outcome'] = df_res['Net P&L'].apply(lambda x: 'Win' if x > 0 else 'Loss')

    # 2. Fake Open Positions (df_open)
    open_data = {
        'Account': ['Tastytrade - Demo Margin', 'Tastytrade - Demo Margin'],
        'Underlying': ['AMD', 'NVDA'],
        'Strategy': ['Short Put', 'Vertical Spread'],
        'Open Date': [pd.Timestamp.now() - pd.Timedelta(days=5), pd.Timestamp.now() - pd.Timedelta(days=2)],
        'Inventory': ['-1 P 150', '-1 P 800 / +1 P 790'],
        'Floating Gross P&L': [45.0, -12.5],
        'Fees Paid': [2.5, 5.0],
    }
    df_open = pd.DataFrame(open_data)
    df_open['Floating Net P&L'] = df_open['Floating Gross P&L'] - df_open['Fees Paid']

    # 3. Fake Deposits & Balances
    df_deposits = pd.DataFrame({'account_id': ['Tastytrade - Demo Margin'], 'net_deposit': [50000.0]})
    live_balances = {'Tastytrade - Demo Margin': 58432.10}
    maint_reqs = {'Tastytrade - Demo Margin': 12500.00}
    
    return df_res, None, df_open, df_deposits, live_balances, maint_reqs

# Load Demo Data instantly without checking for secrets
with st.spinner("Loading Demo Data..."):
    df_res, error, df_open, df_deposits, live_balances, maint_reqs = generate_demo_data()

# Create empty databases for the demo so it doesn't crash the unified balance charts
df_unified_db = pd.DataFrame(columns=['date', 'account_name', 'balance', 'source'])
df_manual = pd.DataFrame(columns=['date', 'account_name', 'balance', 'source'])

if error:
    st.error(f"API Error: {error}")
elif df_res is None or df_res.empty:
    st.warning("No realized trade history found.")
else:
    
    st.title("Portfolio Tracker & Analytics")
    st.markdown("<div class='sticky-marker'></div>", unsafe_allow_html=True)
    
    sticky_container = st.container()
    portfolio_container = st.container()
    metrics_container = st.container()
    open_pos_container = st.container()
    filters_container = st.container() 
    closed_tables_container = st.container()
    charts_container = st.container()

    all_underlyings = set(df_res['Underlying'].dropna()) if not df_res.empty else set()
    raw_strategies = set(df_res['Strategy'].dropna()) if not df_res.empty else set()
    if not df_open.empty:
        all_underlyings.update(df_open['Underlying'].dropna())
        raw_strategies.update(df_open['Strategy'].dropna())
        
    available_underlyings = sorted(list(all_underlyings))
    base_strategies = set(s.replace(" - Rolled", "") for s in raw_strategies)
    available_strategies = sorted(list(base_strategies))

    tt_accs_sorted = sorted(list(live_balances.keys()), key=lambda x: x.split(' - ')[-1] if ' - ' in x else x)
    db_accs = df_unified_db['account_name'].unique().tolist() if not df_unified_db.empty else []
    
    tt_display_map = {f"Tastytrade - {acc}": acc for acc in tt_accs_sorted}
    db_display_map = {acc: acc for acc in db_accs} 
    
    all_display_map = {**tt_display_map, **db_display_map}
    all_display_options = list(all_display_map.keys())
    tt_display_options = list(tt_display_map.keys())

    with sticky_container:
        top_left, top_right = st.columns([3, 1])
        with top_left:
            c1, c2, c3, _ = st.columns([5, 1, 1, 3])
            with c1:
                time_frame = st.segmented_control(
                    "Timeframe",
                    options=["Daily", "Weekly", "Monthly", "YTD", "All Time"],
                    default="YTD", 
                    selection_mode="single",
                    label_visibility="collapsed"
                )
            
            today_dt = pd.Timestamp.now().normalize()
            
            if not time_frame: time_frame = "YTD"
            
            if time_frame != st.session_state.current_tf:
                new_anchor = st.session_state.end_date_cache
                if new_anchor > today_dt:
                    new_anchor = today_dt
                st.session_state.anchor_date = new_anchor
                st.session_state.current_tf = time_frame
            
            anchor = st.session_state.anchor_date
            if anchor > today_dt:
                anchor = today_dt
                st.session_state.anchor_date = anchor
            
            if time_frame == "Daily":
                start_date, end_date = anchor, anchor
                step = timedelta(days=1)
            elif time_frame == "Weekly":
                start_date = anchor - timedelta(days=anchor.weekday())
                end_date = start_date + timedelta(days=6)
                step = timedelta(weeks=1)
            elif time_frame == "Monthly":
                start_date = anchor.replace(day=1)
                end_date = start_date + relativedelta(months=1, days=-1)
                step = relativedelta(months=1)
            elif time_frame == "YTD":
                start_date = pd.Timestamp(today_dt.year, 1, 1).normalize()
                end_date = today_dt
                step = relativedelta(years=1)
            else: 
                start_date = df_res['Close Date'].min() if not df_res.empty else today_dt
                end_date = today_dt
                step = timedelta(days=0)
                
            if end_date > today_dt:
                end_date = today_dt
                
            st.session_state.end_date_cache = end_date

            disable_nav = time_frame in ["All Time", "YTD"]
            with c2:
                st.button("◀", key="prev", disabled=disable_nav, on_click=step_date_window, args=("prev", step), use_container_width=True)
            with c3:
                st.button("▶", key="next", disabled=disable_nav, on_click=step_date_window, args=("next", step), use_container_width=True)
            
            label = "All History" if time_frame == "All Time" else \
                    start_date.strftime('%A, %b %d') if time_frame == "Daily" else \
                    f"{start_date.strftime('%b %d, %Y')} — {end_date.strftime('%b %d, %Y')}"
            
            st.markdown(f"<div style='padding-top: 15px; padding-bottom: 10px; color: #9ca3af; font-size: 15px;'><b>Viewing Range:</b> {label}</div>", unsafe_allow_html=True)

        with top_right:
            tr1, tr2 = st.columns([6, 1])
            with tr1:
                sel_view = st.segmented_control("View Mode", options=["Options Analytics", "Portfolio Tracker"], default="Options Analytics", label_visibility="collapsed")
            with tr2:
                st.markdown(get_tooltip_html(), unsafe_allow_html=True)
            
            if sel_view == "Options Analytics":
                st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
                sel_acc_display = st.selectbox("Account", ["All Accounts"] + tt_display_options, key="account_selector", label_visibility="collapsed")
                
                if sel_acc_display == "All Accounts":
                    active_tt_accs = tt_accs_sorted
                else:
                    active_tt_accs = [tt_display_map[sel_acc_display]]
            else:
                st.markdown("<div style='margin-top: 5px;'></div>", unsafe_allow_html=True)
                sel_accs_display = []
                
                for opt in all_display_options:
                    if f"chk_{opt}" not in st.session_state:
                        st.session_state[f"chk_{opt}"] = True

                def set_all_checkboxes(val):
                    for o in all_display_options:
                        st.session_state[f"chk_{o}"] = val
                        
                def select_platform(plat):
                    for o in all_display_options:
                        if o.startswith(plat):
                            st.session_state[f"chk_{o}"] = True
                
                with st.popover("Select Accounts ▾", use_container_width=True):
                    c1, c2 = st.columns(2)
                    c1.button("☑ Select All", on_click=set_all_checkboxes, args=(True,), use_container_width=True)
                    c2.button("☐ Clear All", on_click=set_all_checkboxes, args=(False,), use_container_width=True)
                    
                    platforms = sorted(list(set([o.split(" - ")[0] for o in all_display_options])))
                    if len(platforms) > 1:
                        pcols = st.columns(len(platforms))
                        for i, plat in enumerate(platforms):
                            pcols[i].button(f"+ {plat}", on_click=select_platform, args=(plat,), key=f"btn_plat_{plat}", use_container_width=True)
                            
                    st.divider()
                    
                    for opt in all_display_options:
                        if st.checkbox(opt, key=f"chk_{opt}"):
                            sel_accs_display.append(opt)

    # -------------------------------------------------------------------
    # TRACKER VIEW RENDER
    # -------------------------------------------------------------------
    if sel_view == "Portfolio Tracker":
        active_internal_accs = [all_display_map[d] for d in sel_accs_display]
        
        with portfolio_container:
            st.markdown("---")
            st.subheader("Total Portfolio Value (Live)")
            
            live_portfolio_total = 0.0
            raw_breakdown = {}
            
            for acc_name in active_internal_accs:
                if acc_name in tt_accs_sorted:
                    val = live_balances.get(acc_name, 0.0)
                    live_portfolio_total += val
                    raw_breakdown[f"Tastytrade - {acc_name}"] = val
                else:
                    acc_data = df_unified_db[df_unified_db['account_name'] == acc_name]
                    if not acc_data.empty:
                        val = acc_data.sort_values('date').iloc[-1]['balance']
                        live_portfolio_total += val
                        raw_breakdown[acc_name] = val
            
            master_sort = [
                "Tastytrade - Demo Margin"
            ]
            
            def get_sort_index(k):
                if k in master_sort:
                    return master_sort.index(k)
                elif k.startswith("Tastytrade"):
                    return len(master_sort) 
                else:
                    return 999 
                    
            sorted_keys = sorted(raw_breakdown.keys(), key=get_sort_index)
            live_acc_breakdown = {k: raw_breakdown[k] for k in sorted_keys}
            
            st.markdown(f"<h1 style='color: #15ace3; margin-bottom: 25px;'>{format_currency(live_portfolio_total)}</h1>", unsafe_allow_html=True)
            
            st.markdown("### Account Breakdown")
            if live_acc_breakdown:
                items = list(live_acc_breakdown.items())
                for i in range(0, len(items), 4):
                    cols = st.columns(4)
                    for j in range(4):
                        if i + j < len(items):
                            name, val = items[i + j]
                            cols[j].markdown(render_metric(name, format_currency(val), "Current Captured Value", condition=None), unsafe_allow_html=True)
                    
                    st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)
            
            st.markdown("<div style='margin-top: 25px;'></div>", unsafe_allow_html=True)
            st.markdown("### Empirical Portfolio Growth")
            
            if not df_unified_db.empty and sel_accs_display:
                db_chart = df_unified_db.copy()
                
                db_chart = db_chart[db_chart['account_name'].isin(sel_accs_display)]
                
                if not db_chart.empty:
                    db_chart['Date'] = pd.to_datetime(db_chart['date']).dt.normalize()
                    
                    if len(db_chart['Date'].unique()) == 1:
                        fake_yesterday = db_chart.copy()
                        fake_yesterday['Date'] = fake_yesterday['Date'] - pd.Timedelta(days=1)
                        db_chart = pd.concat([fake_yesterday, db_chart], ignore_index=True)
                        
                    pivot_df = db_chart.pivot_table(index='Date', columns='account_name', values='balance')
                    pivot_df = pivot_df.ffill().fillna(0)
                    
                    pivot_df['Total Portfolio'] = pivot_df.sum(axis=1)
                    
                    melted_df = pivot_df.reset_index().melt(id_vars='Date', var_name='Account', value_name='Balance')
                    
                    df_individuals = melted_df[melted_df['Account'] != 'Total Portfolio']
                    df_total = melted_df[melted_df['Account'] == 'Total Portfolio']
                    
                    fig_port = px.area(df_individuals, x='Date', y='Balance', color='Account')
                    fig_port.update_traces(mode='lines+markers') 
                    
                    fig_port.add_trace(go.Scatter(
                        x=df_total['Date'], 
                        y=df_total['Balance'],
                        mode='lines+markers',
                        name='Grand Total',
                        line=dict(color='white', width=3, dash='dot'),
                        marker=dict(size=6)
                    ))
                    
                    fig_port.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)', 
                        plot_bgcolor='rgba(0,0,0,0)', 
                        yaxis_gridcolor='#333', 
                        xaxis_gridcolor='#333', 
                        font_color='white',
                        yaxis=dict(tickformat="$,.2f", title="Balance"), 
                        legend_title_text='',
                        hovermode='x unified'
                    )
                    st.plotly_chart(fig_port, width='stretch')
                else:
                    st.info("No historical data collected yet for these accounts. Your database will build daily.")
            else:
                st.info("Historical growth charts will dynamically plot here as your API cache logs daily balances over time.")

            st.markdown("---")
            with st.expander("📝 Manual Account Update (Voya / External)"):
                with st.form("manual_entry_form", clear_on_submit=True):
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        man_date = st.date_input("Statement Date", value=pd.Timestamp.now().date())
                    with col2:
                        man_acc = st.text_input("Account Name", placeholder="e.g. Voya - 401k")
                    with col3:
                        man_bal = st.number_input("Total Balance ($)", min_value=0.0, step=100.0)
                    with col4:
                        man_dep = st.number_input("Contributions / Deposits ($)", value=0.0, step=100.0)
                    
                    submitted = st.form_submit_button("Save Entry")
                    if submitted and man_acc:
                        st.success(f"Saved {man_acc} balance of ${man_bal:,.2f} (Simulated for Demo)")

    # -------------------------------------------------------------------
    # ANALYTICS VIEW RENDER
    # -------------------------------------------------------------------
    elif sel_view == "Options Analytics":
        df_view = df_res[df_res['Account'].isin(active_tt_accs)]
        mask = (df_view['Close Date'] >= start_date) & (df_view['Close Date'] <= end_date.replace(hour=23, minute=59))
        df_base = df_view.loc[mask].copy() 
        df_open_base = df_open[df_open['Account'].isin(active_tt_accs)]

        current_balance = sum(live_balances.get(a, 0.0) for a in active_tt_accs)
        maintenance = sum(maint_reqs.get(a, 0.0) for a in active_tt_accs)
        if df_open_base.empty: maintenance = 0.0

        realized_gross_all = df_view['Gross P&L'].sum() if not df_view.empty else 0
        closed_comm_all = df_view['Commissions'].sum() if not df_view.empty else 0
        closed_other_all = df_view['Other Fees'].sum() if not df_view.empty else 0
        all_time_net_total = realized_gross_all - closed_comm_all - closed_other_all
        
        df_deposits_view = df_deposits[df_deposits['account_id'].isin(active_tt_accs)]
        total_deposits_all = df_deposits_view['net_deposit'].sum() if not df_deposits_view.empty else 0.0
        
        derived_principal = total_deposits_all if total_deposits_all > 0 else (current_balance - all_time_net_total)
        if derived_principal <= 0: derived_principal = 1.0 

        realized_gross = df_base['Gross P&L'].sum() if not df_base.empty else 0
        realized_net = df_base['Net P&L'].sum() if not df_base.empty else 0

        if time_frame == "Daily":
            est_start_bal = current_balance - realized_net
            if est_start_bal <= 0: est_start_bal = 1.0
            mult_growth = (realized_net / est_start_bal) * 100 if not df_base.empty else 0.0
        else:
            if not df_base.empty:
                base_daily_bal = df_base.groupby(df_base['Close Date'].dt.date)['Net P&L'].sum().reset_index()
                mask_before_base = (df_view['Close Date'].dt.date < start_date.date())
                net_before_base = df_view.loc[mask_before_base]['Net P&L'].sum()
                chart_baseline_base = derived_principal + net_before_base
                base_daily_bal['Balance'] = chart_baseline_base + base_daily_bal['Net P&L'].cumsum()
                
                base_daily_bal['Prev_Balance'] = base_daily_bal['Balance'] - base_daily_bal['Net P&L']
                base_daily_bal['Daily_Return'] = base_daily_bal['Net P&L'] / base_daily_bal['Prev_Balance'].replace(0, 1)
                mult_growth = ((1 + base_daily_bal['Daily_Return']).prod() - 1) * 100
            else:
                mult_growth = 0.0
                
        # EOY PROJECTION MATH
        ytd_start = pd.Timestamp(today_dt.year, 1, 1).normalize()
        df_ytd = df_view[(df_view['Close Date'] >= ytd_start) & (df_view['Close Date'] <= today_dt.replace(hour=23, minute=59))]
        ytd_net_profit = df_ytd['Net P&L'].sum() if not df_ytd.empty else 0.0
        
        days_elapsed = max(1, (today_dt - ytd_start).days)
        days_in_year = 366 if today_dt.is_leap_year else 365
        days_remaining = max(0, days_in_year - days_elapsed)
        
        daily_velocity = ytd_net_profit / days_elapsed
        monthly_velocity = daily_velocity * (days_in_year / 12.0)
        
        projected_eoy_profit = ytd_net_profit + (daily_velocity * days_remaining)
        projected_pct = (projected_eoy_profit / derived_principal) * 100 if derived_principal > 0 else 0.0

        with metrics_container:
            closed_comm = df_base['Commissions'].sum() if not df_base.empty else 0
            closed_other = df_base['Other Fees'].sum() if not df_base.empty else 0
            total_comm = closed_comm 
            total_other_fees = closed_other 
            total_fees = total_comm + total_other_fees
            
            principal_for_rop = total_deposits_all if total_deposits_all > 0 else 1.0
            rop_all_time = (all_time_net_total / principal_for_rop) * 100
            
            net_wins = df_base[df_base['Net P&L'] > 0]['Net P&L'].sum() if not df_base.empty else 0.0
            net_losses = abs(df_base[df_base['Net P&L'] < 0]['Net P&L'].sum()) if not df_base.empty else 0.0
            
            if net_losses > 0:
                profit_factor = net_wins / net_losses
                pf_display = f"{profit_factor:.2f}x"
            elif net_wins > 0:
                profit_factor = net_wins / 1.0
                pf_display = f"{profit_factor:.1f}x"
            else:
                profit_factor = 0.0
                pf_display = "0.00x"
                
            pf_condition = profit_factor - 1.0
            pf_subtext = f"{format_currency(net_wins)} P / {format_currency(-net_losses)} L"
            
            winning_trades = df_base[df_base['Net P&L'] > 0]['Net P&L'] if not df_base.empty else pd.Series(dtype=float)
            count = len(df_base)
            wins = len(winning_trades)
            win_rate = (wins / count * 100) if count > 0 else 0
            
            bpu_rate = (maintenance / current_balance * 100) if current_balance > 0 else 0.0

            credit_trades = df_base[df_base['Initial Premium'] > 0] if not df_base.empty else pd.DataFrame()
            if not credit_trades.empty and credit_trades['Initial Premium'].sum() > 0:
                pcr = (credit_trades['Net P&L'].sum() / credit_trades['Initial Premium'].sum()) * 100
            else:
                pcr = 0.0

            spy_pct = get_spy_return(start_date, end_date)
            spy_sign = "+" if spy_pct >= 0 else ""
            twr_subtext = f"vs. SPY ({spy_sign}{spy_pct:.2f}%)" if HAS_YFINANCE else "vs. SPY (Needs yfinance package)"

            st.markdown("---")
            
            k1, k2, k3, k4 = st.columns(4)
            k1.markdown(render_metric("Gross P&L", format_currency(realized_gross), "Realized", condition=realized_gross), unsafe_allow_html=True)
            k2.markdown(render_metric("Net P&L", format_currency(realized_net), "w/Fees", condition=realized_net), unsafe_allow_html=True)
            
            floating_gross = df_open_base['Floating Gross P&L'].sum() if not df_open_base.empty else 0.0
            k3.markdown(render_metric("Floating P&L", format_currency(floating_gross), "Unrealized Gross", condition=floating_gross), unsafe_allow_html=True)
            
            k4.markdown(render_metric("Total Fees", format_currency(total_fees), f"Comms: {format_currency(total_comm)} | Fees: {format_currency(total_other_fees)}", condition=None), unsafe_allow_html=True)
            
            st.markdown("<div style='padding-top: 15px;'></div>", unsafe_allow_html=True)
            
            r1, r2, r3, r4 = st.columns(4)
            rop_subtext = f"{format_currency(all_time_net_total)} Net / {format_currency(principal_for_rop)} Dep"
            r1.markdown(render_metric("Return on Principal", f"{rop_all_time:.1f}%", rop_subtext, condition=rop_all_time), unsafe_allow_html=True)
            
            r2.markdown(render_metric("Time-Weighted Return", f"{mult_growth:.2f}%", twr_subtext, condition=mult_growth), unsafe_allow_html=True)
            
            r3.markdown(render_metric("Profit Factor", pf_display, pf_subtext, condition=pf_condition), unsafe_allow_html=True)
            
            r4.markdown(render_metric("Premium Capture Rate", f"{pcr:.1f}%", "Total Net / Total Initial", condition=pcr), unsafe_allow_html=True)

            st.markdown("<div style='padding-top: 15px;'></div>", unsafe_allow_html=True)

            m1, m2, m3, m4 = st.columns(4)
            m1.markdown(render_metric("Closed Trades Win Rate", f"{win_rate:.1f}%", f"{wins}W / {count-wins}L", condition=None), unsafe_allow_html=True)
            m2.markdown(render_metric("Buying Power Util (BPU)", f"{bpu_rate:.1f}%", f"{format_currency(maintenance)} Reserved", condition=None), unsafe_allow_html=True)
            
            expected_str = f"Est: {projected_pct:.1f}% (+{format_currency(monthly_velocity)}/mo)" if monthly_velocity >= 0 else f"Est: {projected_pct:.1f}% ({format_currency(monthly_velocity)}/mo)"
            m3.markdown(render_metric('Projected EOY P&L', format_currency(projected_eoy_profit), expected_str, condition=projected_eoy_profit), unsafe_allow_html=True)
            
            m4.markdown(render_metric("Account Balance", format_currency(current_balance), "Direct API Feed", condition=None), unsafe_allow_html=True)

        with filters_container:
            st.markdown("---")
            st.subheader("Filter Positions & Activity")
            f1, f2 = st.columns(2)
            with f1:
                selected_underlying = st.multiselect("Filter by Underlying", available_underlyings)
            with f2:
                selected_strategy = st.multiselect("Filter by Strategy", available_strategies)

        df_filtered = df_base.copy()
        
        if selected_underlying:
            df_filtered = df_filtered[df_filtered['Underlying'].isin(selected_underlying)] if not df_filtered.empty else df_filtered
                
        if selected_strategy:
            expanded_strategies = [s for strat in selected_strategy for s in (strat, f"{strat} - Rolled")]
            df_filtered = df_filtered[df_filtered['Strategy'].isin(expanded_strategies)] if not df_filtered.empty else df_filtered

        with open_pos_container:
            df_open_view = df_open_base.sort_values('Open Date', ascending=False)
            if not df_open_view.empty:
                st.markdown("<div style='margin-top: 60px;'></div>", unsafe_allow_html=True)
                st.subheader("Active / Open Positions")
                
                open_display_cols = ['Account', 'Underlying', 'Strategy', 'Open Date', 'Inventory', 'Floating Gross P&L', 'Fees Paid', 'Floating Net P&L']
                
                st.dataframe(
                    df_open_view[open_display_cols]
                    .style.format({
                        "Floating Gross P&L": "${:,.2f}",
                        "Fees Paid": "${:,.2f}",
                        "Floating Net P&L": "${:,.2f}",
                        "Open Date": lambda x: x.strftime('%Y-%m-%d %H:%M') if pd.notnull(x) else ""
                    })
                    .map(lambda x: 'color: #00cc96' if x > 0 else 'color: #ff4b4b' if x < 0 else 'color: #d1d5db', subset=['Floating Gross P&L']),
                    width='stretch',
                    hide_index=True
                )

        with closed_tables_container:
            if not df_filtered.empty:
                st.markdown("---")
                st.subheader("Recent Closed Activity")
                
                closed_display_cols = ['Account', 'Close Date', 'Underlying', 'Strategy', 'Capital Risked', 'Initial Premium', 'Gross P&L', 'Fees', 'Net P&L', 'ROI %', 'Profit %', 'Days Open', 'DTE at Open', 'Outcome']
                    
                df_display = df_filtered[closed_display_cols].copy()
                
                dynamic_height = min(600, (len(df_display) * 35) + 40)
                
                st.dataframe(
                    df_display
                    .sort_values('Close Date', ascending=False)
                    .style.format({
                        "Capital Risked": "${:,.2f}",
                        "Initial Premium": "${:,.2f}",
                        "Gross P&L": "${:,.2f}", 
                        "Fees": "${:,.2f}", 
                        "Net P&L": "${:,.2f}",
                        "ROI %": "{:.1f}%",
                        "Profit %": "{:.1f}%",
                        "DTE at Open": lambda x: f"{x:.0f}" if pd.notnull(x) else "N/A",
                        "Close Date": lambda x: x.strftime('%Y-%m-%d %H:%M') if pd.notnull(x) else ""
                    })
                    .map(lambda x: 'color: #00cc96' if x > 0 else 'color: #ff4b4b' if x < 0 else 'color: #d1d5db', subset=['Gross P&L', 'Initial Premium']),
                    width='stretch',
                    height=dynamic_height,
                    hide_index=True
                )
            else:
                st.info(f"No closed trades found for this filter range ({label}).")

        with charts_container:
            if not df_filtered.empty:
                st.markdown("---")
                c1, c2 = st.columns(2)
                
                with c1:
                    st.subheader("Account Balance")
                    daily_bal = df_filtered.groupby(df_filtered['Close Date'].dt.date)['Net P&L'].sum().reset_index()
                    
                    mask_before = (df_view['Close Date'].dt.date < start_date.date())
                    net_before = df_view.loc[mask_before]['Net P&L'].sum()
                    chart_baseline = derived_principal + net_before
                    
                    daily_bal['Balance'] = chart_baseline + daily_bal['Net P&L'].cumsum()
                    daily_bal['Date_Str'] = pd.to_datetime(daily_bal['Close Date']).dt.strftime('%b %d, %Y')
                    
                    num_points = len(daily_bal)
                    tick_step = max(1, num_points // 10)
                    
                    fig = px.line(daily_bal, x='Date_Str', y='Balance')
                    fig.update_traces(line_color='#15ace3', line_width=3)
                    fig.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)', 
                        plot_bgcolor='rgba(0,0,0,0)', 
                        yaxis_gridcolor='#333', 
                        xaxis_gridcolor='#333', 
                        font_color='white',
                        yaxis=dict(tickformat="$,.2f", title="Balance"),
                        xaxis=dict(title="", type='category', categoryorder='trace', tickangle=-45, tickmode='linear', dtick=tick_step)
                    )
                    st.plotly_chart(fig, width='stretch')

                with c2:
                    st.subheader("Daily Net P&L")
                    day_data = df_filtered.groupby(df_filtered['Close Date'].dt.date)['Net P&L'].sum().reset_index()
                    day_data['Color'] = day_data['Net P&L'].apply(lambda x: '#00cc96' if x >= 0 else '#ff4b4b')
                    
                    day_data['Date_Str'] = pd.to_datetime(day_data['Close Date']).dt.strftime('%b %d, %Y')
                    
                    num_points_bar = len(day_data)
                    tick_step_bar = max(1, num_points_bar // 10)

                    fig_b = go.Figure(data=[go.Bar(x=day_data['Date_Str'], y=day_data['Net P&L'], marker_color=day_data['Color'])])
                    fig_b.update_layout(
                        paper_bgcolor='rgba(0,0,0,0)', 
                        plot_bgcolor='rgba(0,0,0,0)', 
                        yaxis_gridcolor='#333', 
                        xaxis_gridcolor='#333', 
                        font_color='white',
                        yaxis=dict(tickformat="$,.2f", title="Net P&L"),
                        xaxis=dict(title="", type='category', categoryorder='trace', tickangle=-45, tickmode='linear', dtick=tick_step_bar)
                    )
                    st.plotly_chart(fig_b, width='stretch')