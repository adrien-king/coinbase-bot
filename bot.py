import os
import logging
import time

from flask import Flask, request, jsonify
from coinbase.rest import RESTClient
import requests

# ------------------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Config from environment variables
# ------------------------------------------------------------------------------

CB_KEY_NAME = os.environ.get("CB_KEY_NAME")
CB_KEY_SECRET = os.environ.get("CB_KEY_SECRET")
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "SOL-USDC")
POSITION_USD = float(os.environ.get("POSITION_USD", "1"))  # $ size per trade

if not CB_KEY_NAME or not CB_KEY_SECRET:
    logger.warning("CB_KEY_NAME or CB_KEY_SECRET not set! Coinbase client will fail.")

# Coinbase Advanced REST client
client = RESTClient(api_key=CB_KEY_NAME, api_secret=CB_KEY_SECRET)

# Flask app
app = Flask(__name__)

# ------------------------------------------------------------------------------
# Helper: retry wrapper for Coinbase calls
# ------------------------------------------------------------------------------

def with_retries(func, *args, retries=3, delay=0.5, **kwargs):
    """
    Run func with retries on network errors.
    """
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except requests.exceptions.RequestException as e:
            logger.warning(
                f"Network error on {func.__name__}, attempt {attempt}/{retries}: {e}"
            )
            if attempt == retries:
                raise
            time.sleep(delay)
        except Exception:
            # For unexpected errors, don't retry by default
            raise

# ------------------------------------------------------------------------------
# Health endpoint
# ------------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return "Coinbase bot is running", 200

# ------------------------------------------------------------------------------
# Coinbase order helpers
# ------------------------------------------------------------------------------

def place_market_buy(product_id: str, quote_size_usd: float):
    """
    Place a MARKET BUY using quote_size (USDC / USD side of the pair).
    Example product_id: 'ABT-USDC', 'SOL-USDC', 'BTC-USD'
    """
    logger.info(
        f"Placing MARKET BUY: product_id={product_id}, quote_size={quote_size_usd}"
    )

    order = with_retries(
        client.market_order_buy,
        client_order_id="",       # let Coinbase generate one
        product_id=product_id,
        quote_size=str(quote_size_usd),
    )

    logger.info(f"Buy order response: {order.to_dict()}")
    return order


def place_market_sell_all(product_id: str):
    """
    Sell ALL available balance of the base asset in product_id.
    Example: product_id 'ABT-USDC' -> base asset 'ABT'
    """
    base_currency = product_id.split("-")[0]
    logger.info(
        f"Placing MARKET SELL for all holdings in {base_currency}, product_id={product_id}"
    )

    # Get all accounts with retries
    accounts = with_retries(client.get_accounts)

    # Coinbase Advanced SDK returns an object with an 'accounts' attribute
    all_accounts = getattr(accounts, "accounts", accounts)

    # DEBUG: dump all accounts so we can see what Coinbase returns
    try:
        logger.info("DEBUG: Listing all accounts returned by Coinbase:")
        for acct in all_accounts:
            cur = getattr(acct, "currency", None)
            available_value = None
            if hasattr(acct, "available_balance"):
                bal = acct.available_balance
                # bal is usually a dict-like: {'value': '...', 'currency': '...'}
                if isinstance(bal, dict):
                    available_value = bal.get("value")
                else:
                    # Some SDK versions wrap it differently
                    available_value = getattr(bal, "value", None)
            logger.info(f"  Account currency={cur}, available={available_value}")
    except Exception as e:
        logger.warning(f"DEBUG: Failed to log accounts: {e}")

    # Find the base currency account
    base_account = None
    for acct in all_accounts:
        if getattr(acct, "currency", None) == base_currency:
            base_account = acct
            break

    if not base_account:
        logger.warning(f"No account found for currency {base_currency}. Nothing to sell.")
        return None

    # Extract available balance
    bal = base_account.available_balance
    if isinstance(bal, dict):
        available_str = bal.get("value", "0")
    else:
        available_str = getattr(bal, "value", "0")

    try:
        available = float(available_str)
    except (TypeError, ValueError):
        logger.warning(
            f"Could not parse available balance for {base_currency}: {available_str}"
        )
        return None

    logger.info(f"Available {base_currency} balance: {available}")

    if available <= 0:
        logger.warning(f"Balance for {base_currency} is zero. Nothing to sell.")
        return None

    order = with_retries(
        client.market_order_sell,
        client_order_id="",
        product_id=product_id,
        base_size=str(available),   # sell entire available base balance
    )

    logger.info(f"Sell order response: {order.to_dict()}")
    return order

# ------------------------------------------------------------------------------
# Webhook endpoint for TradingView alerts
# ------------------------------------------------------------------------------

def parse_webhook_payload():
    """
    Try to parse TradingView webhook payload as JSON.
    TradingView usually sends exactly what you put in the "Message" box.
    """
    data = request.get_json(force=True, silent=True)
    if isinstance(data, dict):
        return data

    # If JSON parsing fails, try raw text
    try:
        raw = request.data.decode("utf-8").strip()
        logger.info(f"Raw webhook payload (non-JSON): {raw}")
    except Exception:
        raw = None

    return {} if data is None else data


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView should send JSON like:

    { "signal": "BUY_SIGNAL",  "product_id": "ABT-USDC" }
    { "signal": "EXIT_SIGNAL", "product_id": "ABT-USDC" }

    If product_id is missing, DEFAULT_PRODUCT_ID is used.
    """
    try:
        payload = parse_webhook_payload()
    except Exception as e:
        logger.exception("Failed to parse webhook payload")
        return "Invalid JSON", 400

    logger.info(f"Incoming webhook payload: {payload}")

    if not isinstance(payload, dict):
        return "Payload must be a JSON object", 400

    signal = payload.get("signal")
    product_id = payload.get("product_id") or DEFAULT_PRODUCT_ID

    if signal not in ("BUY_SIGNAL", "EXIT_SIGNAL"):
        logger.warning(f"Unknown signal: {signal}")
        return "Unknown signal", 400

    try:
        if signal == "BUY_SIGNAL":
            order = place_market_buy(product_id, POSITION_USD)
            return jsonify(
                {
                    "status": "ok",
                    "action": "buy",
                    "product_id": product_id,
                    "order": order.to_dict() if order else None,
                }
            ), 200

        elif signal == "EXIT_SIGNAL":
            order = place_market_sell_all(product_id)
            return jsonify(
                {
                    "status": "ok",
                    "action": "sell",
                    "product_id": product_id,
                    "order": order.to_dict() if order else None,
                }
            ), 200

    except Exception as e:
        logger.exception("Error while placing order")
        return "Error while placing order", 500

# ------------------------------------------------------------------------------
# Local run (Render uses gunicorn via start.sh)
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    # For local testing
    app.run(host="0.0.0.0", port=port)
