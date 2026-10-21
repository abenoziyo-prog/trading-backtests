# NQ paper bot: setup (zero cost, 24/7)

**What it does:**
- Streams real-time **/NQ 1-minute bars** from Schwab.
- Runs **system R** (GF-1, ORB-C, S3, L2, plus the one-position picker) bar by bar.
- Fills trades **on paper inside the bot**, and sends you an alert for every entry and exit so you can mirror it in **thinkorswim paperMoney**.

**What it never does:** send an order to Schwab.

**Code:**
- `src/quant/live/`: engine, store, calendar, Schwab feed, alerts, ledger.
- `bots/nq_paper_bot.py`: the runner.
- `bots/schwab_login.py`: Schwab login.

**Verified:**
- `scripts/check_live_engine.py`: the engine reproduces all 1,139 strategy trades and all 627 picker trades from the 18-month backtest exactly.
- `tests/test_live_bot.py`: unit tests and the safety checks.

## 1. Accounts (free)
1. **Schwab brokerage account** with thinkorswim enabled (paperMoney is inside thinkorswim). Futures quotes may need the futures agreement or market-data entitlements on the account; check them in thinkorswim.
2. **Schwab developer app** at developer.schwab.com:
   - Create an app with callback URL `https://127.0.0.1:8182`.
   - Approval can take several days.
   - The streamer address comes from Schwab's user-preferences endpoint, so the app may need the trading product as well as market data.
   - That's fine: the bot can't place orders, because its client is wrapped in `ReadOnlyClient`, which blocks every non-streaming call, and a test fails if any order code appears.
3. **An alert channel** (any of these):
   - [ntfy](https://ntfy.sh): free app, no account. Pick a long random topic name, because anyone who knows the topic can read it.
   - A Telegram bot.
   - A Discord webhook.

## 2. Server (free, 24/7)
**Oracle Cloud Always Free:**
- An Ampere A1 VM (1 OCPU / 6 GB is plenty) with Ubuntu 24.04.
- The free allowance was reportedly cut to 2 OCPU / 12 GB in mid-2026. Stay inside it: pay-as-you-go accounts are billed above the limit.

```bash
sudo apt update && sudo apt install -y python3.12-venv git
# copy the project to /home/ubuntu/trading-backtests (scp or git); data/ and reports/ are not needed to run the bot
cd ~/trading-backtests && python3.12 -m venv .venv && .venv/bin/pip install -e . schwab-py
cp deploy/nq_paper_bot.env.example deploy/nq_paper_bot.env && chmod 600 deploy/nq_paper_bot.env   # fill it in
mkdir -p state/nqbot
```

**Alternative:** run it on this Mac with `deploy/com.nqpaperbot.plist`. It's free, but the Mac must never sleep or go offline.

## 3. Log in to Schwab (every < 7 days)
```bash
set -a; . deploy/nq_paper_bot.env; set +a; .venv/bin/python bots/schwab_login.py
```
1. Open the printed URL in your own browser and log in on Schwab's site.
2. Paste back the address you're redirected to.

**Weekly login:** Schwab's refresh token lasts **7 days**, and no API can renew it. That's the one manual step: do it every weekend.
- The bot alerts you 48 hours before the token expires.
- If it expires, the bot alerts you and keeps retrying until you log in again.

## 4. Start and check
```bash
set -a; . deploy/nq_paper_bot.env; set +a
.venv/bin/python bots/nq_paper_bot.py --test-alert        # one test message to your channels
sudo cp deploy/nq-paper-bot.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now nq-paper-bot
journalctl -u nq-paper-bot -f
```

**First-run check:** in the log, the first five bars report "received N s after its timestamp".
- **N ≈ 60:** correct (Schwab labels bars by their start minute).
- **N ≈ 0:** Schwab labels bars by their end, and every bar would be one minute late. Stop the bot and tell me before trading on it.

**Bar timing (measured 2026-10-21):** Schwab's history bars are final within about 2 seconds after each minute ends. In busy minutes the live stream bar can differ from them (by up to 9 points at 09:41–09:46 ET). So the bot:
- waits **5 seconds** after each minute (`NQBOT_SETTLE_S`), then trades on Schwab's **history** bar for that minute;
- falls back to the stream bar if history hasn't arrived within 30 seconds (`NQBOT_MAX_WAIT_S`);
- logs every minute's source, timing and stream-vs-used difference in `state/nqbot/audit/`.

To check a day against the archived history (after the nightly archive):
```bash
.venv/bin/python scripts/stream_audit_report.py
```
To use stream bars instead (no history lookups), set `NQBOT_BAR_SOURCE=stream`.

**Restarts are safe.** On start or reconnect, the bot pulls the minutes it missed from Schwab history and replays the day without alerts, so its paper positions and ledger match an uninterrupted run. Then it sends one alert for any position still open.

## 5. First day: levels
Each day needs **yesterday's US-session high, low and close**, plus today's **London (02:00–05:00 ET) high**. The bot builds them from bars it has recorded, but Schwab's API has no historical futures bars. So:
- **Easy way:** start the bot at least one full session before you want it to trade. On a day without levels it alerts **NO TRADING** and records the session.
- **To trade on the first day:** write `state/nqbot/seed_levels.json` with yesterday's NQ values from thinkorswim:
  `{"date": "2026-10-22", "pdh": 25500.25, "pdl": 25210.00, "pdc": 25455.50}`

## 6. Daily routine (mirroring in paperMoney)
**Alert sequence:**
- **"ready" (09:30 ET):** today's levels.
- **"SHORT/LONG 1 /NQ now":** the signal, at the close of the signal candle. Enter at market in paperMoney, using the stop and target in the alert.
- **"paper … filled":** the bot's own fill, at the next bar.
- **"EXIT … (stop/target/16:00 close)":** close the trade in paperMoney.
- **"day summary":** after the close.

You can place the stop and target in paperMoney as a bracket order. L2 has no target: it's held to the close.

**The bot's paper fills assume no slippage, per your rules:** the entry is the signal candle's close, and exits are at exactly the stop or target. Your paperMoney fills will differ slightly; comparing the two is part of the test.

**Records:**
- `state/nqbot/ledger/trades.csv`: every trade, taken and skipped.
- `state/nqbot/ledger/daily.csv`: daily and cumulative P&L.
- `state/nqbot/bars/`: every recorded bar.

## Known differences vs the backtest
- **Instrument:** the backtest used NSX (Nasdaq-100 index quotes). Live uses /NQ futures, which trade at a premium to the index, with all levels computed from NQ itself.
- **Contract roll:** when the bot rolls to a new contract (quarterly), the first day's PDH/PDL/PDC come from the old contract. Re-seed or skip that day.
- **Early-close days:** the bot goes flat at 13:00 ET. The backtest held one trade (2025-11-28) until NSX's last quote at 13:14, worth $151 more.
- **Holidays:** taken from `src/quant/live/calendar.py` (2025–2027). Add unscheduled closures in `state/nqbot/calendar_overrides.json`.
