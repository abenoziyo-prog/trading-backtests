# Fetching GEX and 0DTE data on your own computer

`bots/gex_snapshot.py` can run on your Mac with plain Python. It talks to Schwab directly, so Northflank, the database
and the port-forward are not involved. It only reads option chains: no account or order call exists in the code.

What you get, in `data/schwab/gex/`:

| File | Content |
|---|---|
| `gex_0dte.csv` | One row per run for SPX and NDX: nearest expiry, net GEX, regime (positive / negative), gamma flip, call and put walls, the five largest open-interest strikes of the calls and of the puts |
| `gex_daily.csv` | One row per run for QQQ, NDX, SPY, SPX over the next 30 days of expiries (skipped with `--zero-dte-only`) |
| `<SYMBOL>_0DTE/`, `<SYMBOL>/` | The raw chain rows behind each run |

## One-time setup

1. **Open a terminal in the repository and get the latest code.**
   ```bash
   cd ~/trading-backtests
   git pull origin main
   source .venv/bin/activate
   pip install -e ".[nqbot]"
   ```
2. **Find your Schwab app's key and secret.** Sign in at <https://developer.schwab.com>, open *Dashboard*, then your
   app. Copy the *App Key* and the *Secret*, and note the *Callback URL* exactly as it is written there.
3. **Put them in `.env`** (the file is ignored by git, so it never leaves your computer). Create it from the example
   if you have none, then fill in the three Schwab lines:
   ```bash
   [ -f .env ] || cp .env.example .env
   open -e .env
   ```
   ```
   SCHWAB_API_KEY=your app key
   SCHWAB_APP_SECRET=your secret
   SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
   ```
   The callback must match the one on the Schwab app character for character.
4. **Log in once.**
   ```bash
   python bots/schwab_login.py
   ```
   It prints a Schwab address. Open it in your browser, sign in and approve. The browser then lands on a page that
   fails to load: that is expected. Copy the whole address from the address bar and paste it into the terminal.
   The token is saved to `state/nqbot/schwab_token.json`.

## Each day

Run it shortly before the US open (09:20 New York time), when the exchanges' overnight open interest is in:

```bash
cd ~/trading-backtests && source .venv/bin/activate
python bots/gex_snapshot.py --zero-dte-only     # SPX and NDX 0DTE, a few seconds
python bots/gex_snapshot.py                      # the same plus the 30-day chains, about a minute
```

Each index block ends with the same levels on its future (`$SPX` on `/ES`, `$NDX` on `/NQ`): level + basis, where
basis = future - index at one moment. While the cash index is open (09:30 to 16:00 New York) both prices are live.
Outside those hours the index is frozen at its last close, so the basis is taken at that close (the future's 15:59
bar). The basis drifts by a point or two in a day and steps at each quarterly roll: read the futures levels as zones.

