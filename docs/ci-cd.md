# Public repository and automatic deployment

Repository: https://github.com/tapolonin/trainings_bot

## What happens

- Feature branches use pull requests into `main`.
- CI runs on standard GitHub Ubuntu runners with Python 3.12 and 3.14. It creates
  original synthetic assets and runs tests, including a real LibreOffice conversion.
- Private form fidelity checks remain local; public CI cannot verify the club layout.
- A successful push to `main` deploys only when repository variable
  `DEPLOY_ENABLED` equals `true`. Leave it unset until server setup is finished.
- The deployment job uses the `production` environment and a concurrency lock.
  Pull requests never receive Google credentials or deployment permissions.
- GitHub uploads the tested commit through IAP. The VM installs it in a separate
  directory, stops the bot, backs up SQLite, switches the active release, and starts it.
- A failed startup switches back to the previous code. The database is never
  automatically restored: migrations must remain compatible with the previous release.
- Startup checks verify process stability for 15 seconds, not full Telegram readiness.
  After the first deployment, verify the bot in Telegram and generate a report.

## Public/private boundary

Included: application source, synthetic tests, deployment scripts and documentation.
Excluded: `.env`, database files, real XLSX/PDF assets, `Templates/`, `spike/`, the
two local reference tests and template extraction tools. `.gitignore` keeps existing
private copies on your development machine; do not use `git add -f` on them.

`tools/check_public.py` checks tracked filenames and obvious token/key patterns.
It is a supporting check, not a complete secret scanner. Review staged changes.
This project had no commits before publication preparation, so there was no old
history containing private assets to rewrite. The example IBANs in tests are test
values, not copied from the private workbooks.

No license is granted for the private club documents. The generated CI fixtures
are original test data, not a substitute club submission form. Choose a code license
before inviting reuse; no license choice has been made on your behalf.

## First push (local WSL terminal)

Authenticate as an account with write access to this repository. The account used
by the assistant has read-only access. These commands do not upload ignored files:

```bash
cd '/home/tapol/Bot for trainings'
gh auth login
gh auth setup-git
git add .gitignore .env.example .github README.md requirements.txt requirements-ci.txt assets/README.md deploy docs trainings_bot tools/create_ci_assets.py tools/check_public.py
git add tests/test_admin.py tests/test_core.py tests/test_history.py tests/test_multiuser.py tests/test_navigation.py tests/test_reminders.py tests/test_reports.py tests/test_requested_fixes.py tests/test_setup_gyms.py tests/test_telegram.py
.venv/bin/python tools/check_public.py
git diff --cached --stat
git diff --cached
git commit -m 'Add training bot with public CI and private production assets'
git branch -M main
git remote add origin https://github.com/tapolonin/trainings_bot.git
git push -u origin main
```

If `origin` already exists, inspect `git remote -v` and use `git remote set-url origin`
only if it points elsewhere. Don't force-push over remote changes. Set your Git name
and email if Git requests them; you can use GitHub's private noreply email.

The first push runs CI only. Check the Actions tab. Under branch rules for `main`,
require a pull request, require both Python test checks, and block force pushes and
deletion. Under environment `production`, restrict deployment branches to `main`.
Approvals can remain off for automatic deployment after merge.

## Production layout

```text
/opt/trainings-bot/releases/<commit>/     code and per-release venv
/opt/trainings-bot/current               symlink to active release
/etc/trainings-bot/bot.env               token/config, root-only
/var/lib/trainings-bot/trainings.sqlite3 persistent database
/var/lib/trainings-bot/assets/           private XLSX and instructions PDF
/var/backups/trainings-bot/              protected SQLite snapshots
```

Each release has an `assets` symlink to the private directory. The existing PDF
renderer continues using the same file paths relative to the release, unchanged.

## Prepare the VM once

Use the SSH terminal. On this Ubuntu 26.04 VM, use `sudo.ws` while the sudo-rs issue
is unresolved. On systems with working traditional sudo, `sudo` is equivalent.

```bash
sudo.ws apt-get update
sudo.ws apt-get install -y --no-install-recommends python3-venv python3-pip libreoffice-calc fonts-crosextra-carlito fonts-liberation sqlite3 cron
sudo.ws adduser --system --group --home /var/lib/trainings-bot trainingbot
sudo.ws install -d -m 755 /opt/trainings-bot/releases
sudo.ws install -d -o trainingbot -g trainingbot -m 700 /var/lib/trainings-bot
sudo.ws install -d -o root -g trainingbot -m 750 /var/lib/trainings-bot/assets
sudo.ws install -d -o root -g root -m 700 /etc/trainings-bot /var/backups/trainings-bot
sudo.ws install -o root -g root -m 600 /dev/null /etc/trainings-bot/bot.env
sudo.ws nano /etc/trainings-bot/bot.env
```

Skip `adduser` if `trainingbot` already exists. Do not re-run the empty-file install
after configuring secrets. Enter:

```dotenv
BOT_TOKEN=your-current-token
DATABASE_PATH=/var/lib/trainings-bot/trainings.sqlite3
ADMIN_USER_IDS=your-numeric-telegram-id
```

Upload the real `assets/template.xlsx` and `assets/instructions.pdf` privately using
the browser SSH upload button (one at a time into your login home), then:

