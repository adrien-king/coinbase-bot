import os
import logging
from flask import Flask, request, jsonify
from coinbase.rest import RESTClient

# -------------------------------------------------------------------
# Logging setup
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Environment & Coinbase client
# -------------------------------------------------------------------
def create_cb_client() -> RESTClient:
    """
    Create a Coinbase Advanced REST client from environment variables.
    Tries a few possible env var names for the private key so it works
    with the different names you've used.
    """
    cb_key_name = (
        os.getenv("CB_KEY_NAME")
        or os.getenv("CB_API_KEY_NAME")
        or os.getenv("CB_API_KEY")
    )

    private_key = (
        os.getenv("CB_PRIVATE_KEY")
        or os.getenv("CB_KEY_SECRET")
        or os.getenv("Private_key")
    )

    if not cb_key_name:
        raise RuntimeError("Missing CB_KEY_NAME / CB_API_KEY_NAME / CB_API_KEY in environment")
    if not private_key:
        raise RuntimeError("Missing CB_PRIVATE_KEY / CB_KEY_SECRET / Private_key in environment")

    logger.info("Creating Coinbase RESTClient with CB_KEY_NAME=%s", cb_key_name)
    # DO NOT log the private key!

    # Coinbase Advanced Trade client (Developer Platform)
    client = RESTClient(api_key=cb_key_name, private_key=private_key)
    return client


# Global client
client = create_cb_client()

# Trading defaults
DEFAULT_PRODUCT_ID = os.getenv("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.getenv("DEFAULT_POSITION_USD", os.getenv("POSITION_USD", "1.0")))
PORT = int(os.getenv("PORT", "10000"))

logger.info("BOT config -> DEFAULT_PRODUCT_ID=%s, DEFAULT_POSITION_USD=%s, PORT=%s",
            DEFAULT_PRODUCT_ID, DEFAULT_POSITION_USD, PORT)

# -------------------------------------------------------------------
# Flask app
# -------------------------------------------------------------------
app = Flask(__name__)

# -------------------------------------------------------------------
# Helper functions for trading
# -------------------------------------------------------------------
def place_market_buy(product_id: str, quote_usd: float):
    """
    Place a MARKET BUY using quote_size in USD/USDC.
    """
    logger.info("Placing MARKET BUY: product_id=%s, quote_size=%s", product_id, quote_usd)

    body = {
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(quote_usd)
            }
        },
    }

    logger.info("Buy order request body: %s", body)
    order = client.create_order(**body)
    logger.info("Buy order response: %s", order)
    return order


def place_market_sell_all(product_id: str):
    """
    Sell ALL available base currency for the given product_id.
    E.g. 'ABT-USDC' -> base = 'ABT'
    """
    base_currency = product_id.split("-")[0].upper()
    logger.info("Placing MARKET SELL ALL for product_id=%s, base_currency=%s",
                product_id, base_currency)

    # List all accounts and find the one for the base currency
    accounts_resp = client.get_accounts()
    logger.info("DEBUG: ListAccountsResponse object: %s", accounts_resp)

    accounts = getattr(accounts_resp, "data", [])
    target_acct = None

    for acct in accounts:
        cur = getattr(acct, "currency", "")
        avail_obj = getattr(acct, "available_balance", None)
        avail_val = getattr(avail_obj, "value", None)
        logger.info("Account currency=%s, available=%s", cur, avail_val)

        if cur.upper() == base_currency:
            target_acct = acct

    if not target_acct:
        logger.warning("No account found for currency %s. Nothing to sell.", base_currency)
        return None

    available_str = getattr(target_acct.available_balance, "value", "0")
    try:
        available = float(available_str)
    except Exception:
        logger.warning("Could not parse available balance '%s' for %s", available_str, base_currency)
        return None

    if available <= 0:
        logger.warning("Account %s has 0 available. Nothing to sell.", base_currency)
        return None

    body = {
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {
            # NOTE: Some docs call this market_market_ioc; for base_size
            # many examples use 'market_ioc'. If this fails, the error
            # will show in the logs with the exact message from Coinbase.
            "market_ioc": {
                "base_size": str(available)
            }
        },
    }

    logger.info("Sell order request body: %s", body)
    order = client.create_order(**body)
    logger.info("Sell order response: %s", order)
    return order


# -------------------------------------------------------------------
# Routes
# -------------------------------------------------------------------
@app.route("/", methods=["GET"])
def root():
    """
    Simple root endpoint so you can hit the base URL in a browser
    and confirm the bot is running.
    """
    return "Coinbase bot is running. Use /webhook for TradingView alerts.", 200


@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    """
    Webhook endpoint:

    - GET: simple health-check so you can open it in a browser and
      confirm it's reachable (no more 405 confusion).

    - POST: used by TradingView alerts. Expects JSON like:
        {"signal":"BUY_SIGNAL", "product_id":"ABT-USDC"}
        {"signal":"EXIT_SIGNAL","product_id":"ABT-USDC"}
      Optional:
        {"quote_usd": 5.0}  # override default position size
    """
    if request.method == "GET":
        logger.info("Received GET /webhook (health check)")
        return "Webhook endpoint is alive – send POST from TradingView.", 200

    logger.info("Received POST /webhook")

    # Try to parse JSON body
    try:
        data = request.get_json(force=True, silent=False)
    except Exception as e:
        logger.exception("ERROR parsing JSON from webhook")
        logger.info("Raw body: %s", request.data)
        return jsonify({"status": "error", "reason": "invalid json"}), 400

    logger.info("Incoming webhook payload: %s", data)

    # Extract fields safely
    signal = str(data.get("signal", "")).upper()
    product_id = str(data.get("product_id", "") or DEFAULT_PRODUCT_ID).upper()

    # Allow an override of position size from TradingView; otherwise use default
    quote_usd_raw = data.get("quote_usd", DEFAULT_POSITION_USD)
    try:
        quote_usd = float(quote_usd_raw)
    except Exception:
        logger.warning("Could not parse quote_usd '%s', falling back to DEFAULT_POSITION_USD=%s",
                       quote_usd_raw, DEFAULT_POSITION_USD)
        quote_usd = DEFAULT_POSITION_USD

    logger.info("Parsed signal=%s, product_id=%s, quote_usd=%s",
                signal, product_id, quote_usd)

    # Act on the signal
    try:
        if signal == "BUY_SIGNAL":
            logger.info("Handling BUY_SIGNAL")
            place_market_buy(product_id, quote_usd)

        elif signal == "EXIT_SIGNAL":
            logger.info("Handling EXIT_SIGNAL")
            place_market_sell_all(product_id)

        else:
            logger.warning("Unknown signal '%s' – ignoring.", signal)
            return jsonify({"status": "ignored", "reason": "unknown signal"}), 200

    except Exception as e:
        logger.exception("Error while placing order for signal=%s", signal)
        return jsonify({"status": "error", "reason": str(e)}), 500

    return jsonify({"status": "ok"}), 200


# -------------------------------------------------------------------
# Main entrypoint
# -------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("BOT starting up with config:")
    logger.info("  CB_KEY_NAME: %s", os.getenv("CB_KEY_NAME"))
    # Do NOT log any private key
    logger.info("  DEFAULT_PRODUCT_ID: %s", DEFAULT_PRODUCT_ID)
    logger.info("  DEFAULT_POSITION_USD: %s", DEFAULT_POSITION_USD)
    logger.info("  PORT: %s", PORT)

    app.run(host="0.0.0.0", port=PORT, debug=False)
