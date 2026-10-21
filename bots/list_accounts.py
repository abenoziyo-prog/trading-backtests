"""List the accounts the Schwab API login can see: masked account number and account type only.

    set -a; . deploy/nq_paper_bot.env; set +a; python bots/list_accounts.py

Read-only: two GET requests (account numbers, account summaries). Prints nothing else: no hashes,
balances or positions. Used to check whether a thinkorswim paperMoney account is reachable via the API.
"""
from __future__ import annotations

import os

from schwab.auth import client_from_token_file


def mask(n: str) -> str:
    return "****" + n[-4:] if len(n) > 4 else "****"


def main() -> None:
    c = client_from_token_file(os.environ["SCHWAB_TOKEN_PATH"], os.environ["SCHWAB_API_KEY"], os.environ["SCHWAB_APP_SECRET"])
    nums = c.get_account_numbers()
    nums.raise_for_status()
    accts = c.get_accounts()  # summaries without positions
    accts.raise_for_status()
    types = {a["securitiesAccount"]["accountNumber"]: a["securitiesAccount"].get("type", "?") for a in accts.json()}
    rows = nums.json()
    print(f"Accounts visible to this API login: {len(rows)}")
    for r in rows:
        n = r["accountNumber"]
        print(f"  {mask(n)}  type: {types.get(n, '?')}")


if __name__ == "__main__":
    main()
