# ui_components.py

def get_custom_css():
    return """
    <style>
        header[data-testid="stHeader"] { display: none !important; }
        .block-container { padding-top: 2rem !important; }
        h1 { padding-top: 0rem !important; padding-bottom: 1rem !important; margin-bottom: 0rem !important; }
        
        div[data-testid="stSegmentedControl"] button { height: 36px !important; min-height: 36px !important; }
        div[data-testid="stSegmentedControl"] button[aria-selected="true"] { background-color: #15ace3 !important; color: white !important; font-weight: 600 !important; border-color: #15ace3 !important; }
        div[data-testid="stSegmentedControl"] button[aria-selected="false"] { background-color: #1f2937; color: #9ca3af; border: 1px solid #374151; }
        div[data-testid="stSegmentedControl"] button:hover { color: #15ace3; border-color: #15ace3; }
        .stApp { background-color: #0e1117; }
        
        div[data-testid="stMetric"] { background-color: #1f2937; border: 1px solid #374151; padding: 16px; border-radius: 8px; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        
        div.stButton > button { background-color: #1f2937; border: 1px solid #374151; color: #e5e7eb; border-radius: 8px; min-height: 36px !important; }
        div.stButton > button:hover { background-color: #374151; border-color: #15ace3; color: #15ace3; }
        
        div[data-testid="stVerticalBlock"] > div:has(.sticky-marker) { display: none !important; }
        div[data-testid="stVerticalBlock"] > div:has(.sticky-marker) + div { position: sticky !important; top: 0px !important; z-index: 999 !important; background-color: #0e1117 !important; padding-top: 1rem !important; padding-bottom: 1.5rem !important; border-bottom: 1px solid #374151 !important; margin-bottom: 2rem !important; }
        
        div[data-testid="stColumn"] { overflow: visible !important; z-index: 10 !important; }
        
        .info-tooltip { 
            position: relative; 
            display: inline-block; 
            cursor: help; 
            font-size: 22px; 
            color: #9ca3af; 
            line-height: 1; 
        }
        .info-tooltip .tooltiptext { 
            visibility: hidden; 
            width: 650px; 
            background-color: #0e1117; 
            color: #fafafa; 
            text-align: left; 
            border-radius: 8px; 
            padding: 24px; 
            border: 1px solid #374151; 
            position: absolute; 
            z-index: 999999; 
            top: 130%; 
            right: 0; 
            font-size: 15px; 
            box-shadow: 0 10px 25px rgba(0,0,0,0.6); 
            font-family: sans-serif; 
            font-weight: normal; 
            line-height: 1.6; 
            white-space: normal; 
        }
        .info-tooltip:hover .tooltiptext { 
            visibility: visible; 
        }
    </style>
    """

def get_tooltip_html():
    return """
    <div style="display: flex; justify-content: flex-end; margin-bottom: 8px;">
        <div class="info-tooltip">ℹ️
            <div class="tooltiptext">
                <b style="font-size: 15px; color: #15ace3;">Dashboard Metrics Explained:</b>
                <ul style="margin-top: 10px; margin-bottom: 0; padding-left: 20px;">
                    <li style="margin-bottom: 6px;"><b>Projected EOY P&L:</b> An estimate of your total year-end profit, calculated by applying your current win-rate to the remaining days in the year.</li>
                    <li style="margin-bottom: 6px;"><b>Est. Monthly Run Rate:</b> Your year-to-date net profit averaged over the time elapsed, scaled to a standard 30.4-day month to show your current trajectory.</li>
                    <li style="margin-bottom: 6px;"><b>Gross P&L:</b> Your raw realized profit or loss before any commissions or clearing fees are deducted.</li>
                    <li style="margin-bottom: 6px;"><b>Net P&L:</b> Your true realized profit or loss after all platform and exchange fees have been paid.</li>
                    <li style="margin-bottom: 6px;"><b>Floating P&L:</b> The current unrealized profit or loss of all open, active positions.</li>
                    <li style="margin-bottom: 6px;"><b>Total Fees:</b> The combined sum of all commissions, clearing fees, and regulatory fees paid.</li>
                    <li style="margin-bottom: 6px;"><b>Return on Principal (ROP):</b> Total net earnings measured against your total deposited cash (strictly excludes open positions).</li>
                    <li style="margin-bottom: 6px;"><b>Time-Weighted Return (TWR):</b> A geometric daily compounding metric that measures pure trading performance by isolating and ignoring external cash deposits or withdrawals.</li>
                    <li style="margin-bottom: 6px;"><b>Profit Factor:</b> The ratio of gross winning trades to gross losing trades (e.g., 2.0x means you make &#36;2 for every &#36;1 you lose).</li>
                    <li style="margin-bottom: 6px;"><b>Premium Capture Rate (PCR):</b> The percentage of upfront premium retained after buying back short options (only evaluates credit-opening trades).</li>
                    <li style="margin-bottom: 6px;"><b>Closed Trades Win Rate:</b> The percentage of all closed trades that resulted in a positive Net P&L.</li>
                    <li style="margin-bottom: 6px;"><b>Buying Power Util (BPU):</b> The percentage of your total account balance currently held as maintenance margin for active positions.</li>
                    <li><b>Account Balance:</b> Your live, direct Net Liquidating Value pulled from the Tastytrade API.</li>
                </ul>
            </div>
        </div>
    </div>
    """

def format_currency(val):
    if val == 0:
        return "$0.00"
    return f"${val:,.2f}" if val > 0 else f"-${abs(val):,.2f}"

def render_metric(label, value, subtext, condition=None):
    if condition is None:
        color = "#d1d5db" 
        bg_color = "rgba(209, 213, 219, 0.25)" 
        arrow = ""
    else:
        if condition > 0:
            color = "#00cc96" 
            bg_color = "rgba(0, 204, 150, 0.15)"
            arrow = "↑&nbsp;"
        elif condition < 0:
            color = "#ff4b4b" 
            bg_color = "rgba(255, 75, 75, 0.15)"
            arrow = "↓&nbsp;"
        else:
            color = "#d1d5db"
            bg_color = "rgba(209, 213, 219, 0.25)"
            arrow = ""
            
    return f"""
    <div style="background-color: #1f2937; border: 1px solid #374151; padding: 16px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); height: 100%;">
        <div style="font-size: 14px; color: rgba(250, 250, 250, 0.6); padding-bottom: 4px;">{label}</div>
        <div style="font-size: 1.8rem; color: white; padding-bottom: 10px; font-weight: 500;">{value}</div>
        <div style="display: inline-block; background-color: {bg_color}; color: {color}; padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; font-weight: 500;">
            {arrow}{subtext}
        </div>
    </div>
    """