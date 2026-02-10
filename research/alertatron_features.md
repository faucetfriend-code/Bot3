# Alertatron Features for Algorithmic Trading

This document outlines the most useful features of Alertatron for an algorithmic trader using TradingView signals to trade on Blofin.

## Key Features

*   **Automated Trading:** Alertatron allows you to automate your trading strategies 24/7. It can receive signals from various sources and execute trades on your behalf on multiple exchanges, including Blofin.
*   **TradingView Integration:** Seamlessly connect your TradingView account to Alertatron. You can create alerts in TradingView that trigger trades in your Alertatron account. This is ideal for traders who rely on TradingView's charting and analysis tools.
*   **Dynamic Alerts:** Your TradingView alerts can include dynamic information, such as the current price of an asset or the value of an indicator. This allows you to create more sophisticated trading strategies that react to changing market conditions.
*   **Webhook Support:** In addition to TradingView, Alertatron can receive signals from any platform that supports webhooks. This provides a high degree of flexibility, allowing you to integrate custom scripts or other signal sources.
*   **Blofin Integration:** Alertatron offers direct integration with Blofin through its "Signals Lite" feature. You can connect your Blofin account using API keys and execute trades directly on the exchange.
*   **Complex Order Types:** Alertatron supports a variety of order types, including `market`, `limit`, `stop_loss`, `take_profit`, and more. You can also create complex order sequences to manage your risk and automate your entire trading strategy.
*   **High Reliability:** Alertatron is a cloud-based platform, which means you don't need to run any software on your own computer. This ensures that your trading strategies are always running, even when your computer is turned off.

## Integrating Discord Trade Calls

Alertatron's Discord integration is designed to **send notifications from Alertatron to a Discord channel**, not the other way around. You cannot directly use messages from a Discord group as a source for your trading signals in Alertatron.

To integrate trade calls from your Discord group, you would need to build a custom solution that performs the following steps:

1.  **Monitor the Discord Channel:** You would need a program (likely a Discord bot) that can read messages from the specific channel in your Discord group where the trade calls are posted.
2.  **Parse the Messages:** The program would need to be able to understand the format of the trade call messages and extract the key information, such as:
    *   The asset to trade (e.g., BTC/USD)
    *   The entry price
    *   The stop-loss price
    *   The take-profit price(s)
3.  **Format the Data:** Once the information is extracted, it needs to be formatted into a command that Alertatron can understand. You would need to consult the Alertatron documentation for the specific command syntax.
4.  **Send to Alertatron:** Finally, the formatted command would be sent to your unique Alertatron webhook URL.

### Alternative: Using a Website

If your Discord group has a website that lists the active trades, you could potentially build a web scraping tool to extract the trade information from the website. The process would be similar to the Discord bot approach:

1.  **Scrape the Website:** The tool would regularly check the website for new trades.
2.  **Parse the Data:** It would extract the trade parameters from the website's HTML.
3.  **Format and Send:** The data would then be formatted and sent to your Alertatron webhook URL.

Both of these solutions would require programming knowledge to implement. You would need to use a programming language like Python or JavaScript and the appropriate libraries for interacting with Discord or scraping websites.
