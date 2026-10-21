"""Log in to Schwab and save the token file the NQ paper bot reads. Run once, then again every < 7 days.

    python bots/schwab_login.py

Works over SSH on a headless server: it prints a Schwab login URL; open it in your own browser, log in on
Schwab's site and approve, then paste the full address you were redirected to (it starts with your callback
URL; the page itself may show an error, that is expected). The token is saved to SCHWAB_TOKEN_PATH (mode 600).
Authentication only: the resulting client is wrapped read-only and no account or order endpoint is called.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant.live.schwab_feed import ReadOnlyClient, refresh_token_hours_left  # noqa: E402


def main() -> None:
    env = os.environ
    missing = [k for k in ("SCHWAB_API_KEY", "SCHWAB_APP_SECRET", "SCHWAB_CALLBACK_URL", "SCHWAB_TOKEN_PATH") if not env.get(k)]
    if missing:
        sys.exit(f"missing environment variables: {', '.join(missing)} (see deploy/nq_paper_bot.env.example)")
    from schwab.auth import client_from_manual_flow

    token = Path(env["SCHWAB_TOKEN_PATH"])
    token.parent.mkdir(parents=True, exist_ok=True)
    if token.exists():
        token.unlink()  # an expired refresh token cannot be renewed; start a fresh login
    client = ReadOnlyClient(client_from_manual_flow(env["SCHWAB_API_KEY"], env["SCHWAB_APP_SECRET"],
                                                    env["SCHWAB_CALLBACK_URL"], str(token)))
    token.chmod(0o600)
    print(f"Saved {token}. Refresh token valid for {refresh_token_hours_left(client):.0f} more hours.")


if __name__ == "__main__":
    main()
