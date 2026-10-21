# Run the NQ paper bot 24/7 on Northflank's free sandbox

## What the free sandbox gives (checked 2026-10-21)
- **Free resources:** 2 always-on services, 2 jobs and 1 add-on (a database). Compute "doesn't sleep". ([pricing](https://northflank.com/pricing))
- **Smallest plan:** `nf-compute-10`, with 0.1 vCPU and 256 MB RAM. ([plans](https://northflank.com/docs/v1/application/billing/resource-plans-and-quotas))
  - The bot uses about 50 MB, plus about 100 MB briefly during the nightly archive, so it fits.
- **Persistent volumes are not confirmed free.** They're billed per GB while they exist. So this setup uses **no volume**: the bot's files (Schwab token, bars, ledger, audit, 1-minute archive) are kept in the **free PostgreSQL add-on** by `bots/cloud_runner.py`.
- **Catches:**
  - A card is verified at signup.
  - Northflank says the free plan is for exploration, "not production". A paper bot is fine.
  - The container's disk is wiped on every restart. The runner restores everything from the database first.

## How it works
`Dockerfile` → `python bots/cloud_runner.py`. The runner:
1. **Restores** the bot's files from PostgreSQL.
2. On the very first start, **writes your Schwab token** from the `SCHWAB_TOKEN_JSON` secret.
3. **Starts the bot,** and restarts it if it exits.
4. **Saves changed files** to the database every 60 seconds.
5. **Runs the archiver** at 18:30 ET Monday to Saturday, and once right away on the first start.

**No seed file or Mac data is needed on the first start.** The bot pulls yesterday's whole US session and today's minutes from Schwab history, and the archiver pulls Schwab's last ~6 weeks of 1-minute bars.

## Steps
### 1. Put the code on GitHub (private repo)
Northflank builds the container from a Git repository. Secrets and data are excluded by `.gitignore` and `.dockerignore`:
- `.env` and `deploy/nq_paper_bot.env`
- `state/`
- `data/` and `reports/`

Create an **empty private repository** on github.com (e.g. `trading-backtests`), then push this folder to it. I can set up the local Git commit for you.

### 2. Northflank project and database
1. **Sign up** at northflank.com (GitHub login is easiest), and allow it to read the private repository.
2. **Create project:** choose the **Northflank cloud**, region **US East** if offered.
3. **Add-ons → Create add-on → PostgreSQL.** Pick the smallest (free) size.

### 3. Secrets (Project → Secret groups → Create)
Add these variables:

| Variable | Value |
|---|---|
| `SCHWAB_API_KEY` | your app's Client ID |
| `SCHWAB_APP_SECRET` | your app's Client Secret |
| `SCHWAB_CALLBACK_URL` | `https://127.0.0.1:8182` |
| `NQBOT_SYMBOL` | `/NQ` |
| `SCHWAB_TOKEN_JSON` | the whole contents of `state/nqbot/schwab_token.json` from the Mac (used once, on the first start) |

Then, in the same secret group, **link the PostgreSQL add-on** and tick **`POSTGRES_URI`**, not the 🌐 `EXTERNAL_…` one. Northflank injects it as `NF_<ADDON>_POSTGRES_URI`, for example `NF_NQBOT_DB_POSTGRES_URI`, and the runner picks it up automatically.

To print the token file for copying (it's sensitive, so paste it only into Northflank):
```bash
cat ~/trading-backtests/state/nqbot/schwab_token.json
```

### 4. Service
**Create new → Service → Deployment service**:
- **Source:** your repository, branch `main`, **Build type: Dockerfile** (path `/Dockerfile`).
- **Plan:** the smallest available (`nf-compute-10`, or `nf-compute-20` if offered free).
- **Instances:** 1.
- **Networking:** **no ports**. It's a background worker.
- **Secret group:** the one above.

When the build finishes and it starts, the **Logs** tab should show these lines, in order:
1. `restored 0 files … wrote the initial Schwab token … bot started`
2. `NQ paper bot starting`
3. `back-filled … missed minutes`
4. `streamer logged in; subscribing CHART_FUTURES /NQ`

**Then stop the bot on the Mac.** Two bots would paper-trade twice, and Schwab may allow only one stream per login.

### 5. Weekly Schwab login (every < 7 days)
1. In the service, open **Shell** (container terminal) and run:
   ```bash
   python bots/schwab_login.py
   ```
2. Open the link in any browser, log in and click **Allow**.
3. Paste the `https://127.0.0.1:8182/?code=…` address within about 30 seconds.
4. Within a minute the runner saves the new token to the database. Then click **Restart** on the service, and the bot rebuilds its day from saved bars and Schwab history.

### 6. Check on it (Shell)
```bash
tail -n 20 state/nqbot/bot.log
```
```bash
tail -n 5 state/nqbot/ledger/daily.csv
```
```bash
python scripts/stream_audit_report.py
```
The last one needs pandas, which is installed in the image.

## Notes
- **Today's Mac ledger stays on the Mac.** The cloud bot starts its own ledger. Its first day trades normally, because it pulls yesterday's session from Schwab.
- **Data only:** the container can't send orders. Its Schwab client blocks everything except streaming and price history, and the tests enforce it.
- **If Northflank's free plan changes,** the same container runs on any host with a PostgreSQL database. Set `STATE_DB_URL`.
