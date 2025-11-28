import os
import json
import uuid
import logging

from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# --------------------------------------------------
# CONFIG FROM ENV
# --------------------------------------------------
CB_KEY_NAME = os.environ.get("CB_KEY_NAME")
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.environ.get("DEFAULT_POSITION_USD", "1.0"))
PORT = int(os.environ.get("PORT", "10000"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)

logger.info("BOT starting up with config:")
logger.info("  CB_KEY_NAME: %s", CB_KEY_NAME)
logger.info("  DEFAULT_PRODUCT_ID: %s", DEFAULT_PRODUCT_ID)
logger.info("  DEFAULT_POSITION_USD: %s", DEFAULT_POSITION_USD)
logger.info("  PORT: %s", PORT)

# Coinbase client
client = RESTClient(api_key=CB_KEY_NAME)

app = Flask(__name__)


# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def _new_client_order_id() -> str:
    """Generate a unique client_order_id for every order."""
    return str(uuid.uuid4())


def place_market_buy(product_id: str, usd_notional: float):
    """
    Place a MARKET BUY using quote_size in USD (or quote currency).
    """
    logger.info(
        "Placing MARKET BUY: product_id=%s, quote_size=%.2f",
        product_id,
        usd_notional,
    )

    order_cfg = {
        "market_market_ioc": {
            # amount of quote currency (USDC, USD, etc)
            "quote_size": str(usd_notional)
        }
    }

    order = client.create_order(
        client_order_id=_new_client_order_id(),
        product_id=product_id,
        side="BUY",
        order_configuration=order_cfg,
    )

    logger.info("Buy order response: %s", order)
    return order


def place_market_sell_all(product_id: str, base_currency: str):
    """
    Sell ALL available balance in `base_currency` for the given product_id.
    Example: product_id='ABT-USDC', base_currency='ABT'
    """
    logger.info(
        "Placing MARKET SELL for all holdings in %s, product_id=%s",
        base_currency,
        product_id,
    )

    accounts = client.get_accounts()
    target_acct = None

    # accounts.data is a list of account objects
    for acct in getattr(accounts, "data", []):
        try:
            currency = getattr(acct, "available_balance", None).currency
            value = getattr(acct, "available_balance", None).value
        except AttributeError:
            continue

        if currency == base_currency:
            target_acct = acct
            logger.info(
                "Found account for %s with available balance=%s",
                currency,
                value,
            )
            break

    if not target_acct:
        logger.warning("No account found for currency %s. Nothing to sell.", base_currency)
        return {"status": "nothing_to_sell", "reason": "no_account"}

    size = float(target_acct.available_balance.value)
    if size <= 0:
        logger.warning(
            "Account for %s has zero balance (%.8f). Nothing to sell.",
            base_currency,
            size,
        )
        return {"status": "nothing_to_sell", "reason": "zero_balance"}

    order_cfg = {
        "market_market_ioc": {
            # base_size is how much of the base coin to sell
            "base_size": str(size)
        }
    }

    order = client.create_order(
        client_order_id=_new_client_order_id(),
        product_id=product_id,
        side="SELL",
        order_configuration=order_cfg,
    )

    logger.info("Sell order response: %s", order)
    return order


# --------------------------------------------------
# ROUTES
# --------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    return "Coinbase bot is live", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends JSON like:
      {"signal":"BUY_SIGNAL","product_id":"ABT-USDC"}
      {"signal":"EXIT_SIGNAL","product_id":"ABT-USDC"}

    Optional:
      {"signal":"BUY_SIGNAL","product_id":"ABT-USDC","usd_notional":5}
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception as e:
        logger.exception("Failed to parse JSON body")
        return jsonify({"error": "invalid_json", "details": str(e)}), 400

    logger.info("Incoming webhook payload: %s", data)

    if not isinstance(data, dict):
        return jsonify({"error": "payload_must_be_object"}), 400

    signal = data.get("signal")
    product_id = data.get("product_id", DEFAULT_PRODUCT_ID)
    usd_notional = float(data.get("usd_notional", DEFAULT_POSITION_USD))

    if not signal:
        logger.warning("Missing 'signal' in payload")
        return jsonify({"error": "missing_signal"}), 400

    # BUY
    if signal == "BUY_SIGNAL":
        try:
            res = place_market_buy(product_id, usd_notional)
            return jsonify({"status": "buy_ok", "order": json.loads(str(res))}), 200
        except Exception as e:
            logger.exception("Error placing MARKET BUY")
            return jsonify({"error": "buy_failed", "details": str(e)}), 500

    # EXIT / SELL
    if signal == "EXIT_SIGNAL":
        base_currency = product_id.split("-")[0]
        try:
            res = place_market_sell_all(product_id, base_currency)
            return jsonify({"status": "sell_ok", "result": res}), 200
        except Exception as e:
            logger.exception("Error placing MARKET SELL")
            return jsonify({"error": "sell_failed", "details": str(e)}), 500

    logger.warning("Unknown signal: %s", signal)
    return jsonify({"error": "unknown_signal", "signal": signal}), 400


# --------------------------------------------------
# ENTRY POINT (for local runs)
# --------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
