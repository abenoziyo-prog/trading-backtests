# NQ paper bot container (Schwab data only, simulated fills). Built by Northflank (or any container host).
# State lives in a database (STATE_DB_URL), not in the image or on a volume: see bots/cloud_runner.py.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 \
    NQBOT_STATE_DIR=/app/state/nqbot SCHWAB_TOKEN_PATH=/app/state/nqbot/schwab_token.json
WORKDIR /app

RUN pip install "schwab-py>=1.5" "psycopg[binary]>=3.1" pandas numpy pyarrow \
 && useradd --create-home --uid 10001 bot

COPY pyproject.toml ./
COPY src/quant/__init__.py src/quant/
COPY src/quant/live/ src/quant/live/
COPY bots/nq_paper_bot.py bots/schwab_login.py bots/schwab_archiver.py bots/seed_levels.py bots/list_accounts.py bots/cloud_runner.py bots/gex_snapshot.py bots/
COPY scripts/stream_audit_report.py scripts/
# shadow book: the lab's ideas run on the bot's bars (lab/ and ideas/ hold code only, no data)
COPY lab/ lab/
COPY ideas/ ideas/
RUN mkdir -p state/nqbot data/schwab && chown -R bot:bot /app
USER bot

CMD ["python", "bots/cloud_runner.py"]
