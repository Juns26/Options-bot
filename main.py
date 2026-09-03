from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from dotenv import load_dotenv
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes
)
from google.oauth2.service_account import Credentials
import os, gspread, logging
import html
import re
from gspread_formatting import *
from decouple import config
import io, os
import plotly.graph_objects as go
import plotly.express as px 

from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.quote.quote_client import QuoteClient
from tigeropen.trade.trade_client import TradeClient

import numpy as np

import asyncio
import os

# Configure logging (must be before analyze_agent import so warning can use logger)
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Analyze Agent integration (LangGraph) — optional, for natural language queries like "Profit in August 2026"
try:
    from analyze_agent import app as analyze_agent_app
    ANALYZE_AGENT_AVAILABLE = True
except Exception as _e:
    analyze_agent_app = None
    ANALYZE_AGENT_AVAILABLE = False
    logger.warning(f"Analyze agent not available: {_e}")

# Backward compat alias
AGENT_AVAILABLE = ANALYZE_AGENT_AVAILABLE

# Conversation states
VIEW_PERFORMANCE, SELECT_MONTH, CONFIRM_TARGET, SELECT_CUSTOM_RANGE = range(4)

load_dotenv()

# Load tiger broker config
def get_client_config():
    """
    https://quant.itigerup.com/#developer Get developer information
    Supports both legacy `client_config.*` and current `TIGER_*` env names.
    """
    client_config = TigerOpenClientConfig()
    # private_key: handle quoted value and escaped \n (Google style) vs raw base64 (Tiger style)
    raw_key = (
        os.getenv("client_config.private_key")
        or os.getenv("TIGER_PRIVATE_KEY")
        or ""
    )
    if raw_key:
        raw_key = raw_key.strip().strip('"').strip("'").replace("\\n", "\n")
    client_config.private_key = raw_key
    client_config.tiger_id = os.getenv("client_config.tiger_id") or os.getenv("TIGER_ID") or ""
    client_config.account = os.getenv("client_config.account") or os.getenv("TIGER_ACCOUNT") or ""
    client_config.license = os.getenv("client_config.license") or os.getenv("TIGER_LICENSE") or ""
    return client_config

client_config = get_client_config()
# Lazy init: don't crash at import if Tiger keys missing (e.g. running bot without refresh)
try:
    if client_config.private_key:
        trade_client = TradeClient(client_config)
        quote_client = QuoteClient(client_config)
    else:
        trade_client = None
        quote_client = None
        logger.warning("Tiger private key empty — TradeClient not initialized.")
except Exception as e:
    logger.warning(f"Tiger client init failed: {e}")
    trade_client = None
    quote_client = None

# Step 1: Define the scope and load credentials
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',  # Access to Google Sheets
    'https://www.googleapis.com/auth/drive'          # Access to Google Drive
]

# Telegram user ID for authorized access
TELEGRAM_USER_ID = int(config("TELEGRAM_USER_ID"))
# Telegram bot token
TOKEN = config("TELEGRAM_BOT_TOKEN")

# Replace with your Google Sheet's name
SPREADSHEET_NAME = 'Options Tracker'

def is_admin(user_id: int) -> bool:
    return user_id == TELEGRAM_USER_ID

# Initialize Google Sheets connection
def init_google_sheets():
    """Initialize Google Sheets connection"""
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    
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

    # Authenticate and open the Google Sheet
    client = gspread.authorize(creds)
    # Access the spreadsheet
    spreadsheet = client.open(SPREADSHEET_NAME)
    return spreadsheet

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the command /start is issued."""
    welcome_message = (
        f"Welcome!\n\n"
        "📊 Options Trading Tracker\n\n"
        "Available Commands:\n"
        "• /analyze <question> - Ask portfolio (e.g. /analyze Profit in August 2026)\n"
        "• /refresh - Refresh trade data (admin only)\n"
        "• /performance - View performance metrics (admin only)\n"
        "• /get_position - View positions (admin only)\n"
        "• /set_target - Set monthly target (admin only)\n"
        "• /help - Show help message\n"
    )
    
    await update.message.reply_text(welcome_message, parse_mode = "HTML")  # REMOVE parse_mode='Markdown'

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message"""
    help_text = (
        "<b>Bot Commands:</b>\n\n"
        "• /start - Start the bot\n"
        "• /analyze &lt;question&gt; - Ask portfolio (open to all)\n"
        "  e.g. /analyze Profit in August 2026 and plot pie by strategy\n"
        "• /refresh - Refresh trade data (admin only)\n"
        "• /performance - View performance metrics (admin only)\n"
        "• /get_position - View positions (admin only)\n"
        "• /set_target - Set monthly target (admin only)\n"
        "• /help - Show this help message\n\n"
        "<b>Performance Options:</b>\n"
        "• Monthly performance\n"
        "• YTD performance\n"
        "• Year-on-Year\n"
        "• Custom date range"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

async def refresh_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to refresh trades.")
        return


    """Refresh trade data from Tiger Broker"""
    await update.message.reply_text("🔄 Refreshing trade data from Tiger Broker...")
    
    try:
        # Execute your trade data fetching script
        # Since we can't directly run the script, we'll import its main logic
        from fetch_trades import fetch_and_update_trades
        
        # Run the fetch trades function
        status,message = fetch_and_update_trades(client_config)
        
        # Send detailed status to user
        await update.message.reply_text(
            f"✅ Trade refresh completed!\n"
            f"{message}\n\n"
            f"Use /performance to view updated metrics.\n"
            f"Use /get_position to view updated option/stock positions."
        )
        
    except Exception as e:
        logger.error(f"Error refreshing trades: {e}")
        await update.message.reply_text(f"❌ Error refreshing trades: {str(e)}")

async def view_performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start performance conversation — admin only"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to view performance. Use /analyze for portfolio queries.")
        return ConversationHandler.END
    keyboard = [
        ['📅 Month-to-date', '📈 Year-to-date'], 
        ['📊 Custom Range', '📅 Year-on-Year'],
        ['❌ Cancel']
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Select performance view:",
        reply_markup=reply_markup
    )
    
    return VIEW_PERFORMANCE

