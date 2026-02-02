from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from dotenv import load_dotenv
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes
)
from datetime import datetime
import pandas as pd
from google.oauth2.service_account import Credentials
import os, gspread, logging
from gspread_formatting import *
from decouple import config
import azure.functions as func
import json
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects  # Add this import
import matplotlib
matplotlib.use('Agg')  # Required for headless environments
import io

import re,os
from collections import Counter

from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.quote.quote_client import QuoteClient
from tigeropen.trade.trade_client import TradeClient
from tigeropen.common.consts import OrderStatus
from dotenv import dotenv_values

from datetime import datetime, timedelta
import pandas as pd
import numpy as np

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
VIEW_PERFORMANCE, SET_TARGET, SELECT_MONTH, CONFIRM_TARGET, SELECT_CUSTOM_RANGE = range(5)

load_dotenv()

# Load tiger broker config
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
trade_client = TradeClient(client_config)
quote_client = QuoteClient(client_config)

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
    user = update.effective_user
    
    # Security check
    if user.id != TELEGRAM_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return
    
    welcome_message = (
        f"Welcome {user.first_name}!\n\n"
        "📊 Options Trading Tracker\n\n"
        "Available Commands:\n"
        "• /refresh - Refresh trade data from Tiger Broker\n"
        "• /performance - View performance metrics\n"
        "• /collateral - View options collateral\n"
        "• /positions - View option/stock positions\n"
        "• /set_target - Set monthly target\n"
        "• /help - Show help message\n"
    )
    
    await update.message.reply_text(welcome_message)  # REMOVE parse_mode='Markdown'

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a help message"""
    help_text = (
        "*Bot Commands:*\n\n"
        "• /start - Start the bot\n"
        "• /refresh - Refresh trade data from Tiger Broker\n"
        "• /performance - View performance metrics\n"
        "• /collateral - View options collateral\n"
        "• /positions - View option/stock positions\n"
        "• /set_target - Set monthly target\n"
        "• /help - Show this help message\n\n"
        "*Performance Options:*\n"
        "• Monthly performance\n"
        "• YTD performance\n"
        "• Custom date range\n"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def refresh_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refresh trade data from Tiger Broker"""
    await update.message.reply_text("🔄 Refreshing trade data from Tiger Broker...")
    
    try:
        # Execute your trade data fetching script
        # Since we can't directly run the script, we'll import its main logic
        from fetch_trades import fetch_and_update_trades
        
        # Run the fetch trades function
        status,message = fetch_and_update_trades()
        
        # Send detailed status to user
        await update.message.reply_text(
            f"✅ Trade refresh completed!\n"
            f"{message}\n\n"
            f"Use /performance to view updated metrics.\n"
            f"Use /collateral to view updated collateral.\n"
            f"Use /positions to view updated option/stock positions."
        )
        
    except Exception as e:
        logger.error(f"Error refreshing trades: {e}")
        await update.message.reply_text(f"❌ Error refreshing trades: {str(e)}")

