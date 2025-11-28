import os
import time
from uuid import uuid4

from flask import Flask, request
from coinbase.rest import RESTClient

# =========================================================
#  Environment / config
# =========================================================

CB_KEY_NAME = os.environ.get("CB_KEY_NAME")
CB_KEY_SECRET = os.environ.get("CB_KEY_SECRET")

if not CB_KEY_NAME or not CB_KEY_SECRET:
    raise RuntimeError("CB_KEY_NAME and CB_KEY_SECRET must be set in environment.")

# Default product + position size (USD) for BUYs, can be overridden by payload
DEFAULT_PRODUCT_ID = os.environ.get("DEFAULT_PRODUCT_ID", "ABT-USDC")
DEFAULT_POSITION_USD = float(os.environ.get("DEFAULT_POSITION_USD", "1.0"))

PORT = int(os.environ.get("PORT", "10000"))

print("========================================================")
print(" Bot starting up with config:")
print(f"  CB_KEY_NAME.........: {CB_KEY_NAME}")
print(f"  DEFAULT_PRODUCT_ID..: {DEFAULT_PRODUCT_ID}")
print(f"  DEFAULT_POSITION_USD: {DEFAULT_POSITION_USD}")
print(f"  PORT................: {PORT}")
print("========================================================")

# =========================================================
#  Coinbase client
# =========================================================

client = RESTClient(
    api_key=CB_KEY_NAME,
    api_secret=CB_KEY_SECRET,
)

def list_accounts_debug():
    """Print a compact summary of all accounts for debugging."""
    try:
        resp = client.get_accounts()
        accounts = resp.get("accounts", [])
        print("=== ACCOUNTS SNAPSHOT ===")
        for acc in accounts:
            curr = acc.get("currency")
            aval = acc.get("available_balance", {}).get("value")
            print(f"  {curr}: available={aval}")
        print("=========================")
    except Exception as e:
        print(f"Error listing accounts: {e}")


def place_market_buy(product_id: str, quote_size: float):
    """
    Place a MARKET BUY using quote_size (in USDC).
    """
    print(f"Placing MARKET BUY: product_id={product_id}, quote_size={quote_size}")
    body = {
        "client_order_id": str(uuid4()),
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": str(quote_size)
            }
        }
    }
    resp = client.place_order(body)
    print(f"BUY response: {resp}")
    return resp


def place_market_sell_all(product_id: str):
    """
    Sell ALL available base asset for the given product.

    Example: product_id = 'ABT-USDC'
    We look for the ABT account and sell its full available_balance as base_size.
    """
    base_currency = product_id.split("-")[0]
    print(f"Preparing MARKET SELL ALL for base currency {base_currency} (product {product_id})")

    # Get accounts and find the matching base currency
    resp = client.get_accounts()
    accounts = resp.get("accounts", [])

    target = None
    for acc in accounts:
        curr = acc.get("currency")
        aval = acc.get("available_balance", {}).get("value")
        if curr == base_currency:
            target = acc
            print(f"Found account for {curr}, available={aval}")
            break

    if target is None:
        print(f"WARNING: No account found for currency {base_currency}. Nothing to sell.")
        return None

    available_str = target.get("available_balance", {}).get("value", "0")
    try:
        available = float(available_str)
    except ValueError:
        print(f"WARNING: Could not parse available balance '{available_str}' for {base_currency}.")
        return None

    if available <= 0:
        print(f"WARNING: Available balance for {base_currency} is {available}. Nothing to sell.")
        return None

    body = {
        "client_order_id": str(uuid4()),
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {
            "market_market_ioc": {
                "base_size": str(available)
            }
        }
    }

    print(f"Placing MARKET SELL for {available} {base_currency} on {product_id}")
    resp = client.place_order(body)
    print(f"SELL response: {resp}")
    return resp

# =========================================================
#  Flask app
# =========================================================

app = Flask(__name__)


@app.route("/", methods=["GET"])
def health():
    return "Bot is running", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    # Log the incoming request at a high level
    print("=== Incoming /webhook request ===")
    print(f"Method: {request.method}")
    print(f"Headers: {dict(request.headers)}")
    print(f"Raw body: {request.data}")

    # Parse JSON safely
    try:
        data = request.get_json(force=True)
    except Exception as e:
        print(f"JSON parse error: {e}")
        return "Bad JSON", 400

    print(f"Parsed JSON: {data}")

    if not isinstance(data, dict):
        print("Payload is not a dict, ignoring.")
        return "Invalid payload", 400

    signal = data.get("signal")
    product_id = data.get("product_id", DEFAULT_PRODUCT_ID)

    print(f"signal={signal}, product_id={product_id}")

    if signal == "BUY_SIGNAL":
        # BUY using DEFAULT_POSITION_USD (or override later if you want)
        try:
            place_market_buy(product_id, quote_size=DEFAULT_POSITION_USD)
        except Exception as e:
            print(f"Error placing BUY: {e}")
            return "Error placing BUY", 500

    elif signal == "EXIT_SIGNAL":
        # SELL ALL base asset for that product
        try:
            place_market_sell_all(product_id)
        except Exception as e:
            print(f"Error placing SELL: {e}")
            return "Error placing SELL", 500
    else:
        print("Unknown or missing signal field, ignoring payload.")

    return "OK", 200


if __name__ == "__main__":
    # Optional: small delay so logs show clearly after boot
    time.sleep(1)
    list_accounts_debug()
    app.run(host="0.0.0.0", port=PORT, debug=False)
