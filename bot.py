import os
import json
import uuid
import logging

from flask import Flask, request, jsonify
import requests

# Coinbase CDP JWT helper
from cdp.auth.utils.jwt import generate_jwt, JwtOptions

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# === ENV VARS ===
# These come from the Coinbase key you already created
CB_KEY_NAME = os.getenv("CB_KEY_NAME")          # API key name (organizations/.../apiKeys/...)
CB_KEY_SECRET = os.getenv("CB_KEY_SECRET")      # The full private key block

# Default product and position size
DEFAULT_PRODUCT_ID = os.getenv("DEFAULT_PRODUCT_ID", "SOL-USD")
POSITION_USD = float(os.getenv("POSITION_USD", "5"))   # default $5 per trade

COINBASE_HOST = "api.coinbase.com"


def build_jwt(method: str, path: str) -> str:
    """
    Build a short-lived JWT for a single Coinbase API request.
    """
    if not CB_KEY_NAME or not CB_KEY_SECRET:
        raise RuntimeError("CB_KEY_NAME or CB_KEY_SECRET not set in environment variables.")

    options = JwtOptions(
        api_key_id=CB_KEY_NAME,
        api_key_secret=CB_KEY_SECRET,
        request_method=method,
        request_host=COINBASE_HOST,
        request_path=path,
        expires_in=120,  # 2 minutes
    )

    return generate_jwt(options)


def coinbase_request(method: str, path: str, json_body: dict | None = None):
    """
    Generic helper to call Coinbase Advanced Trade REST API with JWT auth.
    """
    jwt_token = build_jwt(method, path)

    url = f"https://{COINBASE_HOST}{path}"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    app.logger.info(f"Sending {method} {url} body={json_body}")

    resp = requests.request(method, url, headers=headers, json=json_body, timeout=10)
    app.logger.info(f"Coinbase response {resp.status_code}: {resp.text}")

    return resp


def get_base_position_size(product_id: str) -> float:
    """
    Look up how much of the base asset (e.g. SOL in SOL-USD) is available.
    We’ll use this to sell everything on an EXIT signal.
    """
    base_currency = product_id.split("-")[0]

    path = "/api/v3/brokerage/accounts"
    resp = coinbase_request("GET", path)

    if not resp.ok:
        app.logger.error(f"List accounts failed: {resp.status_code} {resp.text}")
        return 0.0

    data = resp.json()
    for acct in data.get("accounts", []):
        if acct.get("currency") == base_currency:
            value = acct.get("available_balance", {}).get("value")
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

    return 0.0


def place_market_buy(product_id: str, quote_size: float):
    """
    Place a MARKET IOC BUY using quote_size (e.g. $5 of SOL-USD).
    """
    path = "/api/v3/brokerage/orders"

    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "BUY",
        "order_configuration": {
            "market_market_ioc": {
                "quote_size": f"{quote_size:.2f}"
            }
        },
    }

    return coinbase_request("POST", path, body)


def place_market_sell_full(product_id: str):
    """
    Sell the full available base position for this product_id.
    """
    size = get_base_position_size(product_id)
    if size <= 0:
        app.logger.warning(f"No {product_id.split('-')[0]} available to sell.")
        return None

    path = "/api/v3/brokerage/orders"

    body = {
        "client_order_id": str(uuid.uuid4()),
        "product_id": product_id,
        "side": "SELL",
        "order_configuration": {
                "market_market_ioc": {
                    "base_size": f"{size:.8f}"
                }
        },
    }

    return coinbase_request("POST", path, body)


@app.route("/", methods=["GET"])
def health():
    return "Bot is running!", 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    TradingView will POST JSON here.

    Example for BUY:
    {
      "signal": "BUY_SIGNAL",
      "product_id": "SOL-USD"
    }

    Example for EXIT:
    {
      "signal": "EXIT_SIGNAL",
      "product_id": "SOL-USD"
    }

    If product_id is missing, DEFAULT_PRODUCT_ID is used.
    """
    data = request.get_json(force=True, silent=True) or {}

    app.logger.info(f"Incoming webhook: {data}")

    signal = (data.get("signal") or data.get("action") or "").upper()
    product_id = data.get("product_id") or DEFAULT_PRODUCT_ID

    if signal == "":
        return jsonify({"error": "Missing 'signal' or 'action' in JSON"}), 400

    if signal in ("BUY_SIGNAL", "BUY", "LONG"):
        resp = place_market_buy(product_id, POSITION_USD)
        if resp is None:
            return jsonify({"status": "error", "message": "Buy failed"}), 500
        return jsonify({"status": "ok", "side": "BUY", "product_id": product_id}), 200

    elif signal in ("EXIT_SIGNAL", "SELL", "CLOSE"):
        resp = place_market_sell_full(product_id)
        if resp is None:
            return jsonify({
                "status": "no_position",
                "message": f"No available {product_id.split('-')[0]} to sell."
            }), 200
        return jsonify({"status": "ok", "side": "SELL", "product_id": product_id}), 200

    else:
        return jsonify({"error": f"Unknown signal: {signal}"}), 400


if __name__ == "__main__":
    # For local testing only
    app.run(host="0.0.0.0", port=5000)
