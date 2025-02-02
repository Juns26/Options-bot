from telegram import Update
from dotenv import load_dotenv
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters, ContextTypes
)
from datetime import datetime
import pandas as pd
from google.oauth2.service_account import Credentials
import os, gspread, logging
from gspread_formatting import *

# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Step 1: Define the scope and load credentials
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',  # Access to Google Sheets
    'https://www.googleapis.com/auth/drive'          # Access to Google Drive
]
TELEGRAM_USER_ID = 1019543572
load_dotenv()
print(GOOGLE_PROJECT_ID)

# Load credentials from the downloaded JSON file
creds = Credentials.from_service_account_info(
    {
        "type": "service_account",
        "project_id": os.getenv('GOOGLE_PROJECT_ID'),
        "private_key_id": os.getenv('GOOGLE_PRIVATE_KEY_ID'), 
        "private_key": os.getenv('GOOGLE_PRIVATE_KEY').replace('\\n', '\n'),  # Fix newlines
        "client_email": os.getenv('GOOGLE_CLIENT_EMAIL'),
        "client_id": os.getenv('GOOGLE_CLIENT_ID'),
        "auth_uri": os.getenv('GOOGLE_AUTH_URI'),
        "token_uri": os.getenv('GOOGLE_TOKEN_URI'),
        "auth_provider_x509_cert_url": os.getenv('GOOGLE_AUTH_PROVIDER_X509_CERT_URL'),
        "client_x509_cert_url": os.getenv('GOOGLE_CLIENT_X509_CERT_URL'),
        "universe_domain": "googleapis.com"
    }, 
    scopes=SCOPES
)

# Step 2: Authenticate and open the Google Sheet
client = gspread.authorize(creds)

# Replace with your Google Sheet's name or ID
SPREADSHEET_NAME = 'Options Tracker'

# Telegram bot token
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Conversation states
WAITING_FOR_TRADE = 0
CONFIRM_TRADE = 1

