# Coinbase Advanced Trading Bot (TradingView Webhook)

This repository contains a minimal TradingView → Webhook → Coinbase Advanced auto-trading bot.

## 🚀 What it does

- Listens for TradingView alerts at `/webhook`
- Accepts signals:
  - `BUY_SIGNAL`
  - `EXIT_SIGNAL`
- Converts TradingView tickers to Coinbase format
- Places market IOC orders using Coinbase Advanced API
- Supports fixed USD position size

---

## 📁 Files

### `bot.py`
Flask server + Coinbase API request handler.

### `requirements.txt`
Python dependencies.

### `start.sh`
Startup script for Render.com.

---

## 🚀 Deploy to Render.com

1. Create a **new Web Service**
2. Connect this GitHub repo
3. Set:

**Start Command:**
```
./start.sh
```

### Add Environment Variables:

```
CB_API_KEY=your_coinbase_api_key
CB_API_SECRET=your_base64_secret
CB_API_PASSPHRASE=your_passphrase
TRADE_SIZE=1000
```

Deploy → Render gives you a URL like:

```
https://your-bot.onrender.com/webhook
```

Use this as your **TradingView Webhook URL**.

---

## 📡 TradingView Alert JSON

Set the alert message to:

```json
{
  "signal": "{{alert_message}}",
  "symbol": "{{ticker}}",
  "price": "{{close}}",
  "time": "{{time}}"
}
```

Make sure your Pine Script uses:

- `"BUY_SIGNAL"`
- `"EXIT_SIGNAL"`

---

## ✔️ Testing

Before real trading, set:

```
TRADE_SIZE=5
```

Force a buy/exit in TradingView (Bar Replay) and check logs.

---

## 🎉 You're ready!

You now have a full 24/7 Coinbase Advanced autobot via TradingView.
