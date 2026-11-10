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

`bots/gex_live.py` repeats the SPX 0DTE fetch every minute and rewrites one page that reloads itself in the browser:

```bash
cd ~/trading-backtests && source .venv/bin/activate
python bots/gex_live.py --open          # every 60 seconds until 16:15 New York; Ctrl-C stops
python bots/gex_live.py --every 30      # faster; 20 seconds is the floor
```

The page is `data/schwab/gex/live/es_map.html`. It shows the flip and the walls on `/ES`, gamma, vanna and charm per
level, and a "What changed" block: the strikes with the most contracts traded since the last snapshot, over five
minutes and since the start, plus any move of the walls, the flip, the regime or net gamma. Each snapshot is also
kept (`SPX_IDX_0DTE/live/<date>/`) and one row per snapshot goes to `gex_live.csv`.

Things to know:

- Each cycle is three requests (calls, puts, the `/ES` price). Schwab's limit is reported as 120 requests a minute.
- The Greeks are computed at the index price implied by `/ES` (last price minus the basis), so the map moves with the
  future also before 09:30. The basis is fixed when the run starts: start it before the open when you can, so it is
  taken at the previous cash close.
- Open interest does not change during the day. "New positioning" is read from volume, which shows where contracts
  trade but not whether dealers bought or sold them.
- The status line on the page turns red when the last snapshot is more than three cycles old: the script stopped.
