import os
import json
import logging
from typing import List, Tuple, Any, Optional

from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# ---------------------------------------------------------
# Logging setup
# ---------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("__main__")

# ---------------------------------------------------------
# Environment
# ---------------------------------------------------------
CB_KEY_NAME = os.environ.get("CB_KEY_NAME", "").strip()
CB_KEY_SECRET = os.environ.get("CB_KEY_SECRET", "").strip()

DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "SOL-USDC").strip()
DEFAULT_POSITION_USD = float(os.environ.get("DEFAULT_POSITION_USD", "1.0"))

PORT = int(os.environ.get("PORT", "10000"))

if not CB_KEY_NAME or not CB_KEY_SECRET:
    logger.error("Missing CB_KEY_NAME or CB_KEY_SECRET in environment.")
else:
    logger.info("BOT starting up with config:")
    logger.info("  CB_KEY_NAME: %s", CB_KEY_NAME)
    logger.info("  DEFAULT_PRODUCT_ID: %s", DEFAULT_PRODUCT_ID)
    logger.info("  DEFAULT_POSITION_USD: %s", DEFAULT_POSITION_USD)
    logger.info("  PORT: %s", PORT)

# ---------------------------------------------------------
# Coinbase client
# ---------------------------------------------------------
client = RESTClient(
    api_key=CB_KEY_NAME,
    api_secret=CB_KEY_SECRET,
)

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
def list_accounts_with_balances() -> List[Tuple[str, float, Any]]:
    """
    Return list of (currency, available, raw_account_obj)
    using the official ListAccountsResponse model.
    """
    results: List[Tuple[str, float, Any]] = []
    try:
        resp = client.get_accounts()
        logger.info("Raw ListAccountsResponse from Coinbase: %s", resp)

        # The SDK returns a Pydantic model; the accounts live on resp.accounts
        accounts = getattr(resp, "accounts", None)

        if not accounts:
            logger.warning("get_accounts() returned no accounts.")
            return results

        for acc in accounts:
            try:
                currency = getattr(acc, "currency", None)

                # available_balance is usually a Money dict/model with "value"
                available_obj = getattr(acc, "available_balance", None)
                available_val: Optional[float] = None

                if available_obj is not None:
                    # New SDKs use .value; older might use ['value']
                    value = getattr(available_obj, "value", None)
                    if value is None and isinstance(available_obj, dict):
                        value = available_obj.get("value")
                    if value is not None:
                        available_val = float(value)
                else:
                    # very old models might have acc.available
                    maybe = getattr(acc, "available", None)
                    if maybe is not None:
                        available_val = float(maybe)

                logger.info(
                    "Account currency=%s, available=%s, raw=%s",
                    currency,
                    available_val,
                    acc,
                )
                if currency and available_val is not None:
                    results.append((currency, available_val, acc))
            except Exception as inner:
                logger.warning("Error parsing single account: %s", inner)

    except Exception as e:
        logger.exception("Error listing accounts: %s", e)

    return results


def find_base_currency_account(product_id: str) -> Tuple[Optional[str], float]:
    """
    For a product like 'ABT-USDC', find the base currency account (ABT)
    and return (currency, available).
    """
    base = product_id.split("-")[0]
    accounts = list_accounts_with_balances()
    for currency, available, _raw in accounts:
        if currency == base:
            logger.info(
                "Matched base currency account: %s, available=%s", currency, available
            )
            return currency, available
    logger.warning("No account found for base currency %s.", base)
    return None, 0.0


def place_market_buy(product_id: str, quote_size_usd: float):
    """
    Place a MARKET BUY with a fixed quote size (USDC amount).
    """
    logger.info(
        "Placing MARKET BUY; product_id=%s, quote_size=%.4f",
        product_id,
        quote_size_usd,
    )

    order_cfg = {
        "market_market_ioc": {
            "quote_size": str(quote_size_usd),
        }
    }

    try:
        order = client.create_order(
            product_id=product_id,
            side="BUY",
            order_configuration=order_cfg,
        )
        logger.info("BUY order response: %s", order)
        return order
    except Exception as e:
        logger.exception("Error placing MARKET BUY: %s", e)
        raise


def place_market_sell_all(product_id: str):
    """
    Sell ALL available base currency for the given product.
    For ABT-USDC, find ABT account and use its available amount as base_size.
    """
    base_currency, available = find_base_currency_account(product_id)

    if base_currency is None or available <= 0:
        logger.warning(
            "No funds to sell for product %s (base %s). Nothing to do.",
            product_id,
            base_currency,
        )
        return None

    # Safety: tiny dust can cause errors; ignore < 0.00000001
    if available < 1e-8:
        logger.warning(
            "Available balance for %s is essentially zero (%.12f). Skipping sell.",
            base_currency,
            available,
        )
        return None

    base_size = round(available, 8)
    logger.info(
        "Placing MARKET SELL ALL; product_id=%s, base_currency=%s, base_size=%s",
        product_id,
        base_currency,
        base_size,
    )

    order_cfg = {
        "market_market_ioc": {
            "base_size": str(base_size),
        }
    }

    try:
        order = client.create_order(
            product_id=product_id,
            side="SELL",
            order_configuration=order_cfg,
        )
        logger.info("SELL order response: %s", order)
        return order
    except Exception as e:
        logger.exception("Error placing MARKET SELL: %s", e)
        raise


# ---------------------------------------------------------
# Flask app
# ---------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception as e:
        logger.exception("Failed to parse JSON from webhook: %s", e)
        return jsonify({"status": "error", "reason": "invalid_json"}), 400

    logger.info("Incoming webhook payload: %s", payload)

    if not isinstance(payload, dict):
        return jsonify({"status": "error", "reason": "payload_not_dict"}), 400

    signal = payload.get("signal")
    product_id = payload.get("product_id", DEFAULT_PRODUCT_ID) or DEFAULT_PRODUCT_ID

    if not signal:
        return jsonify({"status": "error", "reason": "missing_signal"}), 400

    try:
        if signal == "BUY_SIGNAL":
            order = place_market_buy(product_id, DEFAULT_POSITION_USD)
            return jsonify({"status": "ok", "action": "buy", "product_id": product_id}), 200

        elif signal == "EXIT_SIGNAL":
            order = place_market_sell_all(product_id)
            return jsonify({"status": "ok", "action": "sell_all", "product_id": product_id}), 200

        else:
            logger.warning("Unknown signal: %s", signal)
            return jsonify({"status": "error", "reason": "unknown_signal"}), 400

    except Exception as e:
        logger.exception("Error handling webhook: %s", e)
        return jsonify({"status": "error", "reason": "internal_exception"}), 500


# ---------------------------------------------------------
# Local run (Render still uses your start.sh)
# ---------------------------------------------------------
if __name__ == "__main__":
    # Useful if you ever run it locally: python bot.py
    logger.info("Starting Flask app on port %s", PORT)
    app.run(host="0.0.0.0", port=PORT)
