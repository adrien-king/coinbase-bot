import os
import json
import uuid
import logging

from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# ---------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("coinbase-bot")

# ---------------------------------------------------------------------
# Environment / config
# ---------------------------------------------------------------------

# Support either the new names or your older ones, so it won't break
CB_API_KEY = os.environ.get("CB_API_KEY") or os.environ.get("CB_KEY_NAME")
CB_API_SECRET = os.environ.get("CB_API_SECRET") or os.environ.get("CB_KEY_SECRET")

DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.environ.get("DEFAULT_POSITION_USD", "1.0"))
PORT = int(os.environ.get("PORT", "10000"))

if not CB_API_KEY or not CB_API_SECRET:
    raise Exception("Missing CB_API_KEY/CB_KEY_NAME or CB_API_SECRET/CB_KEY_SECRET in environment.")

safe_key = CB_API_KEY[:6] + "..." + CB_API_KEY[-4:]
logger.info(
    "BOT starting up with config: KEY=%s, DEFAULT_PRODUCT_ID=%s, "
    "DEFAULT_POSITION_USD=%.2f, PORT=%s",
    safe_key, DEFAULT_PRODUCT_ID, DEFAULT_POSITION_USD, PORT
)

# ---------------------------------------------------------------------
# Coinbase client
# ---------------------------------------------------------------------
client = RESTClient(
    api_key=CB_API_KEY,
    api_secret=CB_API_SECRET
)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def get_account_for_currency(currency: str):
    """
    Find the account object for a given currency (e.g., 'ABT', 'USDC')
    in the portfolio that this API key is attached to.
    """
    logger.info("Listing all accounts from Coinbase...")
    accounts = client.get_accounts()
    logger.info("Raw accounts response: %s", accounts)

    # accounts.data is a list of account objects
    for acc in accounts.data:
        try:
            cur = acc.currency
            avail_val = acc.available_balance.value
            logger.info(
                "Account uuid=%s, currency=%s, available=%s",
                acc.uuid,
                cur,
                avail_val,
            )
            if cur == currency:
                return acc
        except Exception as e:
            logger.exception("Error inspecting account object: %s", e)

    logger.warning("No account found for currency %s", currency)
    return None


def place_market_buy_quote(product_id: str, quote_size_usd: float):
    """
    Place a MARKET BUY using quote_size (spend this much USDC).
    """
    logger.info(
        "Placing MARKET BUY for product_id=%s, quote_size=%.2f",
        product_id, quote_size_usd
    )

    order_cfg = {
        "market_market_ioc": {
            "quote_size": f"{quote_size_usd:.2f}"
        }
    }

    order = client.create_order(
        client_order_id=str(uuid.uuid4()),
        product_id=product_id,
        side="BUY",
        order_configuration=order_cfg,
    )

    logger.info("BUY order response: %s", order)
    return order


def place_market_sell_all(product_id: str, base_currency: str):
    """
    Sell ALL available balance of base_currency (e.g., 'ABT') in a MARKET SELL.
    """
    logger.info(
        "Placing MARKET SELL ALL for product_id=%s, base_currency=%s",
        product_id, base_currency
    )

    acc = get_account_for_currency(base_currency)
    if acc is None:
        logger.warning("No account for %s. Nothing to sell.", base_currency)
        return {"status": "no_account", "currency": base_currency}

    available_str = acc.available_balance.value
    try:
        available = float(available_str)
    except Exception:
        logger.exception("Could not parse available balance '%s'", available_str)
        return {"status": "parse_error", "raw_available": available_str}

    if available <= 0:
        logger.warning("Zero balance for %s. Nothing to sell.", base_currency)
        return {"status": "zero_balance", "currency": base_currency}

    logger.info("Selling base_size=%s of %s", available_str, base_currency)

    order_cfg = {
        "market_market_ioc": {
            "base_size": f"{available:.8f}"  # more precision for small coins
        }
    }

    order = client.create_order(
        client_order_id=str(uuid.uuid4()),
        product_id=product_id,
        side="SELL",
        order_configuration=order_cfg,
    )

    logger.info("SELL order response: %s", order)
    return order


# ---------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return "Coinbase auto-trader bot is running.\n", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView should POST JSON like:
      { "signal": "BUY_SIGNAL",  "product_id": "ABT-USDC" }
      { "signal": "EXIT_SIGNAL", "product_id": "ABT-USDC" }
    """
    try:
        payload = request.get_json(force=True)
    except Exception as e:
        logger.exception("Error parsing JSON body: %s", e)
        return jsonify({"status": "error", "message": "invalid json"}), 400

    logger.info("Incoming webhook payload: %s", payload)

    if not isinstance(payload, dict):
        return jsonify({"status": "error", "message": "payload must be JSON object"}), 400

    signal = payload.get("signal")
    product_id = payload.get("product_id") or DEFAULT_PRODUCT_ID

    if not signal:
        logger.warning("Webhook payload missing 'signal' field")
        return jsonify({"status": "error", "message": "missing signal"}), 400

    # BUY
    if signal == "BUY_SIGNAL":
        try:
            place_market_buy_quote(product_id, DEFAULT_POSITION_USD)
            return jsonify({"status": "ok", "action": "buy", "product_id": product_id}), 200
        except Exception as e:
            logger.exception("Error handling BUY_SIGNAL: %s", e)
            return jsonify({"status": "error", "action": "buy", "message": str(e)}), 500

    # EXIT / SELL ALL
    if signal == "EXIT_SIGNAL":
        base_currency = product_id.split("-")[0]
        try:
            place_market_sell_all(product_id, base_currency)
            return jsonify({"status": "ok", "action": "sell", "product_id": product_id}), 200
        except Exception as e:
            logger.exception("Error handling EXIT_SIGNAL: %s", e)
            return jsonify({"status": "error", "action": "sell", "message": str(e)}), 500

    logger.warning("Unknown signal: %s", signal)
    return jsonify({"status": "ignored", "message": f"unknown signal {signal}"}), 200


if __name__ == "__main__":
    # Local run (Render will use start.sh / gunicorn)
    app.run(host="0.0.0.0", port=PORT)
