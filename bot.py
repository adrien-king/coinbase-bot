from flask import Flask, request, jsonify
import time
import hmac
import hashlib
import base64
import requests
import os
import json

app = Flask(__name__)

# Environment variables (set these in Render.com dashboard)
CB_API_KEY = os.environ.get("CB_API_KEY")
CB_API_SECRET = os.environ.get("CB_API_SECRET")          # base64-encoded secret from Coinbase
CB_API_PASSPHRASE = os.environ.get("CB_API_PASSPHRASE")
BASE_URL = "https://api.coinbase.com"

# Default trade size in USD (can be overridden with TRADE_SIZE env var)
USD_SIZE = float(os.environ.get("TRADE_SIZE", 1000))


def sign_request(timestamp: str, method: str, path: str, body: str) -> str:
    """
    Create Coinbase Advanced API signature.
    """
    message = f"{timestamp}{method}{path}{body}"
    # Coinbase Advanced gives a base64-encoded secret
    hmac_key = base64.b64decode(CB_API_SECRET)
    signature = hmac.new(hmac_key, message.encode(), hashlib.sha256)
    return base64.b64encode(signature.digest()).decode()


def send_market_order(product_id: str, side: str, usd_size: float):
    """
    Send a simple market IOC order using quote_size (USD amount).
    """
    path = "/api/v3/brokerage/orders"
    url = BASE_URL + path

    body_dict = {
        "product_id": product_id,
        "side": side.lower(),  # "buy" or "sell"
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(usd_size)
            }
        }
    }

    body = json.dumps(body_dict)
    timestamp = str(int(time.time()))

    headers = {
        "CB-ACCESS-KEY": CB_API_KEY,
        "CB-ACCESS-SIGN": sign_request(timestamp, "POST", path, body),
        "CB-ACCESS-TIMESTAMP": timestamp,
        "CB-ACCESS-PASSPHRASE": CB_API_PASSPHRASE,
        "Content-Type": "application/json"
    }

    print("Sending order:", body_dict)
    response = requests.post(url, headers=headers, data=body)
    print("Order response:", response.status_code, response.text)
    return response.json()


def tv_symbol_to_cb(symbol: str) -> str:
    """
    Convert TradingView symbol to Coinbase Advanced product_id.

    Examples:
    - "COINBASE:SOLUSD"  -> "SOL-USD"
    - "BINANCE:SOLUSDT"  -> "SOL-USD"  (USDT is mapped to USD)
    """
    # Strip exchange prefix if present
    s = symbol.split(":")[-1]

    # Map USDT → USD for convenience (if you chart non-Coinbase exchanges)
    s = s.replace("USDT", "USD")

    base = s[:-3]
    quote = s[-3:]
    return f"{base}-{quote}"


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView should POST JSON here with at least:
    {
      "signal": "BUY_SIGNAL" or "EXIT_SIGNAL",
      "symbol": "COINBASE:SOLUSD",
      "price": "123.45",
      "time": "2025-11-26T15:30:00Z"
    }
    """
    data = request.get_json(force=True)
    print("Webhook received:", data)

    signal = data.get("signal")
    symbol = data.get("symbol")

    if not signal or not symbol:
        return jsonify({"error": "Missing signal or symbol"}), 400

    if signal not in ["BUY_SIGNAL", "EXIT_SIGNAL"]:
        # Ignore any other alerts
        return jsonify({"ignored": True}), 200

    product_id = tv_symbol_to_cb(symbol)

    if signal == "BUY_SIGNAL":
        send_market_order(product_id, "BUY", USD_SIZE)

    if signal == "EXIT_SIGNAL":
        send_market_order(product_id, "SELL", USD_SIZE)

    return jsonify({"status": "ok"}), 200


@app.route("/")
def home():
    return "Coinbase Advanced TradingView bot is running."


if __name__ == "__main__":
    # Render (and most PaaS) expose the port in the PORT env var
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