async def handle_performance_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle performance selection"""
    user_choice = update.message.text
    spreadsheet = init_google_sheets()
    
    try:
        if user_choice == '📅 Month-to-date':
            await show_mtd_performance(update, spreadsheet)
        elif user_choice == '📈 Year-to-date':
            await show_ytd_performance(update, spreadsheet)
        elif user_choice == '📊 Custom Range':
            return await view_custom_range(update, context)
        elif user_choice == '📅 Year-on-Year':  # NEW OPTION
            await show_year_on_year_performance(update, spreadsheet)
        elif user_choice == '❌ Cancel':
            await update.message.reply_text("Performance view cancelled.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error in performance: {e}")
        await update.message.reply_text(f"Error: {str(e)}")
    
    return ConversationHandler.END

def parse_money(value: str) -> float:
    if not value:
        return 0.0

    value = (
        value.replace('U$', '')
             .replace('$', '')
             .replace(',', '') 
             .strip()
    )

    if value in ("-", ""):
        return 0.0

    return float(value)

def delta_indicator(current: float, base: float) -> str:
    delta = current - base
    if delta > 0:
        return f"▲+${delta:,.0f}"
    elif delta < 0:
        return f"▼-${abs(delta):,.0f}"
    else:
        return " ■ $0"

async def generate_pie_chart(update: Update, strategy_data: dict, title: str) -> io.BytesIO:
    """Generate performance pie chart with Plotly (clean, no matplotlib)."""
    COLOR_SEQ = ["#636EFA", "#00CC96", "#EF553B", "#AB63FA", "#FFA15A", "#19D3F3", "#FF6692", "#B6E880"]

    # --- empty ---
    if not strategy_data or sum(strategy_data.values()) == 0:
        fig = go.Figure()
        fig.add_annotation(text="No MTD performance data found<br><span style='color:gray'>Trades will appear here once recorded</span>",
                           x=0.5, y=0.55, showarrow=False, font=dict(size=18))
        fig.update_layout(title=dict(text=f"{title}<br><span style='font-size:12px'>Net Total: $0.00</span>", x=0.5, xanchor="center"),
                          template="plotly_white", margin=dict(l=20, r=20, t=80, b=20))
        fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
        buf = io.BytesIO()
        buf.write(fig.to_image(format="png", scale=2))
        buf.seek(0)
        return buf

    strategies = list(strategy_data.keys())
    profits = list(strategy_data.values())
    total = sum(profits)
    # Plotly pie uses absolute values for slice size, keep signed value in hover
    abs_vals = [abs(p) for p in profits]
    labels = [f"{s} ({'+' if p>=0 else '-'}${abs(p):,.2f})" for s, p in zip(strategies, profits)]

    fig = go.Figure(data=[go.Pie(
        labels=strategies,
        values=abs_vals,
        customdata=[[p] for p in profits],
        hovertemplate="%{label}<br>Net: $%{customdata[0]:,.2f}<br>%{percent}<extra></extra>",
        text=[f"{s}<br>{p:+,.2f}" for s, p in zip(strategies, profits)],
        textinfo="label+percent",
        marker=dict(colors=COLOR_SEQ[:len(strategies)]),
        hole=0.35,
    )])
    fig.update_layout(
        title=dict(text=f"{title}<br><span style='font-size:13px'>Net Total: ${total:,.2f}</span>", x=0.5, xanchor="center", font=dict(size=18)),
        template="plotly_white",
        margin=dict(l=20, r=20, t=80, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
        height=500, width=700,
    )
    buf = io.BytesIO()
    buf.write(fig.to_image(format="png", scale=2))
    buf.seek(0)
    return buf


async def show_mtd_performance(update: Update, spreadsheet) -> None:
    """Show month-to-date performance"""
    sheet = spreadsheet.worksheet('Tiger Trade API Summary')

    title_label = sheet.acell('AA4').value
    total_value = sheet.acell('AB5').value
    target_value = sheet.acell('AC5').value
    prev_total_value = sheet.acell('AD5').value

    # Clean summary values
    clean_total = parse_money(total_value)
    clean_prev_total = parse_money(prev_total_value)
    clean_target = parse_money(target_value)

    # Collect data in a single pass
    strategy_data = {}
    mtd_performance = []

    if clean_total ==0:
        chart_buffer = await generate_pie_chart(
            update,
            strategy_data={},          # empty → placeholder plot
            title=title_label
        )

        await update.message.reply_text(
            "No MTD performance data found.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_photo(
            photo=chart_buffer,
            caption="📈 Monthly Performance Chart"
        )
        return

    # Calculate indicators
    vs_target = delta_indicator(clean_total, clean_target)
    vs_prev = delta_indicator(clean_total, clean_prev_total)
    
    for row in range(7, 13):  # Rows 7 to 12
        # Get current performance data
        strategy = sheet.acell(f'AA{row}').value
        current_profit = sheet.acell(f'AB{row}').value
        previous_profit = sheet.acell(f'AD{row}').value
        
        if not strategy or not current_profit:
            continue

        # Clean and process values
        profit_float = parse_money(current_profit)
        prev_float = parse_money(previous_profit)
        
        # Store data for chart (only non-zero values)
        if profit_float != 0:
            strategy_data[strategy] = profit_float
        
        # Create performance text line
        indicator = delta_indicator(profit_float, prev_float)
        line = f"• *{strategy}*: ${profit_float:,.2f} ({indicator})"
        mtd_performance.append(line)

    performance_text = "\n".join(mtd_performance)

    # Generate chart
    chart_buffer = await generate_pie_chart(
        update,
        strategy_data=strategy_data,
        title = title_label
    )

    await update.message.reply_text(
        f"📊 *{title_label}*\n\n"
        f"*Total:* ${clean_total:,.2f}\n"
        f"*Target:* ${clean_target:,.2f} ({vs_target})\n"
        f"*Prev Month:* ${clean_prev_total:,.2f} ({vs_prev})\n\n"
        f"*Strategy Breakdown:*\n{performance_text}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    # Send chart as photo
    await update.message.reply_photo(
        photo=chart_buffer,
        caption="📈 Monthly Performance Chart"
    )

async def show_ytd_performance(update: Update, spreadsheet) -> None:
    """Show year-to-date performance"""
    sheet = spreadsheet.worksheet('Tiger Trade API Summary')

    title_label = sheet.acell('AF4').value  # Should be "{YTD YY} Performance"
    total_value = sheet.acell('AG5').value  # Total YTD value
    target_value = sheet.acell('AH5').value  # YTD target value
    prev_total_value = sheet.acell('AI5').value  # Previous YTD total value

    # Clean summary values
    clean_total = parse_money(total_value)
    clean_prev_total = parse_money(prev_total_value)
    clean_target = parse_money(target_value)

    # Collect data in a single pass
    strategy_data = {}
    ytd_performance = []

    if clean_total == 0:
        chart_buffer = await generate_pie_chart(
            update,
            strategy_data={},          # empty → placeholder plot
            title=title_label
        )

        await update.message.reply_text(
            "No YTD performance data found.",
            reply_markup=ReplyKeyboardRemove()
        )

        await update.message.reply_photo(
            photo=chart_buffer,
            caption="📈 Year-to-Date Performance Chart"
        )
        return

    # Calculate indicators
    vs_target = delta_indicator(clean_total, clean_target)
    vs_prev = delta_indicator(clean_total, clean_prev_total)
    
    for row in range(7, 13):  # Rows 7 to 12
        # Get YTD performance data
        strategy = sheet.acell(f'AF{row}').value
        current_profit = sheet.acell(f'AG{row}').value
        previous_profit = sheet.acell(f'AI{row}').value
        
        if not strategy or not current_profit:
            continue

        # Clean and process values
        profit_float = parse_money(current_profit)
        prev_float = parse_money(previous_profit)
        
        # Store data for chart (only non-zero values)
        if profit_float != 0:
            strategy_data[strategy] = profit_float
        
        # Create performance text line
        indicator = delta_indicator(profit_float, prev_float)
        line = f"• *{strategy}*: ${profit_float:,.2f} ({indicator})"
        ytd_performance.append(line)

    performance_text = "\n".join(ytd_performance)

    # Generate chart
    chart_buffer = await generate_pie_chart(
        update,
        strategy_data=strategy_data,
        title=title_label
    )

    await update.message.reply_text(
        f"📊 *{title_label}*\n\n"
        f"*Total:* ${clean_total:,.2f}\n"
        f"*Target:* ${clean_target:,.2f} ({vs_target})\n"
        f"*Previous YTD:* ${clean_prev_total:,.2f} ({vs_prev})\n\n"
        f"*Strategy Breakdown:*\n{performance_text}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    # Send chart as photo
    await update.message.reply_photo(
        photo=chart_buffer,
        caption="📈 Year-to-Date Performance Chart"
    )

# this is to let user select range for 3,6,9,12 months
async def view_custom_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start custom range conversation"""
    if is_admin(update.effective_user.id):
        keyboard = [
            ['📅 Month-to-date', '📈 Year-to-date'], 
            ['📊 Custom Range', '📅 Year-on-Year'],
            ['❌ Cancel']
        ]
    else:
        keyboard = [
            ['📅 Month-to-date', '📈 Year-to-date'], 
            ['📅 Year-on-Year'],
            ['❌ Cancel']
        ]

    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Select custom range period:",
        reply_markup=reply_markup
    )
    
    return SELECT_CUSTOM_RANGE

