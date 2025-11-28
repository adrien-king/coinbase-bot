import os
import json
import logging
from time import sleep

from flask import Flask, request, jsonify
from requests.exceptions import RequestException
from coinbase.rest import RESTClient

# ---------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Environment / Coinbase client
# ---------------------------------------------------------------------
CB_KEY_NAME   = os.environ.get("CB_KEY_NAME")
CB_KEY_SECRET = os.environ.get("CB_KEY_SECRET")

if not CB_KEY_NAME or not CB_KEY_SECRET:
    raise RuntimeError("CB_KEY_NAME and CB_KEY_SECRET must be set as environment variables.")

# Default product & position size (in QUOTE currency, e.g. USDC)
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC").upper()
POSITION_USD       = float(os.environ.get("POSITION_USD", "1.0"))

logger.info("Using DEFAULT_PRODUCT_ID=%s POSITION_USD=%s", DEFAULT_PRODUCT_ID, POSITION_USD)

client = RESTClient(api_key=CB_KEY_NAME, api_secret=CB_KEY_SECRET)

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def debug_list_accounts(accounts):
    """
    Log all accounts & available balances for the current portfolio.
    This is the debug step that shows what the bot can actually see.
    """
    logger.info("===== DEBUG: Listing all accounts returned by Coinbase =====")
    for acct in accounts:
        try:
            cur   = acct["currency"]
            avail = acct["available_balance"]["value"]
        except Exception:
            # In case library changes format
            logger.info("Raw account object: %s", acct)
            continue

        logger.info("Account currency=%s, available=%s", cur, avail)
    logger.info("============================================================")


def get_accounts_with_retry(max_attempts: int = 3, delay_sec: float = 1.0):
    """
    Fetch accounts with simple retry logic for transient network issues.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            accounts = client.get_accounts()
            return accounts
        except RequestException as e:
            logger.warning(
                "Network error on get_accounts attempt %d/%d: %s",
                attempt, max_attempts, e
            )
            sleep(delay_sec)

    raise RuntimeError("Failed to fetch accounts from Coinbase after retries.")


def place_market_buy(product_id: str, quote_size: float):
    """
    Place a MARKET BUY using quote_size in the quote currency
    (e.g. $1 USDC of ABT-USDC).
    """
    logger.info(
        "Placing MARKET BUY: product_id=%s, quote_size=%s",
        product_id, quote_size
    )

    order_req = {
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(quote_size)
            }
        },
    }

    for attempt in range(1, 4):
        try:
            resp = client.create_order(**order_req)
            logger.info("Buy order response: %s", resp)
            return resp
        except RequestException as e:
            logger.warning(
                "Network error on BUY attempt %d/3: %s",
                attempt, e
            )
            sleep(1.0)

    raise RuntimeError("Failed to place BUY order after retries.")


def place_market_sell_all(product_id: str):
    """
    Sell ALL available base currency for the given product.
    Example: product_id='ABT-USDC' → sell all ABT into USDC.
    """
    base, quote = product_id.split("-")
    logger.info(
        "Placing MARKET SELL for all holdings in %s, product_id=%s",
        base, product_id
    )

    # 1) Get portfolio accounts
    accounts = get_accounts_with_retry()

    # 2) Debug: log everything we see
    debug_list_accounts(accounts)

    # 3) Find the base asset account (e.g. ABT)
    base_acct = None
    for acct in accounts:
        if acct.get("currency") == base:
            base_acct = acct
            break

    if not base_acct:
        logger.warning(
            "WARNING: No account found for currency %s. Nothing to sell.",
            base
        )
        return None

    available_str = base_acct["available_balance"]["value"]
    available = float(available_str)

    if available <= 0:
        logger.warning(
            "WARNING: Account %s has 0 available. Nothing to sell.",
            base
        )
        return None

    logger.info("Base currency %s available to sell: %s", base, available_str)

    order_req = {
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {
            "market_market_ioc": {
                "base_size": f"{available:.8f}"  # sell everything we see
            }
        },
    }

    for attempt in range(1, 4):
        try:
            resp = client.create_order(**order_req)
            logger.info("Sell order response: %s", resp)
            return resp
        except RequestException as e:
            logger.warning(
                "Network error on SELL attempt %d/3: %s",
                attempt, e
            )
            sleep(1.0)

    raise RuntimeError("Failed to place SELL order after retries.")


# ---------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------
app = Flask(__name__)


@app.route("/", methods=["GET"])
def healthcheck():
    return "coinbase-bot is running", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView sends:
        {
          "signal": "BUY_SIGNAL" or "EXIT_SIGNAL",
          "product_id": "ABT-USDC"    (optional, falls back to DEFAULT_PRODUCT_ID)
        }
    """
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        logger.exception("Failed to parse JSON payload.")
        return jsonify({"status": "error", "detail": "invalid JSON"}), 400

    logger.info("Incoming webhook payload: %s", payload)

    if not isinstance(payload, dict):
        return jsonify({"status": "error", "detail": "payload must be an object"}), 400

    signal = payload.get("signal")
    product_id = payload.get("product_id", DEFAULT_PRODUCT_ID).upper()

    if signal not in ("BUY_SIGNAL", "EXIT_SIGNAL"):
        logger.warning("Unknown or missing signal: %s", signal)
        return jsonify({"status": "ignored", "detail": "unknown signal"}), 200

    try:
        if signal == "BUY_SIGNAL":
            place_market_buy(product_id, POSITION_USD)
        else:  # EXIT_SIGNAL
            place_market_sell_all(product_id)

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.exception("Error while handling webhook")
        return jsonify({"status": "error", "detail": str(e)}), 500


# ---------------------------------------------------------------------
# Local run (Render uses start.sh, but this keeps it runnable anywhere)
# ---------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
