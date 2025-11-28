import os
import uuid
import logging
from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# ------------------------
# Config
# ------------------------

# These should match what you have in Render
API_KEY = os.environ.get("COINBASE_API_KEY")
API_SECRET = os.environ.get("COINBASE_API_SECRET")

# What to trade by default if product_id not provided
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.environ.get("DEFAULT_POSITION_USD", "1.0"))

PORT = int(os.environ.get("PORT", "10000"))

# Basic logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# ------------------------
# Coinbase client
# ------------------------

def create_cb_client() -> RESTClient:
    if not API_KEY or not API_SECRET:
        raise RuntimeError("COINBASE_API_KEY and COINBASE_API_SECRET must be set")
    log.info("Creating Coinbase RESTClient")
    return RESTClient(api_key=API_KEY, api_secret=API_SECRET)

client = create_cb_client()

# ------------------------
# Helpers
# ------------------------

def place_market_buy(product_id: str, quote_size_usd: float):
    """
    Market buy using quote_size (USDC amount).
    """
    log.info("Placing MARKET BUY: product_id=%s, quote_size=%.2f", product_id, quote_size_usd)

    client_order_id = str(uuid.uuid4())
    order_cfg = {
        "market_market_ioc": {
            "quote_size": str(quote_size_usd)
        }
    }

    try:
        order = client.create_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side="BUY",
            order_configuration=order_cfg,
        )
        log.info("BUY order response: %s", order)
        return order
    except Exception as e:
        log.error("Error placing MARKET BUY: %s", e, exc_info=True)
        raise


def place_market_sell_all(product_id: str):
    """
    Sell *all* of the base currency for this product_id using a market order.
    Example: ABT-USDC -> base currency is ABT.
    """
    base_currency = product_id.split("-")[0]
    log.info("Placing MARKET SELL for ALL holdings in %s (product_id=%s)", base_currency, product_id)

    try:
        accounts = client.get_accounts()
    except Exception as e:
        log.error("Error fetching accounts: %s", e, exc_info=True)
        raise

    # Handle both dict-style and object-style responses
    raw_accounts = getattr(accounts, "data", None) or getattr(accounts, "accounts", None) or accounts

    found = False
    size_str = None

    try:
        for acct in raw_accounts:
            # Some versions have attributes, some are dicts
            currency = getattr(acct, "currency", None) or acct.get("currency")
            available = getattr(acct, "available_balance", None) or acct.get("available_balance")

            if not currency or not available:
                continue

            # available_balance may itself be object or dict with "value"
            if isinstance(available, dict):
                value = available.get("value")
            else:
                value = getattr(available, "value", None)

            if currency == base_currency:
                log.info("Matched account for %s: available=%s", currency, value)
                found = True
                if value is None:
                    log.warning("Account for %s has no 'value' field in available_balance", currency)
                    break
                if float(value) <= 0:
                    log.warning("Account for %s has no available balance to sell", currency)
                    break
                size_str = str(value)
                break
    except Exception as e:
        log.error("Error iterating accounts: %s", e, exc_info=True)
        raise

    if not found or not size_str:
        log.warning("No sellable account found for currency %s. Nothing to sell.", base_currency)
        return None

    client_order_id = str(uuid.uuid4())
    order_cfg = {
        "market_market_ioc": {
            "base_size": size_str
        }
    }

    try:
        order = client.create_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side="SELL",
            order_configuration=order_cfg,
        )
        log.info("SELL order response: %s", order)
        return order
    except Exception as e:
        log.error("Error placing MARKET SELL: %s", e, exc_info=True)
        raise

# ------------------------
# Flask app
# ------------------------

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "coinbase bot running"}), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as e:
        log.error("Failed to parse JSON payload: %s", e, exc_info=True)
        return jsonify({"error": "invalid json"}), 400

    log.info("Incoming webhook payload: %s", payload)

    signal = (payload or {}).get("signal")
    product_id = (payload or {}).get("product_id") or DEFAULT_PRODUCT_ID
    usd_size = float((payload or {}).get("usd_size") or DEFAULT_POSITION_USD)

    if not signal:
        log.warning("No 'signal' field in payload; ignoring")
        return jsonify({"error": "missing signal"}), 400

    try:
        if signal.upper() == "BUY_SIGNAL":
            order = place_market_buy(product_id, usd_size)
            return jsonify({"status": "ok", "action": "buy", "product_id": product_id, "order": str(order)}), 200

        elif signal.upper() == "EXIT_SIGNAL":
            order = place_market_sell_all(product_id)
            return jsonify({"status": "ok", "action": "sell_all", "product_id": product_id, "order": str(order)}), 200

        else:
            log.warning("Unknown signal: %s", signal)
            return jsonify({"error": "unknown signal"}), 400

    except Exception as e:
        log.error("Error handling webhook: %s", e, exc_info=True)
        return jsonify({"error": "internal error", "details": str(e)}), 500


if __name__ == "__main__":
    # Local run (Render uses gunicorn / start.sh instead)
    app.run(host="0.0.0.0", port=PORT)