```bash
sudo.ws install -o root -g trainingbot -m 640 ~/template.xlsx /var/lib/trainings-bot/assets/template.xlsx
sudo.ws install -o root -g trainingbot -m 640 ~/instructions.pdf /var/lib/trainings-bot/assets/instructions.pdf
```

To retain users, stop your local bot, take a SQLite backup as described in the
[manual deployment guide](deployment.md#4-move-existing-users-and-logs-optional), upload
the backup privately, and install it before the first deployment:

```bash
sudo.ws install -o trainingbot -g trainingbot -m 600 ~/trainings-migration.sqlite3 /var/lib/trainings-bot/trainings.sqlite3
```

Skip for a fresh database. Never overwrite a live database. Remove temporary uploaded
copies after checking the migration. Do not run local and production bots with the
same token simultaneously.

Upload `deploy/release.sh` and `deploy/trainings-bot-production.service` from the
reviewed local files via SSH upload, then install:

```bash
sudo.ws install -o root -g root -m 755 ~/release.sh /usr/local/sbin/deploy-trainings-bot
sudo.ws install -o root -g root -m 644 ~/trainings-bot-production.service /etc/systemd/system/trainings-bot.service
sudo.ws systemctl daemon-reload
sudo.ws systemctl enable trainings-bot
```

Don't start yet: `current` is created by the first deployment. If a previous service
is running, stop it before changing its configuration. Provision swap for a 1 GB VM
using the manual guide. Back up the database off the VM as well as locally.

## Google authentication: no permanent key

Use a dedicated deployment service account and Workload Identity Federation (WIF).
Follow [Google's GitHub authentication instructions](https://github.com/google-github-actions/auth)
and [Google's deployment pipeline guide](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines).

In your project, enable Compute Engine, IAP, IAM Credentials, Security Token Service,
and OS Login APIs. Create a service account such as `github-trainings-deploy`.

Create a workload identity pool and GitHub OIDC provider (`https://token.actions.githubusercontent.com`).
Map `google.subject=assertion.sub`, `attribute.repository_id=assertion.repository_id`,
and `attribute.repository_owner_id=assertion.repository_owner_id`. Restrict its
attribute condition to your actual **numeric repository and owner IDs**, `refs/heads/main`,
and workflow `tapolonin/trainings_bot/.github/workflows/ci-cd.yml@refs/heads/main`.
Allow only `push` and `workflow_dispatch` events. Numeric IDs prevent a renamed or
recreated repository name from inheriting access. Get IDs from GitHub's repository API.

Grant the pool's repository-ID principal set `roles/iam.workloadIdentityUser` on the
deployment service account. Don't grant this to all identities in the pool.

Enable OS Login on this VM. Give the deployment service account:

- `roles/compute.osAdminLogin` on this VM (deployment requires sudo).
- Compute read permissions needed by gcloud, such as `roles/compute.viewer` on the project.
- `roles/iap.tunnelResourceAccessor` restricted to this VM, TCP port 22.
- `roles/iam.serviceAccountUser` on the VM's attached service account, if it has one.

Use a VM service account with minimal permissions; the bot itself needs no Google API
permissions. OS Admin Login grants administrator access to this VM: only trusted code
merged into `main` should obtain this deployment identity. Don't grant project Editor.

The VM firewall must allow TCP 22 from `35.235.240.0/20` on its actual VPC, targeting
the VM's network tag. IAP reaches the internal IPv4 address even when external IPv4
is disabled. See [IAP access configuration](https://docs.cloud.google.com/iap/docs/using-tcp-forwarding).
Keep your own administrator access when enabling OS Login and verify it before closing
the current SSH session.

## GitHub configuration and first deployment

Under **Settings → Secrets and variables → Actions → Variables**, configure:

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | Your Google project ID |
| `GCP_ZONE` | Actual VM zone, e.g. `us-east1-c` |
| `GCP_VM` | `trainings-bot` |
| `GCP_WIF_PROVIDER` | Full `projects/NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER` name |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | Deployment service account email |
| `DEPLOY_ENABLED` | `true` only after the server and IAM setup are complete |

These are configuration identifiers, not private keys. Do not add the Telegram
token or club assets to GitHub secrets. The service loads them locally on the VM.

Run **Actions → CI and deploy → Run workflow → main** for the first deployment.
Later merges deploy automatically. Inspect `sudo.ws journalctl -u trainings-bot -n 50`
if startup fails. Re-deploying the same SHA is deliberately refused to avoid replacing
an existing release; inspect a failed release before removing it or deploy a new commit.

Keep several releases for rollback and monitor disk space; this initial script does
not delete old releases or backups. Schedule daily backups and off-VM copies before
depending on the service. No cloud resources or IAM permissions are created by a push.

## Local checks matching public CI

Run these in a **clean clone**, not the working folder containing private assets:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-ci.txt
.venv/bin/python tools/check_public.py
.venv/bin/python tools/create_ci_assets.py
RUN_PDF_TESTS=1 .venv/bin/python -m unittest discover -s tests -q
```

Install LibreOffice/Carlito first. The synthetic asset generator refuses to overwrite
existing files. Public CI uses standard runners; larger runners are not selected.
See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
