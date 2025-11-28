import os
import json
import logging
from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# --------------------------------------------------------------------
# Logging setup
# --------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Env vars
# --------------------------------------------------------------------
CB_KEY_NAME       = os.environ.get("CB_KEY_NAME")
CB_KEY_SECRET     = os.environ.get("CB_KEY_SECRET")
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.environ.get("POSITION_USD", "1"))

if not CB_KEY_NAME or not CB_KEY_SECRET:
    logger.error("Missing Coinbase API credentials in environment variables!")
    raise SystemExit("Set CB_KEY_NAME and CB_KEY_SECRET in Render.")

client = RESTClient(api_key=CB_KEY_NAME, api_secret=CB_KEY_SECRET)

# --------------------------------------------------------------------
# Helper: place market buy
# --------------------------------------------------------------------
def place_market_buy_usd(product_id: str, quote_size_usd: float):
    logger.info("Placing MARKET BUY: product_id=%s, quote_size=%.2f",
                product_id, quote_size_usd)

    order = client.place_market_order(
        product_id=product_id,
        side="BUY",
        quote_size=str(quote_size_usd),
    )
    logger.info("BUY order response: %s", order)
    return order

# --------------------------------------------------------------------
# Helper: sell ALL of a base currency (e.g., sell all ABT to USDC)
# --------------------------------------------------------------------
def place_market_sell_all(product_id: str):
    """
    Sells ALL available base currency for the given product_id.
    Example: product_id='ABT-USDC' -> base_currency='ABT'
    """
    base_currency = product_id.split("-")[0]
    logger.info("Placing MARKET SELL ALL for base currency %s on %s",
                base_currency, product_id)

    # List all accounts in this portfolio
    accounts = client.get_accounts()
    held_account = None

    logger.info("=== DEBUG: Accounts with non-zero balance in this portfolio ===")
    for acct in accounts["accounts"]:
        cur = acct["currency"]
        avail = float(acct["available_balance"]["value"])
        if avail > 0:
            logger.info("• %s available=%s", cur, avail)
        if cur == base_currency and avail > 0:
            held_account = acct

    if not held_account:
        logger.warning("No account found with positive balance for %s. Nothing to sell.",
                       base_currency)
        return None

    size = held_account["available_balance"]["value"]
    logger.info("Found %s balance=%s, sending MARKET SELL on %s",
                base_currency, size, product_id)

    order = client.place_market_order(
        product_id=product_id,
        side="SELL",
        size=str(size),
    )
    logger.info("SELL order response: %s", order)
    return order

# --------------------------------------------------------------------
# Flask app
# --------------------------------------------------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def index():
    return "Coinbase bot is alive", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        logger.exception("Failed to parse JSON payload: %s", e)
        return jsonify({"status": "error", "message": "Invalid JSON"}), 400

    logger.info("Incoming webhook payload: %s", payload)

    signal = str(payload.get("signal", "")).upper()
    product_id = payload.get("product_id", DEFAULT_PRODUCT_ID) or DEFAULT_PRODUCT_ID

    if signal not in ("BUY_SIGNAL", "EXIT_SIGNAL"):
        logger.warning("Unknown or missing signal: %s", signal)
        return jsonify({"status": "ignored", "reason": "unknown signal"}), 200

    try:
        if signal == "BUY_SIGNAL":
            order = place_market_buy_usd(product_id, DEFAULT_POSITION_USD)
            return jsonify({"status": "ok", "side": "BUY", "order": order}), 200

        if signal == "EXIT_SIGNAL":
            order = place_market_sell_all(product_id)
            return jsonify({"status": "ok", "side": "SELL", "order": order}), 200

    except Exception as e:
        logger.exception("Error handling signal=%s for product_id=%s", signal, product_id)
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    logger.info("Using DEFAULT_PRODUCT_ID=%s; DEFAULT_POSITION_USD=%.2f",
                DEFAULT_PRODUCT_ID, DEFAULT_POSITION_USD)
    logger.info("Starting Flask dev server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
