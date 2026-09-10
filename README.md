# Tape — a Kite Connect trading terminal

A self-hosted trading dashboard built on the **official Kite Connect API**: live
candlestick charts, positions, holdings, orders, GTTs, and an order ticket — in
one dark terminal-style page. No Zerodha internal endpoints, no scraped APIs;
everything goes through the documented SDK with your own credentials.

Charting uses TradingView's open-source [Lightweight Charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0).

## Why this instead of cloning kite.zerodha.com

Kite's web app ships a *licensed* TradingView Charting Library and talks to
Zerodha's *private* data endpoints — neither is yours (or mine) to redistribute.
This app reproduces the **functionality** legitimately: the same data and order
actions, via `kiteconnect`, which you already have API access to.

## Setup

1. Create a Kite Connect app at https://developers.kite.trade/apps and note the
   **API key** and **API secret**. Set the app's **Redirect URL** to:

   ```
   http://127.0.0.1:5000/login/callback
   ```

2. Install and configure:

   ```bash
   pip install -r requirements.txt
   cp .env.example .env      # fill in KITE_API_KEY / KITE_API_SECRET
   python app.py
   ```

3. Open http://127.0.0.1:5000 — you'll be sent to Kite to log in, then bounced
   back to the dashboard. The access token is cached for the day (Kite tokens
   expire daily around 6 AM IST; just log in again the next session).

## What's wired up

| Area        | Source |
|-------------|--------|
| Live LTP / charts | `KiteTicker` websocket → SSE → browser; intra-bar candle updates |
| Historical candles | `historical_data()` (1m / 5m / 15m / 1h / 1D) |
| Positions / Holdings LTP + P&L | **driven by the live ticker** — every portfolio instrument is auto-subscribed; the header Day P&L sums live position P&L |
| Margins / structure | `positions()`, `holdings()`, `margins()` polled every 8s only to reconcile structure (new positions, fills); the *numbers* come from ticks |
| Orders / GTT | `orders()`, `get_gtts()` (polled 7s) |
| Order placement | `place_order()` — behind a confirm dialog |
| Instrument search | local index over `instruments()` dump |

## Notes & next steps

- It's a **single-user local tool** — one session object, token cached to
  `.access_token.json` (gitignore it). Don't expose this to the internet as-is;
  there's no multi-user auth and SSE/Flask dev server isn't built for that.
- Historical data API needs the **historical data** subscription on your Kite
  Connect app, or those calls 403.
- The order ticket places **real orders**. The confirm modal is deliberate;
  keep it.
- Natural extensions given your setup: GTT trailing-SL controls in the ticket,
  a multi-instrument watchlist rail, MCX-specific lot-size handling for
  CRUDEOILM/GOLDM, and wiring your SAR / color-match signals as chart overlays.

## File map

```
app.py             Flask routes + SSE
kite_client.py     SDK wrapper, instrument resolver, ticker fan-out
templates/index.html
static/css/style.css
static/js/dashboard.js
```
