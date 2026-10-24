# Data snapshot for the strategy lab (CI only)

Copied into the repository root by `.github/workflows/lab.yml` before the harness runs. Same paths as a local checkout:

| File | Contents |
|---|---|
| `snapshot/data/free/NSXUSD_1min_histdata.parquet` | NSX (Nasdaq-100) 1-minute bars from 2025-03-01 (trimmed from the full histdata file), New York time |
| `snapshot/data/free/NDX_daily_clean.parquet` | official ^NDX daily bars (trading calendar) |
| `snapshot/data/free/VIX_daily_cboe.parquet` | official Cboe VIX daily OHLC |
| `snapshot/reports/us_session_levels/daily_levels_NSX.csv` | per-session PDH/PDL/PDC and Asia/London levels |

Frozen at 2026-10-09 (the research window). Newer /NQ data accumulates in the live bot's Schwab archive.