# Strategy to sheet mapping
STRATEGIES = {
    "CSP": "Cash Secured Puts",
    "CC": "Covered Calls",
    "BPS": "Bull Put Spread"
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    await update.message.reply_text(
        '📈 *Options Trading Tracker Bot*\n\n'
        '*Commands:*\n'
        '/add - Record new trade\n'
        '/performance - View last 12 months performance\n'
        '/status - View current status',
        parse_mode="Markdown"
        # Add other commands here
    )

async def add_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter trade details"""
    user_id = update.message.from_user.id
    if user_id == TELEGRAM_USER_ID:
        await update.message.reply_text(
            'Enter trade details in this format:\n\n'
            'Strategy, Date(DD/MM/YYYY), Action, Ticker, Strike, Contract, Expiration(DD/MM/YYYY), Premium, Fees, Trade\n\n'
            'Example:\n'
            'CSP, 14/12/2024, SELL PUT, BABA, 82.5, 1, 17/01/2025, 125, 3.31, Open'
        )
    else:
        await update.message.reply_text("🚫 You are not authorized to use this command.")
    return WAITING_FOR_TRADE

async def handle_trade_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for confirmation after receiving trade details"""
    try:
        parts = [part.strip() for part in update.message.text.split(',')]
        if len(parts) != 10:
            raise ValueError("Invalid number of fields. Please follow the specified format.")

        # Store trade details in context for later confirmation
        context.user_data['trade_details'] = parts  

        trade_summary = (
            f"📊 *Trade Summary:*\n"
            f"Strategy: {parts[0]}\n"
            f"Date: {parts[1]}\n"
            f"Action: {parts[2]}\n"
            f"Ticker: {parts[3]}\n"
            f"Strike: {parts[4]}\n"
            f"Contract: {parts[5]}\n"
            f"Expiration: {parts[6]}\n"
            f"Premium: {parts[7]}\n"
            f"Fees: {parts[8]}\n"
            f"Trade: {parts[9]}\n\n"
            "✅ Confirm? Reply with *Yes* or *No*."
        )

        await update.message.reply_text(trade_summary,parse_mode="Markdown")
        return CONFIRM_TRADE

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        await update.message.reply_text(f'❌ Error: {str(e)}. Please try again.')
        return ConversationHandler.END

async def confirm_trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process trade if user confirms"""
    user_response = update.message.text.strip().lower()

    if user_response == "yes":
        parts = context.user_data.get('trade_details')
        if not parts:
            await update.message.reply_text("❌ No trade details found. Please restart with /add.")
            return ConversationHandler.END
        
        # Extract and validate fields
        strategy = parts[0].upper()
        if strategy not in STRATEGIES:
            await update.message.reply_text(f"❌ Unsupported strategy: {strategy}. Supported strategies: {list(STRATEGIES.keys())}")
            return ConversationHandler.END

        sheet_name = STRATEGIES[strategy]

        trade_data = {
            'Date': parts[1],
            'Action': parts[2].upper(),
            'Ticker': parts[3].upper(),
            'Strike Price': float(parts[4]),
            'Contract': int(parts[5]),
            'Expiration date': parts[6],
            'Premium': float(parts[7]),
            'Fees': float(parts[8]),
            'Trade': parts[9]
        }

        trade_data["Collateral"] = trade_data["Strike Price"] * trade_data['Contract'] * 100 * (-1 if trade_data["Action"]=='BUY PUT' else 1)
        trade_data["Net Profit"] = trade_data['Premium'] - trade_data["Fees"]
        trade_data["Year / Month"] = datetime.strptime(trade_data['Date'], "%d/%m/%Y").strftime("%Y %b")

        # Open the Google Sheet
        try:
            spreadsheet = client.open(SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            print(f"Spreadsheet '{SPREADSHEET_NAME}' not found.")
            exit()

        # Select the sheet
        if sheet_name not in [sheet.title for sheet in spreadsheet.worksheets()]:
            await update.message.reply_text(f"❌ Sheet '{sheet_name}' does not exist in the Google Sheet.")
            return ConversationHandler.END
        
        sheet = spreadsheet.worksheet(sheet_name)

        # Get column indexes
        headers = sheet.row_values(1)
        header_to_index = {header: idx + 1 for idx, header in enumerate(headers)}

        row_num = len(sheet.get_all_values()) + 1

        for column_name, value in trade_data.items():
            column_index = header_to_index.get(column_name)
            if column_index:
                sheet.update_cell(row_num, column_index, value)
                apply_formatting(sheet, row_num, column_index)
            else:
                print(f"Column '{column_name}' not found in the sheet.")

        await update.message.reply_text('✅ Trade added successfully!')
        return ConversationHandler.END

    elif user_response == "no":
        await update.message.reply_text("❌ Trade entry canceled. Enter trade details again.")
        return WAITING_FOR_TRADE

    else:
        await update.message.reply_text("❌ Invalid response. Please reply with *Yes* or *No*.",parse_mode="Markdown")
        return CONFIRM_TRADE

async def get_performance(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        sheet = spreadsheet.worksheet('Tracker')

        # get performance
        periods = sheet.get("A6:A17")
        profits = sheet.get("D6:D17")

        ytd_table = "\n".join([f"*{periods[i][0]}*: ${profits[i][0]}" for i in range(min(len(periods),12))])
        message = (
            f"📊 *Performance Summary (USD):*\n\n"
            f"📈 *Last 12 months:*\n{ytd_table}"
        )
        await update.message.reply_text(message,parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error retrieving performance: {str(e)}")
        await update.message.reply_text("❌ Error retrieving performance. Please try again.")

async def get_status(update:Update,context:ContextTypes.DEFAULT_TYPE):
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        sheet = spreadsheet.worksheet('Tracker')

        # get performance
        collateral = sheet.acell("G2").value
        open_trades = sheet.acell("G3").value

        message = (
            f"*Current Status:*\n\n"
            f"💵 *Collateral (USD):* ${collateral}\n"
            f"🤝 *Open trades:* {open_trades}"
        )
        await update.message.reply_text(message,parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Error retrieving status: {str(e)}")
        await update.message.reply_text("❌ Error retrieving status. Please try again.")


def main():
    """Start the bot"""
    application = Application.builder().token(TOKEN).build()

    # Conversation handler for adding trades
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_trade)],
        states={
            WAITING_FOR_TRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_trade_details)],
            CONFIRM_TRADE: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_trade)]
        },
        fallbacks=[]
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_error_handler(lambda u, c: logger.error(c.error))
    application.add_handler(CommandHandler('performance',get_performance))
    application.add_handler(CommandHandler('status',get_status))

    # Start the bot
    application.run_polling()

# Formatting function for Google Sheets
def apply_formatting(sheet, row_num, column_index):
    """Apply formatting to cells in Google Sheets"""
    cell_range = f"{gspread.utils.rowcol_to_a1(row_num, column_index)}"
    fmt = CellFormat(textFormat=TextFormat(bold=False, fontSize=11, fontFamily="Arial"))
    format_cell_range(sheet, cell_range, fmt)


if __name__ == '__main__':
    main()
