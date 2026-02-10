# Developer Guide: Integrating Trade Calls with Alertatron

## Introduction

This guide provides two methods for automatically sending trade calls to an Alertatron webhook:

1.  **Discord Bot:** A Python bot that reads messages from a Discord channel, parses them, and sends them to Alertatron.
2.  **Website Scraper:** A Python script that scrapes trade calls from a website, formats them, and sends them to Alertatron.

## Alertatron Command Syntax

Alertatron expects a specific format for trade commands sent to its webhooks. The basic structure is as follows:

```
API_KEY_NAME(TRADING_SYMBOL) { command(parameters); } #bot
```

-   `API_KEY_NAME`: The name you assigned to your exchange API keys in Alertatron.
-   `TRADING_SYMBOL`: The trading pair (e.g., `BTCUSDT`).
-   `command`: The action to take, such as `market`, `limit`, `stop_loss`, or `take_profit`.
-   `parameters`: The parameters for the command, such as `side`, `amount`, and `price`.
-   `#bot`: This tag is **required** to tell Alertatron to process the command.

**Example:**

```
MyBybitKeys(BTCUSDT) { market(side=buy, amount=10%); stop_loss(price=60000); take_profit(price=70000); } #bot
```

This command would open a long position for 10% of the available balance on the Bybit account connected with `MyBybitKeys`, with a stop-loss at $60,000 and a take-profit at $70,000.

---

## Method 1: Discord Bot for Trade Calls

This method uses a Discord bot to listen for trade calls in a specific channel.

### Prerequisites

-   Python 3.6+
-   A Discord account with a server where you can add a bot.
-   The `discord.py` and `requests` Python libraries. Install them with `pip install discord.py requests`.

### Step-by-Step Guide

#### 1. Create a Discord Bot

1.  Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2.  Click "New Application" and give it a name.
3.  Go to the "Bot" tab and click "Add Bot".
4.  Under "Token", click "Copy" to get your bot's token. **Keep this secret!**
5.  Enable the "Message Content Intent" under "Privileged Gateway Intents".

#### 2. Invite the Bot to Your Server

1.  Go to the "OAuth2" -> "URL Generator" tab in the Developer Portal.
2.  Select the `bot` scope.
3.  Under "Bot Permissions", select "Read Messages/View Channels".
4.  Copy the generated URL and paste it into your browser to invite the bot to your server.

#### 3. Write the Bot Code

The following Python script will connect to your Discord server, listen for messages in a specific channel, parse the trade calls, and send them to your Alertatron webhook.

```python
import discord
import requests
import re
import os

# --- Configuration ---
DISCORD_TOKEN = "YOUR_DISCORD_BOT_TOKEN"
ALERTATRON_WEBHOOK_URL = "YOUR_ALERTATRON_WEBHOOK_URL"
TRADE_CHANNEL_ID = 123456789012345678  # Replace with your channel ID
API_KEY_NAME = "MyExchangeKeys"  # Your Alertatron API key name

class TradeBot(discord.Client):
    async def on_ready(self):
        print(f'Logged in as {self.user}')

    async def on_message(self, message):
        if message.author == self.user:
            return

        if message.channel.id == TRADE_CHANNEL_ID:
            print(f"Processing message: {message.content}")
            trade_command = self.parse_trade_call(message.content)

            if trade_command:
                self.send_to_alertatron(trade_command)

    def parse_trade_call(self, content):
        """
        This is a simple parser. You will need to adapt the regular expressions
        to match the specific format of the trade calls in your Discord channel.
        """
        content = content.lower()
        
        # Example trade call format: "BUY BTC/USDT at 65000, SL 64000, TP 67000"
        match = re.search(r'(buy|sell)\s+([a-z/]+)\s+at\s+([\d\.]+),\s+sl\s+([\d\.]+),\s+tp\s+([\d\.]+)', content)
        
        if not match:
            return None

        side = match.group(1)
        symbol = match.group(2).replace('/', '') # e.g., "BTCUSDT"
        entry_price = float(match.group(3))
        stop_loss = float(match.group(4))
        take_profit = float(match.group(5))

        # For this example, we assume a fixed 10% of the balance for each trade.
        amount = "10%" 

        command = f"{API_KEY_NAME}({symbol}) {{ market(side={side}, amount={amount}); stop_loss(price={stop_loss}); take_profit(price={take_profit}); }} #bot"
        return command

    def send_to_alertatron(self, command):
        try:
            response = requests.post(ALERTATRON_WEBHOOK_URL, json={"message": command})
            response.raise_for_status()
            print(f"Successfully sent command to Alertatron: {command}")
        except requests.exceptions.RequestException as e:
            print(f"Error sending to Alertatron: {e}")

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

client = TradeBot(intents=intents)
client.run(DISCORD_TOKEN)
```

