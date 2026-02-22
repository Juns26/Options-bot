# Options Trading Tracker Bot

A comprehensive Telegram Bot designed to track options and stock trading activities on Tiger Brokers. The bot acts as a convenient interface to fetch your trades, calculate PnL, maintain a Google Sheets ledger, and visualize your portfolio's performance directly in Telegram.

## 🌟 Features

- **Automated Trade Sync**: Fetches the latest options and stock orders directly from your Tiger Brokers account via the TigerOpen API.
- **Performance Visualization**: Generates insightful pie charts and bar charts for your performance, showing your PnL breakdown across different strategies.
- **Flexible Timeframes**: View performance metrics Month-to-Date (MTD), Year-to-Date (YTD), Year-on-Year (YoY), or across custom date ranges.
- **Position Tracking**: Get real-time summaries of your open stock and option positions, including total value, floating PnL, and collateral used.
- **Goal Setting**: Set monthly profit targets and track your achievement percentage.
- **Admin Security**: Restricts critical commands (like fetching new trades and setting targets) to the authorized Telegram User ID.

## 🛠️ Prerequisites

1. **Python 3.8+**
2. **Tiger Brokers Developer config**: You need developer access enabled on your Tiger Brokers account to get the license, account ID, and private key.
3. **Google Cloud Service Account**: 
   - Enable the **Google Sheets API** and **Google Drive API**.
   - Create a Service Account and download the JSON credentials.
   - Create a Google Sheet named `Options Tracker` (with sheets `Tiger Trade API Summary` and `Tiger Trade API Data`) and share it with the service account email.
4. **Telegram Bot Token**: Created via BotFather on Telegram.

## ⚙️ Environment Structure

Create a `.env` file in the root directory and populate it with your credentials:

```env
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
TELEGRAM_USER_ID=123456789  # Your personal Telegram User ID for admin access

# Tiger Brokers API Configuration
client_config.tiger_id="your_tiger_id"
client_config.account="your_account_number"
client_config.license="TBSG" # Or your corresponding license
client_config.private_key="your_tiger_private_key"

# Google Sheets Service Account Credentials
GOOGLE_PROJECT_ID="your_project_id"
GOOGLE_PRIVATE_KEY_ID="your_private_key_id"
GOOGLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
GOOGLE_CLIENT_EMAIL="your_service_account_email"
GOOGLE_CLIENT_ID="your_client_id"
GOOGLE_AUTH_URI="https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI="https://oauth2.googleapis.com/token"
GOOGLE_AUTH_PROVIDER_X509_CERT_URL="https://www.googleapis.com/oauth2/v1/certs"
GOOGLE_CLIENT_X509_CERT_URL="your_client_x509_url"
```

## 🚀 Installation & Setup

1. **Clone the repository** (if applicable) or download the files.

2. **Set up a virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the bot**:
   ```bash
   python main.py
   ```
   *Note: The script also spins up a lightweight `aiohttp` web server on port 10000 (or the port defined in `$PORT`) to serve health checks, which is useful for platforms like Render.*

## 📱 Bot Commands

- `/start` - Displays the welcome message and available commands.
- `/refresh` - Refreshes trade data from Tiger Broker and updates the Google Sheet (Admin only).
- `/performance` - Opens an interactive menu to view performance metrics (MTD, YTD, YoY, Custom).
- `/get_position` - Fetches and displays your current stock and option positions, total value, and collateral.
- `/set_target <amount>` - Sets your monthly profit target (Admin only).
- `/help` - Shows the help message outlining all features.

## 🏗️ Architecture overview

- **`main.py`**: The entry point for the Telegram bot, handling command routing, fetching summary data from Google Sheets, calculating deltas, creating matplotlib charts, and generating responses.
- **`fetch_trades.py`**: Contains the core logic to query the TigerOpen API for recently filled trades, parse single/multi-leg options, compute trade cashflows, calculate required collaterals, classify the trading strategy (e.g., CSP, BPS, PMCC), and append new rows to the Google Sheet.
- **Google Sheets (`Options Tracker`)**: Acts as the database. `main.py` fetches the aggregated data (from `Tiger Trade API Summary`) to render charts, avoiding heavy database setups.

## ⚠️ Notes
- Ensure your `matplotlib` is configured appropriately if deploying on a headless server. The application defaults to `Agg` backend (`matplotlib.use('Agg')`) which prevents GUI-related crashes.
- To avoid Tiger API rate limits, `fetch_trades.py` pulls trade orders in chunks.
