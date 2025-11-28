import os
import logging
import uuid

from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Config from environment
# -------------------------------------------------------------------
API_KEY = os.getenv("COINBASE_API_KEY")
API_SECRET = os.getenv("COINBASE_API_SECRET")

DEFAULT_PRODUCT_ID = os.getenv("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.getenv("DEFAULT_POSITION_USD", "1.0"))
PORT = int(os.getenv("PORT", "10000"))

if not API_KEY or not API_SECRET:
    log.warning("COINBASE_API_KEY or COINBASE_API_SECRET not set – bot will fail to trade.")

# -------------------------------------------------------------------
# Coinbase client helpers
# -------------------------------------------------------------------
def create_cb_client() -> RESTClient:
    """
    Create a Coinbase Advanced Trade REST client using classic
    api_key + api_secret (this is what your 10:14 buy used).
    """
    log.info("Creating Coinbase RESTClient with api_key only (Advanced Trade style).")
    client = RESTClient(API_KEY, API_SECRET)  # api_key, api_secret
    return client


def place_market_buy_usd(product_id: str, quote_size_usd: float):
    """
    Market BUY using a USD quote size.
    This matches exactly the order that succeeded at 10:14.
    """
    client = create_cb_client()
    client_order_id = str(uuid.uuid4())

    log.info(
        "Placing MARKET BUY: product_id=%s, quote_size=%s, client_order_id=%s",
        product_id,
        quote_size_usd,
        client_order_id,
    )

    order_cfg = {
        "market_market_ioc": {
            "quote_size": str(quote_size_usd),
        }
    }

    order = client.create_order(
        client_order_id,
        product_id=product_id,
        side="BUY",
        order_configuration=order_cfg,
    )

    log.info("BUY order response: %s", order)
    return order


def place_market_sell_all(product_id: str):
    """
    Sell ALL of the base currency in the product_id (e.g. ABT for ABT-USDC).
    This is the minimal change we needed to make selling work.
    """
    client = create_cb_client()
    base_currency = product_id.split("-")[0]

    log.info("Placing MARKET SELL ALL for product_id=%s (base=%s)", product_id, base_currency)

    try:
        accounts_response = client.get_accounts()
    except Exception as e:
        log.exception("Error calling get_accounts(): %s", e)
        raise

    # The SDK returns a ListAccountsResponse object with .accounts
    accounts = getattr(accounts_response, "accounts", []) or []
    log.info("DEBUG: Listing all accounts returned by Coinbase:")
    for acct in accounts:
        try:
            avail = float(acct.available_balance.value)
            log.info(
                "  Account currency=%s, available=%s",
                acct.currency,
                avail,
            )
        except Exception:
            log.info("  Account object: %s", acct)

    # Find the account for our base currency with positive balance
    target = None
    for acct in accounts:
        try:
            if acct.currency == base_currency:
                avail = float(acct.available_balance.value)
                if avail > 0:
                    target = acct
                    break
        except Exception:
            continue

    if not target:
        log.warning("No sellable account found for currency %s. Nothing to sell.", base_currency)
        return {"status": "no_position"}

    base_size = float(target.available_balance.value)

    log.info(
        "Found account for %s with available=%s. Placing market sell for full size.",
        base_currency,
        base_size,
    )

    client_order_id = str(uuid.uuid4())

    order_cfg = {
        "market_market_ioc": {
            "base_size": str(base_size),
        }
    }

    order = client.create_order(
        client_order_id,
        product_id=product_id,
        side="SELL",
        order_configuration=order_cfg,
    )

    log.info("SELL order response: %s", order)
    return order


# -------------------------------------------------------------------
# Flask app
# -------------------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends JSON like:
    { "signal": "BUY_SIGNAL", "product_id": "ABT-USDC" }
    """
    try:
        payload = request.get_json(force=True, silent=True) or {}
        log.info("Incoming webhook payload: %s", payload)

        signal = (payload.get("signal") or "").upper()
        product_id = payload.get("product_id") or DEFAULT_PRODUCT_ID

        if signal == "BUY_SIGNAL":
            result = place_market_buy_usd(product_id, DEFAULT_POSITION_USD)
            return jsonify({"status": "buy_sent", "result": str(result)}), 200

        elif signal == "EXIT_SIGNAL":
            result = place_market_sell_all(product_id)
            return jsonify({"status": "sell_sent", "result": str(result)}), 200

        else:
            log.warning("Unknown signal: %s", signal)
            return jsonify({"error": "unknown_signal", "signal": signal}), 400

    except Exception as e:
        log.exception("Error handling webhook: %s", e)
        return jsonify({"error": "server_error", "details": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
