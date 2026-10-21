# Run the NQ paper bot 24/7 in the cloud for $0 (no dependency on your Mac)

## Which free host (checked 2026-10-21)
| Host | Cost for this bot | Runs 24/7? | Verdict |
|---|---|---|---|
| **Oracle Cloud Always Free**: AMD VM.Standard.E2.1.Micro (1 GB RAM) | **$0**. The public IPv4 and 10 TB/month of outbound data are included. | Yes | **Use this** |
| Google Cloud free e2-micro (us-east1 / us-central1 / us-west1) | VM free, but its **public IPv4 costs about $3.65/month**. Schwab's servers are IPv4-only, so it can't be avoided. | Yes | Fallback if Oracle doesn't work out |
| Hugging Face Spaces (free CPU) | $0 | **No.** Free Spaces go to sleep. | Not suitable |
| GitHub Actions, Google Cloud Shell | $0 | **No.** Jobs and sessions time out. | Not suitable |

**One Oracle catch:** Always Free VMs that stay idle for 7 days (CPU and network under 20%) can be reclaimed, and this bot is idle almost all the time. The usual fix is to **upgrade the account to Pay-As-You-Go**. You're still billed $0 while you stay inside the Always Free limits, and Oracle stops reclaiming idle VMs. Add a **$1 budget alert** so any charge is caught immediately.
- Sources: [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [reclamation notes](https://blog.51sec.org/2023/02/oracle-cloud-cleaning-up-idle-compute.html), [Google free tier](https://docs.cloud.google.com/free/docs/free-cloud-features), [Google IPv4 charge](https://github.com/robhunter/agentdeals/issues/2704).

## 1. Create the VM (Oracle console, about 15 minutes, done by you)
1. **Sign up** at oracle.com/cloud/free. A card is required for identity checks, and prepaid cards are often refused.
   - Pick **US East (Ashburn)** as the **home region**. It's closest to New York and can't be changed later.
2. **Upgrade to Pay-As-You-Go:** Billing → Upgrade.
   - Then **Billing → Budgets → Create budget** at **$1**, with an email alert at 100%.
3. **Make an SSH key on your Mac:**
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/nqbot -N ""
   ```
   ```bash
   cat ~/.ssh/nqbot.pub
   ```
4. **Create the VM:** Compute → Instances → Create instance.
   - **Image:** Canonical **Ubuntu 24.04**.
   - **Shape:** **VM.Standard.E2.1.Micro** (marked "Always Free eligible").
   - **Networking:** the default VCN with a **public subnet**, and **Assign a public IPv4 address** checked.
   - **SSH keys:** paste the contents of `~/.ssh/nqbot.pub`.
   - **Boot volume:** keep the default (about 47–50 GB; 200 GB is free).
   - If you see "Out of host capacity", try another availability domain or try again later.
5. **Copy the instance's public IP address** from its details page.
6. **Test SSH** (it should print `ok`):
   ```bash
   ssh -i ~/.ssh/nqbot ubuntu@<PUBLIC_IP> 'echo ok'
   ```

## 2. Move the bot (one command, from the Mac)
1. **Stop the bot on the Mac** (Ctrl+C in its terminal), so two bots never run at once.
2. **Push:**
   ```bash
   cd ~/trading-backtests && deploy/push_to_vm.sh ubuntu@<PUBLIC_IP>
   ```

It copies the bot code, your config (paths rewritten for the VM), the Schwab token, the bot's saved bars, ledger and audit, and the Schwab 1-minute archive. Then it installs Python and the four packages, and starts:
- **`nq-paper-bot`:** a systemd service that restarts on its own and after reboots. On every start it rebuilds the day from saved bars plus Schwab history.
- **`nq-archiver.timer`:** saves /NQ and QQQ 1-minute history at 18:30 ET, Monday to Saturday.

It also switches off the Mac's nightly archiver. After this, the Mac can be off.

**To update the bot's code later,** re-run the same command. The VM's own state is kept.

## 3. Weekly Schwab login (the only manual step)
Schwab's login token lasts 7 days. From any computer with your SSH key:
```bash
deploy/vm_login.sh ubuntu@<PUBLIC_IP>
```
1. Open the printed link in any browser, log in and click **Allow**.
2. Paste the `https://127.0.0.1:8182/?code=…` address back within about 30 seconds.

The bot restarts and picks up where it left off. From a phone, an SSH app (e.g. Termius) with the same key works too.

## 4. Check on it
```bash
ssh -i ~/.ssh/nqbot ubuntu@<PUBLIC_IP> 'systemctl is-active nq-paper-bot; tail -n 20 trading-backtests/state/nqbot/bot.log'
```
```bash
ssh -i ~/.ssh/nqbot ubuntu@<PUBLIC_IP> 'tail -n 5 trading-backtests/state/nqbot/ledger/daily.csv'
```
```bash
ssh -i ~/.ssh/nqbot ubuntu@<PUBLIC_IP> 'cd trading-backtests && .venv/bin/python scripts/stream_audit_report.py'
```

The bot's log timestamps follow the VM's clock (UTC); its trading logic always uses New York time. Phone alerts are off, as you asked. Turning them on means adding an ntfy topic, or Telegram or Discord settings, to `deploy/nq_paper_bot.env` and re-running the push.

## Safety
- **Data only:** the VM bot can't send orders. Its Schwab client blocks everything except streaming and price history, and the tests enforce it.
- **Secrets stay private:** the config and token are copied over SSH with owner-only permissions (600) and are never printed.
- **Network:** the VM needs no inbound ports except SSH (22, open by default).
