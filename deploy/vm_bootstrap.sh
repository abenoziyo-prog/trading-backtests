#!/usr/bin/env bash
# One-time setup on the cloud VM (Ubuntu 22.04/24.04, user "ubuntu"). Run by deploy/push_to_vm.sh; safe to re-run.
# Installs Python + the bot's four packages, adds swap (1 GB VMs), and enables the bot service and nightly archiver.
set -euo pipefail
APP=/home/ubuntu/trading-backtests
cd "$APP"

if ! swapon --show | grep -q swapfile; then          # 1 GB RAM: swap keeps pip/pandas from running out of memory
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip >/dev/null
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q "schwab-py>=1.5" pandas numpy pyarrow

mkdir -p state/nqbot data/schwab
chmod 600 deploy/nq_paper_bot.env state/nqbot/schwab_token.json 2>/dev/null || true
sudo cp deploy/nq-paper-bot.service deploy/nq-archiver.service deploy/nq-archiver.timer /etc/systemd/system/
sudo sed -i 's|^ReadWritePaths=.*|ReadWritePaths=/home/ubuntu/trading-backtests/state /home/ubuntu/trading-backtests/data|' \
  /etc/systemd/system/nq-paper-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now nq-archiver.timer
sudo systemctl enable nq-paper-bot
sudo systemctl restart nq-paper-bot
sleep 15
systemctl --no-pager --lines=0 status nq-paper-bot | head -5
tail -n 12 state/nqbot/bot.log
