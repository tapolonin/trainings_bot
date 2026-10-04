# Deploy on a VM

Use Ubuntu 24.04 LTS and one running bot process. The bot uses outbound Telegram
long polling; no domain, HTTPS certificate or inbound web port is needed. It still
needs internet access. Closing your SSH terminal will not stop the systemd service.

These instructions have not been executed on a cloud VM for this project yet.
They use `/opt/trainings-bot` and a dedicated Linux account named `trainingbot`.

## 1. Choose and create a VM

Provider limits checked on 29 September 2026. Check the linked pricing before creation.

### Oracle: an Always Free option

In your account's home region, create a Compute instance with an **Always Free
eligible** Ubuntu 24.04 image and **VM.Standard.E2.1.Micro** shape. It has 1 GB RAM
and includes a public IP. Use a 50 GB boot volume, staying within your account's
200 GB total free block storage allowance. Select a public subnet with an internet
gateway and assign a public IPv4 address. Save your SSH private key securely.

Oracle may have no capacity in your region and may reclaim idle free instances.
A small bot can qualify as idle. Keep an off-server backup; this option does not
guarantee uninterrupted service. See [Oracle's Always Free limits and reclamation rules](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

Allow incoming TCP 22 from your own public IP in the network security rules.
Keep outbound internet access enabled. Connect using the VM's public IP:

```bash
chmod 600 /path/to/your-private-key
ssh -i /path/to/your-private-key ubuntu@VM_IP
```

For the `scp` commands below, also add `-i /path/to/your-private-key` when needed.

### Google Cloud: free compute, optional paid IPv4

The Free Tier has no scheduled end date; it is not a one-year offer. The separate
new-customer trial provides $300 for 90 days. Continued Free Tier use requires an
active billing account. See [Google's Free Program terms](https://docs.cloud.google.com/free/docs/free-cloud-features).

Create a project with billing enabled, enable Compute Engine, then create:

| Setting | Value |
| --- | --- |
| Name | `trainings-bot` |
| Region | `us-central1`, `us-east1` or `us-west1` |
| Machine | E2 → `e2-micro`, standard provisioning (not Spot) |
| Image | Ubuntu 24.04 LTS, ordinary image rather than Ubuntu Pro |
| Boot disk | 30 GB **standard persistent disk**, not balanced or SSD |
| Networking | Ephemeral external IPv4; outbound internet access |
| HTTP / HTTPS firewall boxes | Leave unchecked |

The free allowance covers one VM's worth of hours, 30 GB standard disk and limited
outbound traffic across the billing account. See [Google's free-tier limits](https://docs.cloud.google.com/free/docs/free-cloud-features#compute).

**This configuration is not entirely free.** External IPv4 costs $0.005/hour,
about $3.65 for 730 hours, before taxes and other usage. Only one IPv4 hour per
month is free. Cloud NAT also costs money, so removing the address and adding NAT
does not make it free. See [Google's networking prices](https://cloud.google.com/vpc/network-pricing).

IPv4 is optional for the bot itself. `api.telegram.org` publishes an IPv6 address
(DNS verified on 30 September 2026), and Google does not charge for external IPv6
addresses. A VM with working external IPv6 can reach Telegram without a public IPv4
address or NAT. The recipe above uses IPv4 for convenience, not as a Telegram requirement.
An IPv6 deployment needs an appropriately configured subnet, routing and SSH access;
package repositories and dependency downloads must also be reachable. That deployment
has not yet been tested here. Data-transfer allowances still apply.

Set a billing budget alert and check Billing after deployment. An alert is not a
spending cap. Trial credits are temporary. Restrict SSH access to your own IP or
use Google's browser SSH/IAP setup; do not open bot/web ports.

Use the console's SSH button, or your configured SSH client. In the upload commands,
`VM_USER` is the Linux login name shown in your VM terminal by `whoami`.

## 2. Prepare Ubuntu (on the VM)

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-venv python3-pip libreoffice-calc fonts-crosextra-carlito fonts-liberation sqlite3 nano cron
sudo systemctl enable --now cron
sudo adduser --system --group --home /opt/trainings-bot trainingbot
sudo install -d -o trainingbot -g trainingbot -m 700 /opt/trainings-bot/data /opt/trainings-bot/backups
```

For a 1 GB VM, add 2 GB swap if it has none (`swapon --show`). Run this once on a
fresh VM; do not overwrite an existing swap file:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. Upload the application (from your local WSL terminal)

Replace `VM_USER` and `VM_IP` with your server login and address. This archive
includes runtime assets and tests, but excludes your token, database and reference
documents. It does not depend on the project having been committed to Git.

```bash
cd '/home/tapol/Bot for trainings'
tar -czf /tmp/trainings-bot-code.tar.gz trainings_bot assets tests requirements.txt .env.example README.md docs deploy
scp /tmp/trainings-bot-code.tar.gz VM_USER@VM_IP:~/
```

For Google browser SSH, its Upload file option can upload this archive instead.

On the VM:

```bash
sudo tar --no-same-owner -xzf ~/trainings-bot-code.tar.gz -C /opt/trainings-bot
sudo chown -R trainingbot:trainingbot /opt/trainings-bot
sudo -u trainingbot python3 -m venv /opt/trainings-bot/.venv
sudo -u trainingbot /opt/trainings-bot/.venv/bin/python -m pip install -r /opt/trainings-bot/requirements.txt
sudo -u trainingbot cp /opt/trainings-bot/.env.example /opt/trainings-bot/.env
sudo chmod 600 /opt/trainings-bot/.env
sudo -u trainingbot nano /opt/trainings-bot/.env
```

Set these values in the editor:

```dotenv
BOT_TOKEN=YOUR_CURRENT_BOTFATHER_TOKEN
DATABASE_PATH=data/trainings.sqlite3
ADMIN_USER_IDS=YOUR_NUMERIC_TELEGRAM_ID
```

Use `/myid` in the local bot to obtain your ID. Leave `ADMIN_USER_IDS` empty for no
admin, or use comma-separated IDs. No setup code or owner/chat ID setting is needed:
registration is open, with separate records for each Telegram user. Keep the same
bot token to retain the same Telegram bot and chat history. Do not paste it into
commands or commit `.env`.

## 4. Move existing users and logs (optional)

Skip this section for a completely fresh database. For your current users, keep the
existing database: it contains profiles, IBANs, gyms, rates, entries, notification
schedules and drafts. Code upload alone does not carry them over.

**Stop the local bot first** (Ctrl+C in its terminal, or stop its service). Keep it
stopped after migration: two polling processes with one token conflict. In your local
project directory, create a consistent SQLite backup (default database path shown):

```bash
.venv/bin/python - <<'PY'
import os
import sqlite3
os.umask(0o077)
with sqlite3.connect('file:data/trainings.sqlite3?mode=ro', uri=True) as source:
    with sqlite3.connect('/tmp/trainings-migration.sqlite3') as target:
        source.backup(target)
PY
scp /tmp/trainings-migration.sqlite3 VM_USER@VM_IP:~/
```

On the VM, before starting the service for the first time:

```bash
sudo install -o trainingbot -g trainingbot -m 600 ~/trainings-migration.sqlite3 /opt/trainings-bot/data/trainings.sqlite3
```

This is for an empty destination. Do not overwrite a running or populated database;
stop the service and back it up first. Remove temporary migration copies from both
machines after verifying the migration. Keep a protected recovery copy elsewhere.

## 5. Check and start the service (on the VM)

Run checks before connecting the bot. Tests use temporary databases and mocked
Telegram calls. PDF checks exercise the installed renderer; reference comparisons
may be skipped because private `Templates/` files were not uploaded.

```bash
cd /opt/trainings-bot
sudo -u trainingbot env RUN_PDF_TESTS=1 .venv/bin/python -m unittest discover -s tests -q
sudo cp deploy/trainings-bot.service /etc/systemd/system/trainings-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now trainings-bot
sudo systemctl status trainings-bot --no-pager
```

The service reads `/opt/trainings-bot/.env` and starts automatically after reboot.
It restarts after a process failure. View logs, or apply later code/settings changes:

```bash
sudo journalctl -u trainings-bot -n 50 --no-pager
sudo systemctl restart trainings-bot
```

In Telegram, check `/start`, your existing calendar and Settings, the admin menu for
your admin account, and generate a PDF from a month with training. Add a temporary
custom notification due in a few minutes to check delivery, then delete its schedule.
Verify another account sees its own setup and no Admin button.

## 6. Backups and updates

A consistent manual backup while the service runs:

```bash
sudo -u trainingbot sqlite3 /opt/trainings-bot/data/trainings.sqlite3 ".backup '/opt/trainings-bot/backups/manual.sqlite3'"
sudo chmod 600 /opt/trainings-bot/backups/manual.sqlite3
```

For daily rotating copies, run `sudo crontab -u trainingbot -e` and add:

```cron
15 3 * * * umask 077; /usr/bin/sqlite3 /opt/trainings-bot/data/trainings.sqlite3 ".backup '/opt/trainings-bot/backups/day-$(date +\%d).sqlite3'"
```

This keeps up to 31 copies, replacing the same day number next month. Cron uses the
server timezone; bot reminders use each user's timezone. Verify a file appears after
the scheduled time. Check a backup with `sqlite3 BACKUP_PATH 'PRAGMA integrity_check;'`;
the result should be `ok`.

Copies on the VM do not protect against disk loss or provider reclamation. Regularly
download a copy to protected storage on another machine using SSH/SCP. On the VM,
substitute your actual Linux login for `VM_USER`:

```bash
sudo install -m 600 -o VM_USER /opt/trainings-bot/backups/manual.sqlite3 ~/trainings-backup.sqlite3
```

Then, on your local machine:

```bash
scp VM_USER@VM_IP:~/trainings-backup.sqlite3 ./
chmod 600 trainings-backup.sqlite3
```

Delete the staging copy afterward. Keep `.env` separately protected too. Backups
contain all users' private data.

To update: make a backup, `sudo systemctl stop trainings-bot`, upload/extract a fresh
code archive, restore ownership, reinstall requirements in the existing venv, run
checks, then `sudo systemctl start trainings-bot`. Preserve `.env`, `data/` and
`backups/`; do not repeat the fresh `.env` copy or initial migration steps.

To restore: stop the service, move the current database and any `-wal`/`-shm` files
to a recovery directory, install a verified backup as `data/trainings.sqlite3` with
owner `trainingbot:trainingbot` and mode `600`, then start the service. Restoring an
older backup also rolls back reminder delivery records and may resend a reminder.

## Troubleshooting

- **Conflict / another getUpdates request:** stop the local bot or duplicate service.
- **Unauthorized:** check the token in `.env` and restart the service.
- **No PDF / missing soffice:** install Calc and Carlito, check `soffice --version`
  and `fc-match Carlito`, then inspect the service log. Do not change the generator.
- **Killed during export:** inspect `free -h` and `swapon --show`; check VM memory.
- **No notification:** check that user's timezone, enabled schedule and whether the
  day already has information. Confirm the service is running and internet works.
- **No Back in a draft started before this update:** Cancel and start logging again;
  earlier drafts do not contain the saved step history.
- **Bot stops when your laptop closes:** it is still running locally; finish the VM
  migration and enable the systemd service.

The cloud setup, transfer and service activation remain operator steps. Adding these
files to the project does not create a VM, schedule backups or deploy the bot.
