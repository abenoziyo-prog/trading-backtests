#!/usr/bin/env bash
# Weekly Schwab login for the bot on the VM (Schwab's refresh token lasts 7 days). Works from any computer
# with SSH; no files needed on that computer except the SSH key.
#
#   deploy/vm_login.sh ubuntu@<VM public IP> [~/.ssh/nqbot]
#
# Prints a Schwab login URL: open it in any browser, log in, Allow, then paste the 127.0.0.1 address you are
# sent to (within ~30 s). The bot is restarted afterwards and rebuilds its day from saved bars + Schwab history.
set -euo pipefail
HOST=${1:?usage: deploy/vm_login.sh ubuntu@<VM IP> [ssh key]}
KEY=${2:-$HOME/.ssh/nqbot}
ssh -t -i "$KEY" "$HOST" 'cd trading-backtests && set -a && . deploy/nq_paper_bot.env && set +a \
  && .venv/bin/python bots/schwab_login.py && sudo systemctl restart nq-paper-bot && sleep 12 && tail -n 8 state/nqbot/bot.log'