The terminal shows each symbol's regime, flip, walls and largest open-interest strikes; the rows are appended to
`data/schwab/gex/gex_0dte.csv`. The run can be repeated during the session: gamma and the flip move with price, the
open interest does not (it is yesterday's close figure all day).

The free S&P history used for the day-size call needs no key:

```bash
python bots/fetch_spx_gex.py
```

**Open interest can be 0 very early.** On 2026-11-10 every request to Schwab (chains and quotes, every expiry) returned
open interest 0 from 06:22 to 06:28 New York time, and the unchanged fetch returned full figures at 06:34. Why is not
known; one day's observation is all there is. To start early and let the fetch keep trying every 5 minutes:

```bash
python bots/gex_snapshot.py --zero-dte-only --wait 240
```

## Every 7 days

Schwab's login expires after 7 days. When a run fails with an authorization error, repeat step 4.

## Things to know

- **The cloud bot uses the same Schwab app.** It keeps its own token. If the cloud bot stops authenticating after a
  local login, log in again for it the way you did when it was set up.
- **Local and cloud files are separate copies.** A local run writes to this computer only; the cloud runner keeps
  writing to its database on its own schedule.
- Run before 16:00 New York time for today's expiry. After that the 0DTE row describes the next session's expiry.

## Live map during the session

`bots/gex_live.py` fetches the SPX 0DTE chain as fast as Schwab's request limit allows and updates an ES map in the
browser:

```bash
cd ~/trading-backtests && source .venv/bin/activate
python bots/gex_live.py --open                   # ES from SPX, about every 1.1 seconds until 16:15 New York; Ctrl-C stops
python bots/gex_live.py --symbols '$SPX' SPY     # a second map from SPY next to it: about every 1.6 seconds
python bots/gex_live.py --open --stream          # /ES price from the quote stream: about every 0.55 seconds
python bots/gex_live.py --every 10               # slower
python bots/gex_live.py --verbose                # print every cycle (default: a status line every 15 seconds)
```

Open `data/schwab/gex/live/live_map.html` once. The page does not reload: it looks for new data four times a second
and redraws in place, so scroll position, toggles and the open table stay as they are. With SPY there are two maps
side by side (`es_map.html`, `es_spy_map.html`; each can be opened alone). A map shows the flip and the walls on
`/ES`, gamma, vanna and charm per level, and a "What changed" block: the strikes with the most contracts traded since
the last snapshot, over five minutes and since the start, plus any move of the walls, the flip, the regime or net
gamma. One snapshot a minute is kept (`SPX_IDX_0DTE/live/<date>/`) and one row per snapshot and symbol goes to
`gex_live.csv`.

Things to know:

- Pace. A cycle is two requests: the `/ES` price and the SPX chain, calls and puts together. Schwab's limit is
  reported as 120 requests a minute (the figure comes from the schwabdev library's documentation, not from Schwab's
  own pages). The loop spends 110 of them (`--rate`), which is one cycle every 1.1 seconds; `--rate 120` makes it 1.0.
  A cycle cannot be shorter than Schwab takes to answer, about a second. The two requests are sent at the same time,
  except in the two minutes before the access token is renewed. If Schwab does not answer the combined request with
  both sides, the loop asks for calls and puts separately from then on (1.6 seconds) and says so. If Schwab answers
  429 (too many requests), the loop slows down by half and says so. Do not run another script against the same
  Schwab app at the same time: the limit is shared.
- Requests overlap. A new request goes out on schedule while earlier answers are still on their way (at most four),
  so one slow answer does not delay the next snapshot. An answer that arrives after a newer one is dropped.
- In cash hours (09:30 to 16:00 New York) the SPX price inside each chain answer is live, so the loop stops asking
  for the `/ES` price at every snapshot. `/ES` is then SPX plus the basis, and the real `/ES` price is asked for every
  10 seconds to renew the basis. A snapshot costs one request, which gives one about every 0.58 seconds. The terminal
  says when this starts. If the basis jumps by more than 5 points between two checks, the index price is taken as
  not live and the loop goes back to asking every time. `--price-every-snapshot` turns this off.
- `--stream` needs Schwab's Trader API (`userPreference`), which an app with only "Market Data Production" does not
  have: the stream then fails with 401 Unauthorized and the loop carries on without it.
- `--stream` takes the `/ES` price from Schwab's quote stream instead of asking for it at every snapshot. The price
  is then the last trade at the moment the chain arrives, and a snapshot costs one request instead of two, so the
  same budget gives one every 0.55 seconds. The terminal says when the price comes from the stream and when the loop
  has gone back to asking (stream not connected). Schwab may allow only one stream per login: do not use `--stream`
  while another program streams with the same Schwab login. The cloud `/NQ` paper bot does; whether a second stream
  disturbs it has not been tested.
- Schwab's data does move that fast: on 2026-11-10 before the open, at one snapshot every 3 seconds, option volume
  changed in 99% of snapshots and the `/ES` price (the newest 1-minute candle including the minute still forming) in
  about 90%.
- SPY has no basis. Its strikes are moved onto `/ES` by the price ratio `/ES` / SPY (about 10), taken once when the
  run starts from SPY's newest 1-minute bar and the future's bar of the same minute.
- SPY gamma covers the SPY chain alone. It is a separate position from the SPX chain, with its own walls and flip:
  read each map on its own, and compare where the levels of the two sources agree.
- The Greeks are computed at the index price implied by `/ES` (last price minus the basis), so the map moves with the
  future also before 09:30. The basis is fixed when the run starts: start it before the open when you can, so it is
  taken at the previous cash close.
- Open interest does not change during the day. "New positioning" is read from volume, which shows where contracts
  trade but not whether dealers bought or sold them.
- The status line on the page turns red when the last snapshot is more than 20 seconds old (or five cycles, when
  that is longer): the script stopped.