async def handle_custom_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom range selection"""
    user_choice = update.message.text
    spreadsheet = init_google_sheets()
    
    try:
        if user_choice == '❌ Cancel':
            await update.message.reply_text("Custom range cancelled.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        
        # Map user choice to months
        months_mapping = {
            '3 Months': 3,
            '6 Months': 6,
            '9 Months': 9,
            '12 Months': 12
        }

        selected_months = months_mapping.get(user_choice)
        
        if selected_months:
            await show_custom_performance(update, spreadsheet, selected_months)
        else:
            await update.message.reply_text(
                "Invalid selection. Please use the keyboard options.",
                reply_markup=ReplyKeyboardRemove()
            )
            
    except Exception as e:
        logger.error(f"Error in custom range: {e}")
        await update.message.reply_text(f"Error: {str(e)}", reply_markup=ReplyKeyboardRemove())
    
    return ConversationHandler.END

async def generate_performance_table(
    periods: list, 
    profits: list, 
    profits_with_stk: list, 
    targets: list, 
    period_count: int,  # Changed from 'months' to 'period_count'
    period_type: str = "month"  # Add this parameter
) -> str:
    """Generate a formatted markdown table of performance data"""
    
    if not periods:
        return "📊 *No data available for this period.*"
    
    # Create markdown table header
    table = ""
    table += "```\n"
    table += "| Period | Profit | Profit+STK | Target | Δ Profit | Δ Profit+STK |\n"
    table += "|--------|--------|------------|--------|----------|--------------|\n"
    
    total_profit = 0
    total_profit_with_stk = 0
    total_target = 0
    total_delta_profit = 0
    total_delta_stk = 0
    
    # Add each row to table
    for period, profit, profit_with_stk, target in zip(periods, profits, profits_with_stk, targets):
        # Calculate deltas vs target
        delta_profit = profit - target
        delta_stk = profit_with_stk - target
        
        # Format values
        profit_fmt = f"${profit:,.0f}"
        profit_with_stk_fmt = f"${profit_with_stk:,.0f}"
        target_fmt = f"${target:,.0f}"
        
        # Use delta_indicator for both deltas
        delta_profit_fmt = delta_indicator(profit, target)
        delta_stk_fmt = delta_indicator(profit_with_stk, target)
        
        table += f"| {period} | {profit_fmt} | {profit_with_stk_fmt} | {target_fmt} | {delta_profit_fmt} | {delta_stk_fmt} |\n"
        
        # Accumulate totals
        total_profit += profit
        total_profit_with_stk += profit_with_stk
        total_target += target
        total_delta_profit += delta_profit
        total_delta_stk += delta_stk
    
    table += "```\n\n"
    
    # Add summary section with dynamic labels
    if period_type == "year":
        period_label = "Years"
        avg_label = "Avg Annual"
        best_label = "Best Year"
        worst_label = "Worst Year"
    else:
        period_label = "Months"
        avg_label = "Avg Monthly"
        best_label = "Best Month"
        worst_label = "Worst Month"
    
    table += "📈 *Summary*\n"
    table += f"• Total Period: {period_count} {period_label.lower()}\n"
    table += f"• Total Profit: `${total_profit:,.2f}` ({delta_indicator(total_profit, total_target)})\n"
    table += f"• Total Profit (w/STK): `${total_profit_with_stk:,.2f}` ({delta_indicator(total_profit_with_stk, total_target)})\n"
    table += f"• Total Target: `${total_target:,.2f}`\n\n"
    
    # Target achievement metrics
    if total_target > 0:
        achievement_pct = (total_profit / total_target * 100)
        achievement_stk_pct = (total_profit_with_stk / total_target * 100)
        
        table += f"• Target Achievement: `{achievement_pct:.1f}%`\n"
        table += f"• Target (w/STK): `{achievement_stk_pct:.1f}%`\n"
    
    # Averages
    if periods:
        avg_profit = total_profit / len(periods)
        avg_profit_with_stk = total_profit_with_stk / len(periods)
        avg_target = total_target / len(periods)
        
        table += f"• {avg_label} Profit: `${avg_profit:,.2f}` ({delta_indicator(avg_profit, avg_target)})\n"
        table += f"• {avg_label} (w/STK): `${avg_profit_with_stk:,.2f}` ({delta_indicator(avg_profit_with_stk, avg_target)})\n"
        table += f"• {avg_label} Target: `${avg_target:,.2f}`\n"
    
    # Best/Worst periods
    if profits:
        best_period_idx = profits.index(max(profits))
        worst_period_idx = profits.index(min(profits))
        
        best_period = periods[best_period_idx]
        worst_period = periods[worst_period_idx]
        
        table += f"• {best_label} ({best_period}): `${max(profits):,.2f}`\n"
        table += f"• {worst_label} ({worst_period}): `${min(profits):,.2f}`\n"
    
    return table

async def generate_performance_chart(
    periods: list, 
    profits: list, 
    profits_with_stk: list, 
    chart_type: str = "Month-over-Month"
) -> io.BytesIO:
    """Generate performance trend chart with Plotly (bars + cumulative lines, clean)."""
    if not periods or not profits or not profits_with_stk:
        fig = go.Figure()
        fig.add_annotation(text="No data available", x=0.5, y=0.5, showarrow=False, font=dict(size=14))
        fig.update_layout(title=dict(text=f"{chart_type} Performance Trend", x=0.5, xanchor="center"), template="plotly_white", height=400)
        fig.update_xaxes(visible=False); fig.update_yaxes(visible=False)
        buf = io.BytesIO()
        buf.write(fig.to_image(format="png", scale=2))
        buf.seek(0)
        return buf

    # Reverse to chronological (oldest -> newest)
    periods_rev = periods[::-1]
    profits_rev = profits[::-1]
    profits_with_stk_rev = profits_with_stk[::-1]
    cum = np.cumsum(profits_rev)
    cum_stk = np.cumsum(profits_with_stk_rev)
    y_label = 'Annual Profit ($)' if chart_type == "Year-on-Year" else 'Monthly Profit ($)'

    fig = go.Figure()
    # Bars side-by-side
    fig.add_trace(go.Bar(x=periods_rev, y=profits_rev, name="Monthly Profit", marker_color="#7ED957", opacity=0.8))
    fig.add_trace(go.Bar(x=periods_rev, y=profits_with_stk_rev, name="Monthly Profit (w/STK)", marker_color="#6EC1E4", opacity=0.8))
    # Cumulative lines on secondary y
    fig.add_trace(go.Scatter(x=periods_rev, y=cum, name="Cumulative Profit", yaxis="y2",
                             mode="lines+markers", line=dict(color="#1B7A3D", dash="dash", width=2), marker=dict(symbol="circle", size=6)))
    fig.add_trace(go.Scatter(x=periods_rev, y=cum_stk, name="Cumulative (w/STK)", yaxis="y2",
                             mode="lines+markers", line=dict(color="#1E3A8A", dash="dash", width=2), marker=dict(symbol="square", size=6)))

    max_abs = max(max(abs(min(profits_rev)), abs(max(profits_rev))), max(abs(min(cum)), abs(max(cum)))) * 1.1
    fig.update_layout(
        title=dict(text=f"{chart_type} Performance Trend", x=0.5, xanchor="center", font=dict(size=16)),
        xaxis=dict(title="Period", tickangle=0),
        yaxis=dict(title=y_label, zeroline=True, zerolinecolor="black", zerolinewidth=1, range=[-max_abs, max_abs]),
        yaxis2=dict(title="", overlaying="y", side="right", showgrid=False, zeroline=False, range=[-max_abs, max_abs]),
        barmode="group",
        template="plotly_white",
        height=500, width=1100,
        margin=dict(l=50, r=50, t=60, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5),
        hovermode="x unified",
    )
    buf = io.BytesIO()
    buf.write(fig.to_image(format="png", scale=2))
    buf.seek(0)
    return buf

async def show_custom_performance(update: Update, spreadsheet, months: int) -> None:
    """Show custom range performance"""
    sheet = spreadsheet.worksheet('Tiger Trade API Summary')
    
    # Update cell AB14 with selected months
    try:
        sheet.update([[months]],'AB14')
        await update.message.reply_text(f"✅ Updated Gsheet custom period to {months} months")
        
        # Wait a moment for Google Sheets to recalculate
        import time
        time.sleep(2)
        
    except Exception as e:
        logger.error(f"Error updating cell AB14: {e}")
        await update.message.reply_text(f"⚠️ Could not update cell: {str(e)}")
    
    # Read data from cells AD15:AG27 (Period, Profit, Profit with STK, Target)
    data_range = sheet.get('AD16:AG27')
    
    if not data_range:
        await update.message.reply_text(
            "No custom range data found.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Parse data and organize for table and chart
    periods = []
    profits = []
    profits_with_stk = []
    targets = []

    for row in data_range:
        # Check if Period cell is not empty and row has at least 4 columns
        if len(row) >= 1 and row[0] and row[0].strip():
            period = row[0].strip()
            
            # Clean and parse values (handle missing columns gracefully)
            profit = parse_money(row[1]) if len(row) > 1 else 0
            profit_with_stk = parse_money(row[2]) if len(row) > 2 else 0
            target = parse_money(row[3]) if len(row) > 3 else 0
            
            # Store data
            periods.append(period)
            profits.append(profit)
            profits_with_stk.append(profit_with_stk)
            targets.append(target)
    
    # Debug logging
    logger.info(f"Parsed data - Periods: {len(periods)}, Profits: {len(profits)}")
    
    if not periods:
        await update.message.reply_text(
            "No performance data available for this period.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # 1. Create and send table
    table_text = await generate_performance_table(
        periods, profits, profits_with_stk, targets, months, period_type="month"
    )
    await update.message.reply_text(
        f"📊 *Custom Range Performance ({months} Months)*\n\n{table_text}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    # 2. Create and send M-o-M chart with both profit types
    chart_buffer = await generate_performance_chart(periods, profits, profits_with_stk, f"{months}-Month")

    caption = f"📈 {months}-Month Performance Trend\n"
    caption += f"Total Profit: ${sum(profits):,.0f}\n"
    caption += f"Total Profit (w/STK): ${sum(profits_with_stk):,.0f}"
    
    await update.message.reply_photo(
        photo=chart_buffer,
        caption=caption
    )

async def show_year_on_year_performance(update: Update, spreadsheet) -> None:
    """Show year-on-year performance for 5 years"""
    sheet = spreadsheet.worksheet('Tiger Trade API Summary')
    
    # Assuming AD32:AH36 contains:
    # AD: Year labels, AE: Profit, AF: Profit with STK, AG: Target, AH: Previous Year
    year_data = sheet.get('AD32:AH36')
    
    if not year_data or len(year_data) < 5:
        await update.message.reply_text(
            "No year-on-year data available.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Parse the data - adjust columns based on your actual sheet structure
    years = []
    profits = []
    profits_with_stk = []
    targets = []
    
    for row in year_data:
        if len(row) >= 3 and row[0]:  # Ensure we have year and at least profit
            year = str(row[0]).strip()
            profit = parse_money(row[1]) if len(row) > 1 else 0
            profit_with_stk = parse_money(row[2]) if len(row) > 2 else profit  # Fallback to profit
            target = parse_money(row[3]) if len(row) > 3 else 0
            
            years.append(year)
            profits.append(profit)
            profits_with_stk.append(profit_with_stk)
            targets.append(target)
    
    if not years:
        await update.message.reply_text(
            "No year-on-year data found.",
            reply_markup=ReplyKeyboardRemove()
        )
        return
    
    # Generate table using your existing function (modified for years)
    table_text = await generate_performance_table(
        periods=years, 
        profits=profits, 
        profits_with_stk=profits_with_stk, 
        targets=targets,
        period_count=len(years),
        period_type="year"  # Add this
    )
    
    # Send performance summary
    await update.message.reply_text(
        f"📅 *{len(years)}-Year Performance History*\n\n{table_text}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )
    
    # Generate and send chart
    chart_buffer = await generate_performance_chart(years, profits, profits_with_stk, "Year-on-Year")
    
    caption = f"📊 {len(years)}-Year Performance Trend\n"
    caption += f"Total Profit: ${sum(profits):,.0f}\n"
    caption += f"Total Profit (w/STK): ${sum(profits_with_stk):,.0f}"
    
    await update.message.reply_photo(
        photo=chart_buffer,
        caption=caption
    )

async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to set targets.")
        return

    """Set the target value in Google Sheets cell AB15"""
    try:
        # Get the target value from command arguments
        if not context.args:
            await update.message.reply_text(
                "Please provide a target value.\n\n"
                "Usage: `/set_target <amount>`\n"
                "Example: `/set_target 500`"
            )
            return
        
        target_value = context.args[0]
        
        # Validate it's a valid number
        try:
            # Remove any commas, dollar signs, etc.
            cleaned_value = target_value.replace('$', '').replace(',', '').strip()
            float_value = float(cleaned_value)
        except ValueError:
            await update.message.reply_text(
                "Invalid target value. Please provide a valid number.\n\n"
                "Example: `/set_target 500` or `/set_target 500.50`"
            )
            return
        
        # Get the spreadsheet
        spreadsheet = init_google_sheets()
        sheet = spreadsheet.worksheet('Tiger Trade API Summary')
        
        # Update cell AB15 with the target value
        sheet.update([[float_value]], 'AB15')
        
        # Format the display value
        if float_value.is_integer():
            display_value = f"${int(float_value):,}"
        else:
            display_value = f"${float_value:,.2f}"
        
        await update.message.reply_text(
            f"✅ Target updated to {display_value}"
        )
        
        logger.info(f"Target updated in AB15: {display_value}")
        
    except Exception as e:
        logger.error(f"Error setting target: {e}")
        await update.message.reply_text(
            f"⚠️ Error updating target: {str(e)}"
        )

async def get_position(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get detailed stock and options positions — admin only"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You are not authorized to view positions. Use /analyze for queries.")
        return
    await update.message.reply_text("📊 Fetching positions data...")
    
    try:
        spreadsheet = init_google_sheets()
        sheet = spreadsheet.worksheet('Tiger Trade API Summary')
        
        # 1. Get Stock Positions (AL6:AP25)
        stock_positions_data = sheet.get('AL6:AP25')
        
        # 2. Get Options Positions (AL29:AV61)
        options_positions_data = sheet.get('AL29:AV61')
        
        # Parse stock positions
        stock_positions = []
        total_stock_value = 0
        total_stock_pnl = 0
        
        if stock_positions_data:
            for row in stock_positions_data:
                if len(row) >= 5 and row[0]:  # Check if we have at least ticker
                    ticker = str(row[0]).strip()
                    quantity = int(float(row[1])) if len(row) > 1 and row[1] else 0
                    avg_price = parse_money(row[2]) if len(row) > 2 and row[2] else 0
                    current_price = parse_money(row[3]) if len(row) > 3 and row[3] else 0
                    delta = parse_money(row[4]) if len(row) > 4 and row[4] else 0
                    
                    if quantity > 0:  # Only include positions with quantity
                        position_value = quantity * current_price
                        total_stock_value += position_value
                        total_stock_pnl += delta
                        
                        stock_positions.append({
                            'ticker': ticker,
                            'quantity': quantity,
                            'avg_price': avg_price,
                            'current_price': current_price,
                            'delta': delta,
                            'value': position_value,
                            'pnl_pct': (delta / (quantity * avg_price) * 100) if quantity * avg_price > 0 else 0
                        })
        
        # Parse options positions
        options_positions = []
        total_options_collateral = 0
        
        if options_positions_data:
            for row in options_positions_data:
                if len(row) >= 11 and row[0]:  # Check if we have at least ticker
                    ticker = str(row[0]).strip()
                    option_type = str(row[1]).strip() if len(row) > 1 and row[1] else ""
                    trade_date = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                    buy_strike = parse_money(row[3]) if len(row) > 3 and row[3] else 0
                    sell_strike = parse_money(row[4]) if len(row) > 4 and row[4] else 0
                    expiration = str(row[5]).strip() if len(row) > 5 and row[5] else ""
                    strategy = str(row[6]).strip() if len(row) > 6 and row[6] else ""
                    quantity = int(float(row[7])) if len(row) > 7 and row[7] else 0
                    collateral = parse_money(row[8]) if len(row) > 8 and row[8] else 0
                    current_price = parse_money(row[9]) if len(row) > 9 and row[9] else 0
                    status = str(row[10]).strip() if len(row) > 10 and row[10] else ""
                    
                    if quantity > 0:  # Only include positions with quantity
                        total_options_collateral += collateral
                        
                        options_positions.append({
                            'ticker': ticker,
                            'type': option_type,
                            'trade_date': trade_date,
                            'buy_strike': buy_strike,
                            'sell_strike': sell_strike,
                            'expiration': expiration,
                            'strategy': strategy,
                            'quantity': quantity,
                            'collateral': collateral,
                            'current_price': current_price,
                            'status': status
                        })
        
        # Format and send response
        response = "📈 *PORTFOLIO POSITIONS*\n\n"
        
        # Stock Positions Summary
        response += "🏦 *STOCK POSITIONS*\n"
        response += f"Total Positions: {len(stock_positions)}\n"
        response += f"Total Value: ${total_stock_value:,.2f}\n"
        response += f"Total P&L: ${total_stock_pnl:,.2f}\n\n"
        
        # Detailed Stock Positions
        if stock_positions:
            response += "*Stock Details:*\n"
            for pos in sorted(stock_positions, key=lambda x: abs(x['delta']), reverse=True)[:10]:  # Top 10 by P&L
                pnl_sign = "🟢" if pos['delta'] >= 0 else "🔴"
                response += f"{pnl_sign} *{pos['ticker']}*: {pos['quantity']} shares\n"
                response += f"  Avg: ${pos['avg_price']:.2f} | Current: ${pos['current_price']:.2f}\n"
                response += f"  P&L: ${pos['delta']:,.2f} ({pos['pnl_pct']:+.1f}%)\n"
                response += f"  Value: ${pos['value']:,.2f}\n\n"
        
        # Options Positions Summary
        response += "📊 *OPTIONS POSITIONS*\n"
        response += f"Total Positions: {len(options_positions)}\n"
        response += f"Total Collateral: ${total_options_collateral:,.2f}\n\n"
        
        # Options Positions by Strategy
        strategies = {}
        for pos in options_positions:
            strat = pos['strategy'] or "Unknown"
            strategies[strat] = strategies.get(strat, 0) + 1
        
        response += "*Strategies:*\n"
        for strat, count in strategies.items():
            response += f"• {strat}: {count} positions\n"
        response += "\n"

        # Detailed Options Positions
        if options_positions:
            response += "*Options Positions:*\n"
            for pos in options_positions:
                status_emoji = "✅" if pos['status'] == "OTM" else "⚠️"
                response += f"{status_emoji} *{pos['ticker']} {pos['expiration']}*\n"
                response += f"  Strategy: {pos['strategy']} |  Qty: {pos['quantity']}\n"
                
                # Always start with 2 spaces for strike line
                response += "  "
                
                if pos['buy_strike'] > 0:
                    response += f"Buy Strike: ${pos['buy_strike']:.2f}"
                    if pos['sell_strike'] > 0:
                        response += f" | Sell Strike: ${pos['sell_strike']:.2f}"
                elif pos['sell_strike'] > 0:
                    response += f"Sell Strike: ${pos['sell_strike']:.2f}"
                
                # Always add newline after strike line
                response += "\n"
                
                response += f"  Current Price: ${pos['current_price']:.2f}\n"
                response += "\n"
        
        # Send the response
        await update.message.reply_text(
            response,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
            
    except Exception as e:
        logger.error(f"Error fetching positions: {e}")
        await update.message.reply_text(f"❌ Error fetching positions: {str(e)}")

# --- Telegram HTML-safe sending for LLM output (fix for "Can't parse entities") ---
# Root cause: synthesizer used to emit GitHub Markdown (**bold**, net_profit with
# underscores) but handlers sent it with parse_mode="Markdown" (legacy). Any
# unbalanced *, _, `, [ breaks Telegram parsing and the whole reply is rejected
# with "Can't parse entities: can't find end of the entity...".
# Fix: constrain the LLM to HTML (see analyze_agent.py) AND sanitize here with
# a plain-text fallback so one bad entity can never fail the reply again.
TELEGRAM_MSG_LIMIT = 4000
_ALLOWED_TELEGRAM_TAGS_RE = re.compile(
    r'</?(?:b|i|u|s|code|pre|a)(?:\s+href="[^"]*")?\s*/?>',
    re.IGNORECASE,
)


def _sanitize_telegram_html(text: str) -> str:
    """Convert arbitrary LLM output into Telegram-safe HTML.

    - Converts common Markdown leftovers (**bold**, __bold__, `code`,
      [text](url)) to the Telegram HTML subset.
    - Escapes stray &, <, > so they can never open a broken entity.
    - Auto-closes unclosed <b>/<i>/... tags and drops stray closings.
    - Truncates to TELEGRAM_MSG_LIMIT without leaving a half-open tag.
    Only <b>, <i>, <u>, <s>, <code>, <pre>, <a href="..."> are kept.
    """
    if not text:
        return ""
    s = str(text).replace("\x00", "").replace("\r\n", "\n")
    # Markdown links -> HTML links (must run before escaping)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    # **bold** / __bold__ -> <b>
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.DOTALL)
    s = re.sub(r"__(.+?)__", r"<b>\1</b>", s, flags=re.DOTALL)
    # `code` -> <code> (single-line only, avoids breaking ``` blocks)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    # Strip Markdown heading markers (harmless in HTML, just noise)
    s = re.sub(r"(?m)^\s*#{1,6}\s*", "", s)

    # Escape everything except the allowed tags: walk tag matches,
    # html.escape() the text between them, keep the tags verbatim.
    out: list[str] = []
    last = 0
    for m in _ALLOWED_TELEGRAM_TAGS_RE.finditer(s):
        out.append(html.escape(s[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(html.escape(s[last:]))
    s = "".join(out)

    # Truncate safely: never leave a half-open "<... " fragment at the end.
    if len(s) > TELEGRAM_MSG_LIMIT:
        s = s[:TELEGRAM_MSG_LIMIT]
        s = re.sub(r"<[^>]*$", "", s)

    # Balance simple paired tags: auto-close missing, drop stray closings.
    for tag in ("b", "i", "u", "s", "code", "pre", "a"):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", s, flags=re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}\s*>", s, flags=re.IGNORECASE))
        if opens > closes:
            s += f"</{tag}>" * (opens - closes)
        elif closes > opens:
            for _ in range(closes - opens):
                s = re.sub(rf"</{tag}\s*>", "", s, count=1, flags=re.IGNORECASE)
    return s


def _strip_html_to_plain(html_text: str) -> str:
    """Strip allowed Telegram tags back to plain text (fallback path)."""
    if not html_text:
        return ""
    plain = re.sub(
        r"</?(?:b|i|u|s|code|pre|a)(?:\s[^>]*)?>",
        "",
        html_text,
        flags=re.IGNORECASE,
    )
    return html.unescape(plain)


async def _reply_html_safe(message, text: str) -> None:
    """Reply with Telegram HTML; on entity-parse failure retry as plain text.

    Guarantees the user still gets the answer even if the LLM emits markup
    Telegram cannot parse — the original bug surfaced as
    "Analyze agent error: Can't parse entities...".
    """
    safe = _sanitize_telegram_html(text)
    try:
        await message.reply_text(safe, parse_mode="HTML", disable_web_page_preview=True)
    except Exception as e:
        msg = str(e).lower()
        if "parse entities" in msg or "can't parse" in msg or "bad request" in msg:
            logger.warning(f"HTML parse failed, falling back to plain text: {e}")
            plain = _strip_html_to_plain(safe)[:TELEGRAM_MSG_LIMIT]
            await message.reply_text(plain, disable_web_page_preview=True)
        else:
            raise


async def handle_agent_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback handler — forwards any natural language to the LangGraph analyze_agent (fetch/aggregate/plot)."""
    if not ANALYZE_AGENT_AVAILABLE or not analyze_agent_app:
        await update.message.reply_text("🤖 Analyze agent not configured. Set GEMINI_API_KEY in .env")
        return
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    # Ignore exact keyboard texts handled by ConversationHandler (they are caught there first)
    if text in ["📅 Month-to-date", "📈 Year-to-date", "📊 Custom Range", "📅 Year-on-Year", "❌ Cancel"]:
        return
    await update.message.reply_text("🤖 Thinking...")
    try:
        initial_state = {
            "query": text,
            "is_relevant": True,
            "guardrail_reason": "",
            "can_fulfill": True,
            "sandbox_reason": "",
            "plan_summary": "",
            "steps": [],
            "execution_results": [],
            "final_response": "",
            "verbose": False,
        }
        final_state = await asyncio.to_thread(analyze_agent_app.invoke, initial_state)
        execution_results = final_state.get("execution_results", [])
        final_response = final_state.get("final_response", "No response.")
        for r in execution_results:
            res = r.get("result")
            if isinstance(res, dict) and "figure" in res:
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure(res["figure"])
                    buf = io.BytesIO()
                    buf.write(fig.to_image(format="png", scale=2))
                    buf.seek(0)
                    caption = res.get("title") or "Chart"
                    await update.message.reply_photo(photo=buf, caption=f"📊 {caption}")
                except Exception as e:
                    logger.warning(f"Failed to render plot image: {e}")
                    try:
                        html_str = go.Figure(res["figure"]).to_html(full_html=False)
                        await update.message.reply_text(html_str[:3500], disable_web_page_preview=True)
                    except Exception:
                        pass
        await _reply_html_safe(update.message, final_response)
    except Exception as e:
        logger.error(f"Analyze agent error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Analyze agent error: {str(e)[:1000]}")


