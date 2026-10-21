#!/usr/bin/env bash
# Move the NQ paper bot from this Mac to the cloud VM and start it there (run on the Mac).
#
#   deploy/push_to_vm.sh ubuntu@<VM public IP> [~/.ssh/nqbot]
#
# Copies only what the bot needs: the quant package, the bot scripts, deploy files, the env file (paths
# rewritten for the VM), the Schwab token, the bot's state (bars, ledger, audit, seed) and the Schwab archive.
# Then runs deploy/vm_bootstrap.sh on the VM. Stop the bot on this Mac first: two bots would stream and
# paper-trade twice. Re-run any time to update the code on the VM (state on the VM is kept, not overwritten).
set -euo pipefail
HOST=${1:?usage: deploy/push_to_vm.sh ubuntu@<VM IP> [ssh key]}
KEY=${2:-$HOME/.ssh/nqbot}
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

if pgrep -f "bots/nq_paper_bot.py" >/dev/null; then
  echo "The bot is still running on this Mac. Stop it first (Ctrl+C in its terminal), then re-run." >&2
  exit 1
fi
FIRST=0
$SSH "$HOST" 'test -d trading-backtests/state/nqbot/bars' || FIRST=1

rsync -az -e "$SSH" --relative \
  pyproject.toml src/quant/__init__.py src/quant/live/ \
  bots/nq_paper_bot.py bots/schwab_login.py bots/schwab_archiver.py bots/seed_levels.py bots/list_accounts.py \
  scripts/stream_audit_report.py \
  deploy/vm_bootstrap.sh deploy/vm_login.sh deploy/nq-paper-bot.service deploy/nq-archiver.service deploy/nq-archiver.timer \
  deploy/nq_paper_bot.env.example deploy/SETUP.md \
  "$HOST:trading-backtests/"

# env file with VM paths; never printed
sed "s|$ROOT|/home/ubuntu/trading-backtests|g" deploy/nq_paper_bot.env | $SSH "$HOST" 'umask 077; cat > trading-backtests/deploy/nq_paper_bot.env'

if [ "$FIRST" = 1 ]; then  # first deploy: bring the Mac's state, token and archive along
  rsync -az -e "$SSH" --relative state/nqbot/ data/schwab/ "$HOST:trading-backtests/"
  echo "copied bot state and Schwab archive (first deploy)"
else
  rsync -az -e "$SSH" --relative state/nqbot/schwab_token.json "$HOST:trading-backtests/" 2>/dev/null || true
fi

$SSH "$HOST" 'bash trading-backtests/deploy/vm_bootstrap.sh'

# this Mac no longer runs the archiver (the VM's nightly timer does)
if launchctl list 2>/dev/null | grep -q com.nqbot.archiver; then
  launchctl unload "$HOME/Library/LaunchAgents/com.nqbot.archiver.plist" && echo "stopped the Mac's nightly archiver"
fi
echo "Done. The bot runs on $HOST. Weekly Schwab login: deploy/vm_login.sh $HOST"