**To use this script:**

1.  Replace the placeholder values in the "Configuration" section.
2.  Run the script from your terminal: `python your_bot_script_name.py`

---

## Method 2: Website Scraping for Trade Calls

This method involves scraping a website that lists trade calls.

### Prerequisites

-   Python 3.6+
-   The `requests` and `beautifulsoup4` libraries. Install with `pip install requests beautifulsoup4`.

### Step-by-Step Guide

#### 1. Identify the Target HTML

Inspect the webpage containing the trade calls and identify the HTML tags and classes that contain the trade information. For this example, let's assume the following HTML structure:

```html
<div class="trade-call">
  <h2>BUY BTC/USDT</h2>
  <p><strong>Entry:</strong> 65000</p>
  <p><strong>Stop Loss:</strong> 64000</p>
  <p><strong>Take Profit:</strong> 67000</p>
</div>
```

#### 2. Write the Scraper Script

The following script will fetch the content of the webpage, parse it to find trade calls, and send them to Alertatron.

```python
import requests
from bs4 import BeautifulSoup
import time
import hashlib

# --- Configuration ---
WEBSITE_URL = "http://example.com/trades"
ALERTATRON_WEBHOOK_URL = "YOUR_ALERTATRON_WEBHOOK_URL"
API_KEY_NAME = "MyExchangeKeys"
CHECK_INTERVAL_SECONDS = 300  # 5 minutes

PROCESSED_TRADES_FILE = "processed_trades.txt"

def get_processed_trades():
    try:
        with open(PROCESSED_TRADES_FILE, 'r') as f:
            return set(line.strip() for line in f)
    except FileNotFoundError:
        return set()

def add_processed_trade(trade_hash):
    with open(PROCESSED_TRADES_FILE, 'a') as f:
        f.write(trade_hash + '\n')

def scrape_trades():
    processed_trades = get_processed_trades()
    
    try:
        response = requests.get(WEBSITE_URL)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching website: {e}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    trade_calls = soup.find_all('div', class_='trade-call')

    for trade in trade_calls:
        try:
            header = trade.find('h2').text
            side, symbol = header.split()
            symbol = symbol.replace('/', '') # e.g., "BTCUSDT"

            entry = trade.find(string="Entry:").next_sibling.strip()
            stop_loss = trade.find(string="Stop Loss:").next_sibling.strip()
            take_profit = trade.find(string="Take Profit:").next_sibling.strip()
            
            # Create a unique hash for the trade to avoid duplicates
            trade_details = f"{side}{symbol}{entry}{stop_loss}{take_profit}"
            trade_hash = hashlib.sha256(trade_details.encode()).hexdigest()

            if trade_hash not in processed_trades:
                # Assuming a fixed 10% of balance per trade
                amount = "10%" 
                command = f"{API_KEY_NAME}({symbol}) {{ market(side={side.lower()}, amount={amount}); stop_loss(price={stop_loss}); take_profit(price={take_profit}); }} #bot"
                
                send_to_alertatron(command)
                add_processed_trade(trade_hash)

        except Exception as e:
            print(f"Error parsing trade: {e}")

def send_to_alertatron(command):
    try:
        response = requests.post(ALERTATRON_WEBHOOK_URL, json={"message": command})
        response.raise_for_status()
        print(f"Successfully sent command to Alertatron: {command}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending to Alertatron: {e}")

if __name__ == "__main__":
    while True:
        scrape_trades()
        print(f"Waiting for {CHECK_INTERVAL_SECONDS} seconds before next check...")
        time.sleep(CHECK_INTERVAL_SECONDS)
```

**To use this script:**

1.  Update the "Configuration" section with your details.
2.  You will likely need to modify the `scrape_trades` function to match the HTML structure of the target website.
3.  Run the script: `python your_scraper_script_name.py`.

This script will run indefinitely, checking the website for new trades every 5 minutes.