async def handle_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Parked analyze_agent endpoint: /analyze <your question> — open to all users.

    Example: /analyze Profit in August 2026 and plot pie by strategy
    Restricted: other endpoints (/refresh, /performance, /get_position, /set_target) remain admin-only.
    """
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text(
            "Usage: /analyze <question>\n"
            "Examples:\n"
            "• /analyze Profit in August 2026\n"
            "• /analyze Profit by strategy in August 2026 and plot pie\n"
            "• /analyze What is my YTD profit?"
        )
        return
    if not ANALYZE_AGENT_AVAILABLE or not analyze_agent_app:
        await update.message.reply_text("🤖 Analyze agent not configured. Set GEMINI_API_KEY in .env")
        return
    await update.message.reply_text("🤖 Thinking...")
    try:
        initial_state = {
            "query": query,
            "is_relevant": True,
            "guardrail_reason": "",
            "can_fulfill": True,
            "sandbox_reason": "",
            "plan_summary": "",
            "steps": [],
            "execution_results": [],
            "final_response": "",
            "verbose": False,
        }
        final_state = await asyncio.to_thread(analyze_agent_app.invoke, initial_state)
        execution_results = final_state.get("execution_results", [])
        final_response = final_state.get("final_response", "No response.")
        for r in execution_results:
            res = r.get("result")
            if isinstance(res, dict) and "figure" in res:
                try:
                    import plotly.graph_objects as go
                    fig = go.Figure(res["figure"])
                    buf = io.BytesIO()
                    buf.write(fig.to_image(format="png", scale=2))
                    buf.seek(0)
                    caption = res.get("title") or "Chart"
                    await update.message.reply_photo(photo=buf, caption=f"📊 {caption}")
                except Exception as e:
                    logger.warning(f"Failed to render plot image: {e}")
        await _reply_html_safe(update.message, final_response)
    except Exception as e:
        logger.error(f"Analyze agent error via /analyze: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Analyze agent error: {str(e)[:1000]}")


from aiohttp import web

async def health_check(request):
    return web.Response(text="OK")

async def main():

    # Get PORT from Render environment
    port = int(os.environ.get("PORT", 10000))
    
    # Create HTTP server
    app = web.Application()
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"✅ Health endpoint active at :{port}/health")

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("refresh", refresh_trades))
    application.add_handler(CommandHandler("set_target", set_target))
    application.add_handler(CommandHandler("get_position", get_position))

    performance_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("performance", view_performance)],
        states={
            VIEW_PERFORMANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_performance_selection)
            ],
            SELECT_CUSTOM_RANGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_range)
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                lambda u, c: u.message.reply_text(
                    "Cancelled.", reply_markup=ReplyKeyboardRemove()
                )
            )
        ],
        allow_reentry=True,
    )
    application.add_handler(performance_conv_handler)

    # Parked analyze_agent endpoint: /analyze <question> — open to all users
    # Example: /analyze Profit in August 2026 and plot pie by strategy
    application.add_handler(CommandHandler("analyze", handle_analyze))

    # Fallback natural language is now disabled for non-admins.
    # Only admin can still use free-text analyze_agent via handle_agent_message (optional).
    # Uncomment to allow admin free-text:
    # application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_agent_message))

    # Start the bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    logger.info("✅ Bot started with long polling")

    # 🔒 KEEP THE PROCESS ALIVE
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())