import os
import logging
from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# ------------------------------------------------------------------------------
# Basic setup
# ------------------------------------------------------------------------------

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables (you’ve already set these in Render)
CB_KEY_NAME = os.environ.get("CB_KEY_NAME")
CB_KEY_SECRET = os.environ.get("CB_KEY_SECRET")
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "SOL-USDC")
POSITION_USD = float(os.environ.get("POSITION_USD", "1"))  # dollar size per trade

if not CB_KEY_NAME or not CB_KEY_SECRET:
    logger.warning("CB_KEY_NAME or CB_KEY_SECRET not set – Coinbase client will fail!")

# Coinbase Advanced REST client
client = RESTClient(api_key=CB_KEY_NAME, api_secret=CB_KEY_SECRET)


# ------------------------------------------------------------------------------
# Health check
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Simple health endpoint."""
    return "Coinbase bot is running", 200


# ------------------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------------------

def place_market_buy(product_id: str, quote_size_usd: float):
    """
    Place a MARKET BUY using quote_size (USDC / USD part of the pair).
    product_id example: 'SOL-USDC', 'BTC-USD', 'BONK-USDC'
    """
    logger.info(f"Placing MARKET BUY: product_id={product_id}, quote_size={quote_size_usd}")

    order = client.market_order_buy(
        client_order_id="",               # let Coinbase auto-generate
        product_id=product_id,
        quote_size=str(quote_size_usd),   # must be string
    )

    logger.info(f"Buy order response: {order.to_dict()}")
    return order


def place_market_sell_all(product_id: str):
    """
    Sell ALL available balance of the base asset in product_id.
    Example: product_id 'SOL-USDC' -> base asset 'SOL'
    """
    base_currency = product_id.split("-")[0]
    logger.info(f"Placing MARKET SELL for all holdings in {base_currency}, product_id={product_id}")

    accounts = client.get_accounts()
    base_account = None

    for acct in accounts.accounts:
        if acct.currency == base_currency:
            base_account = acct
            break

    if not base_account:
        logger.warning(f"No account found for currency {base_currency}. Nothing to sell.")
        return None

    available = float(base_account.available_balance["value"])
    logger.info(f"Available {base_currency} balance: {available}")

    if available <= 0:
        logger.warning(f"Balance for {base_currency} is zero. Nothing to sell.")
        return None

    order = client.market_order_sell(
        client_order_id="",
        product_id=product_id,
        base_size=str(available),  # sell entire available base balance
    )

    logger.info(f"Sell order response: {order.to_dict()}")
    return order


# ------------------------------------------------------------------------------
# Webhook endpoint (TradingView -> here)
# ------------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends JSON here.
    Expected JSON:
      { "signal": "BUY_SIGNAL",  "product_id": "SOL-USDC" }
      { "signal": "EXIT_SIGNAL", "product_id": "SOL-USDC" }
    If product_id is missing, DEFAULT_PRODUCT_ID is used.
    """
    try:
        data = request.get_json(force=True)
    except Exception as e:
        logger.exception("Invalid JSON in webhook")
        return "Invalid JSON", 400

    logger.info(f"Incoming webhook payload: {data}")

    if not isinstance(data, dict):
        return "Payload must be a JSON object", 400

    signal = data.get("signal")
    product_id = data.get("product_id") or DEFAULT_PRODUCT_ID

    if signal not in ("BUY_SIGNAL", "EXIT_SIGNAL"):
        logger.warning(f"Unknown signal: {signal}")
        return "Unknown signal", 400

    try:
        if signal == "BUY_SIGNAL":
            order = place_market_buy(product_id, POSITION_USD)
            return jsonify({
                "status": "ok",
                "action": "buy",
                "product_id": product_id,
                "order": order.to_dict() if order else None,
            }), 200

        elif signal == "EXIT_SIGNAL":
            order = place_market_sell_all(product_id)
            return jsonify({
                "status": "ok",
                "action": "sell",
                "product_id": product_id,
                "order": order.to_dict() if order else None,
            }), 200

    except Exception as e:
        logger.exception("Error while placing order")
        return "Error while placing order", 500


# ------------------------------------------------------------------------------
# Local run (Render uses gunicorn via start.sh)
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