async def view_performance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start performance conversation"""
    keyboard = [['📅 Month-to-date', '📈 Year-to-date', '📊 Custom Range', '❌ Cancel']]
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
    """Generate and send a performance chart"""

    # Handle empty data case
    if not strategy_data:
        fig, ax = plt.subplots(figsize=(10, 8))

        ax.text(
            0.5, 0.55,
            "No MTD performance data found",
            ha="center",
            va="center",
            fontsize=20,
            fontweight="bold",
            transform=ax.transAxes
        )

        ax.text(
            0.5, 0.45,
            "Trades will appear here once recorded",
            ha="center",
            va="center",
            fontsize=12,
            color="gray",
            transform=ax.transAxes
        )

        ax.set_title(
            f"{title}\nNet Total: $0.00",
            fontsize=18,
            fontweight="bold",
            pad=20
        )

        ax.axis("off")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        buf.seek(0)
        plt.close()

        return buf

    # Prepare data for pie chart when data exists
    strategies = list(strategy_data.keys())
    profits = list(strategy_data.values())

    # Use absolute values for pie sizing
    total = sum(profits)
    sizes = [abs(p) for p in profits]
    total_abs = sum(sizes)

    if total == 0:
        fig, ax = plt.subplots(figsize=(10, 8))
        ax.text(0.5, 0.5, "No data to display", 
                horizontalalignment='center', verticalalignment='center',
                transform=ax.transAxes, fontsize=14)
        ax.axis('off')
        ax.set_title(f'{title}\nTotal: $0.00', fontsize=14, fontweight='bold', pad=20)

    def autopct_format(pct):
        if total_abs == 0:
            return ''
        value = pct * total_abs / 100
        return f'{pct:.1f}%'

    plt.figure(figsize=(10, 8))

    colors = plt.cm.Set3(np.linspace(0, 1, len(strategies)))
    wedges, texts, autotexts = plt.pie(
        sizes,                      # ✅ IMPORTANT
        labels=strategies,
        autopct=autopct_format,
        startangle=90,
        colors=colors,
        textprops={'fontsize': 15}
    )

    # ✅ OVERWRITE AUTOTEXT TO INCLUDE SIGNED VALUE
    for i, autotext in enumerate(autotexts):
        sign = '-' if profits[i] < 0 else '+'
        value = abs(profits[i])

        autotext.set_text(
            f"{autotext.get_text()}\n({sign}${value:,.2f})"
        )

        # Optional: red for losses
        if profits[i] < 0:
            autotext.set_color('red')

    plt.title(
        f'{title}\nNet Total: ${sum(profits):,.2f}',
        fontsize=18,
        fontweight='bold',
        pad=20
    )

    plt.axis('equal')

    plt.legend(
        wedges,
        strategies,
        title="Strategies",
        loc="upper left",
        bbox_to_anchor=(1, 0, 0.5, 1)
    )

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()

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
    keyboard = [
        ['3 Months', '6 Months'],
        ['9 Months', '12 Months'],
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

async def generate_performance_table(periods: list, profits: list, profits_with_stk: list, targets: list, months: int) -> str:
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
    
    # Add summary section
    table += "📈 *Summary*\n"
    table += f"• Total Period: {months} months\n"
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
        avg_target = total_target / len(periods) if periods else 0
        
        table += f"• Avg Monthly Profit: `${avg_profit:,.2f}` ({delta_indicator(avg_profit, avg_target)})\n"
        table += f"• Avg Monthly (w/STK): `${avg_profit_with_stk:,.2f}` ({delta_indicator(avg_profit_with_stk, avg_target)})\n"
        table += f"• Avg Monthly Target: `${avg_target:,.2f}`\n"
    
    # Best/Worst months
    if profits:
        best_month_idx = profits.index(max(profits))
        worst_month_idx = profits.index(min(profits))
        
        best_month_period = periods[best_month_idx]
        worst_month_period = periods[worst_month_idx]
        
        table += f"• Best Month ({best_month_period}): `${max(profits):,.2f}`\n"
        table += f"• Worst Month ({worst_month_period}): `${min(profits):,.2f}`\n"
    
    return table

async def generate_mom_chart(periods: list, profits: list, profits_with_stk: list, months: int) -> io.BytesIO:
    """Generate Month-over-Month profit trend chart with both profit and profit+STK"""
    
    if not periods or not profits or not profits_with_stk:
        # Create empty chart
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.text(0.5, 0.5, "No data available", 
                ha='center', va='center', fontsize=14)
        ax.set_title(f"{months}-Month Performance Trend", fontsize=16, fontweight='bold')
        ax.axis('off')
        
    else:
        # Create figure with subplots - make it wider to accommodate everything
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), 
                                      gridspec_kw={'height_ratios': [3, 1]})
        
        # Reverse data to show chronological order (oldest to newest)
        periods_rev = periods[::-1]
        profits_rev = profits[::-1]
        profits_with_stk_rev = profits_with_stk[::-1]
        
        # Calculate cumulative profits
        cumulative_profit = np.cumsum(profits_rev)
        cumulative_profit_stk = np.cumsum(profits_with_stk_rev)
        
        # Main plot: Profit bars and cumulative lines
        x = np.arange(len(periods_rev))
        width = 0.35  # Narrower bars for side-by-side display
        
        # Create bars for both profit types (side-by-side)
        x1 = x - width/2
        x2 = x + width/2
        
        bars_profit = ax1.bar(x1, profits_rev, width, 
                             color='lightgreen', alpha=0.7, label='Monthly Profit')
        bars_profit_stk = ax1.bar(x2, profits_with_stk_rev, width,
                                 color='lightblue', alpha=0.7, label='Monthly Profit (w/STK)')
        
        
        # Add cumulative profit lines (secondary axis)
        ax1_cum = ax1.twinx()
        line_cum_profit, = ax1_cum.plot(x, cumulative_profit, 
                                       color='darkgreen', marker='o', linewidth=2, 
                                       markersize=6, linestyle = "--", label='Cumulative Profit')
        line_cum_profit_stk, = ax1_cum.plot(x, cumulative_profit_stk, 
                                           color='darkblue', marker='s', linewidth=2, 
                                           markersize=6, linestyle='--', label='Cumulative Profit (w/STK)')
        # Customize main plot
        ax1.set_xlabel('Period', fontsize=12)
        ax1.set_ylabel('Monthly Profit ($)', fontsize=12, color='black')
        ax1.set_title(f'{months}-Month Performance Trend', fontsize=16, fontweight='bold', pad=20)
        ax1.set_xticks(x)
        ax1.set_xticklabels(periods_rev, rotation=45, ha='right', fontsize=10)
        ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
        ax1.grid(axis='y', alpha=0.3)
        
        # Customize cumulative axis - move label position
        ax1_cum.set_ylabel('Cumulative Profit ($)', fontsize=12, color='black')
        ax1_cum.yaxis.set_label_position("right")
        ax1_cum.yaxis.tick_right()
        
        # Create legend with better positioning
        from matplotlib.patches import Patch
        legend_elements = [
            Patch(facecolor='lightgreen', alpha=0.7, label='Monthly Profit'),
            Patch(facecolor='lightblue', alpha=0.7, label='Monthly Profit (w/STK)'),
            line_cum_profit,
            line_cum_profit_stk
        ]
        
        # Move legend further to the right (1.15 instead of 1.02)
        legend = ax1.legend(handles=legend_elements, loc='center left', 
                            bbox_to_anchor=(1.15, 0.5), fontsize=9, frameon=True,
                            fancybox=True, shadow=True, borderpad=1)
        
        # Bottom plot: Performance metrics
        ax2.axis('off')
        
        # Calculate metrics for both profit types
        total_profit = sum(profits_rev)
        total_profit_stk = sum(profits_with_stk_rev)
        avg_profit = np.mean(profits_rev)
        avg_profit_stk = np.mean(profits_with_stk_rev)
        max_profit = max(profits_rev)
        max_profit_stk = max(profits_with_stk_rev)
        
        # Create metrics text
        metrics_text = (
            f"*Performance Metrics*:\n"
            f"• Total Profit: ${total_profit:,.0f}\n"
            f"• Total Profit (w/STK): ${total_profit_stk:,.0f}\n"
            f"• Avg Monthly: ${avg_profit:,.0f}\n"
            f"• Avg Monthly (w/STK): ${avg_profit_stk:,.0f}\n"
            f"• Best Month: ${max_profit:,.0f}\n"
            f"• Best Month (w/STK): ${max_profit_stk:,.0f}"
        )
        
        # Add metrics to bottom plot
        ax2.text(0.02, 0.5, metrics_text, 
                fontsize=9, fontfamily='monospace',
                verticalalignment='center',
                bbox=dict(boxstyle='round', facecolor='lightgray', alpha=0.3))
    
    # Adjust layout - leave more space on the right for legend
    plt.tight_layout(rect=[0, 0, 0.8, 1])  # Changed from 0.85 to 0.8 (more space)
    
    # Save to buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    return buf

async def show_custom_performance(update: Update, spreadsheet, months: int) -> None:
    """Show custom range performance"""
    sheet = spreadsheet.worksheet('Tiger Trade API Summary')
    
    # Update cell AB14 with selected months
    try:
        sheet.update('AB14', [[months]])
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
    table_text = await generate_performance_table(periods, profits, profits_with_stk, targets, months)
    await update.message.reply_text(
        f"📊 *Custom Range Performance ({months} Months)*\n\n{table_text}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove()
    )

    # 2. Create and send M-o-M chart with both profit types
    chart_buffer = await generate_mom_chart(periods, profits, profits_with_stk, months)
    
    caption = f"📈 {months}-Month Performance Trend\n"
    caption += f"Total Profit: ${sum(profits):,.0f}\n"
    caption += f"Total Profit (w/STK): ${sum(profits_with_stk):,.0f}"
    
    await update.message.reply_photo(
        photo=chart_buffer,
        caption=caption
    )

def main():
    """Start the bot"""
    application = Application.builder().token(TOKEN).build()

    # Conversation handler for 
    # Refresh new trade data
    # View Month performance
    # View YTD performance
    # View options collateral
    # View option stock positions
    # Set new mth target

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("refresh", refresh_trades))
    
    # Performance conversation handler
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
        fallbacks=[CommandHandler("cancel", lambda u, c: u.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove()))],
        allow_reentry=True
    )
    
    application.add_handler(performance_conv_handler)

    # Start the bot
    application.run_polling()

if __name__ == '__main__':
    main()