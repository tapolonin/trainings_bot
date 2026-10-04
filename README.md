# Bot for trainings

Telegram bot with open registration for recording training sessions and preparing monthly club reports. Each user has their own profile, logs and notifications.

**Automatic deployment:** see [CI/CD setup](docs/ci-cd.md) for the public repository,
private assets/secrets/data, GitHub Actions and releases deployed from `main`.

**Manual hosting:** see [the VM deployment guide](docs/deployment.md) for Oracle Always Free
and Google Cloud, installation, moving existing data, automatic startup and backups.
Google's free VM allowance does not cover continuous external IPv4 use. IPv4 is optional
with working IPv6 connectivity; the guide explains the cost and current deployment limits.

## Implemented: logging, editing, PDF reports and reminders

Buttons appear below the bot’s messages, leaving the typing area free. They use readable labels: **Log day**,
**Calendar**, **Earnings**, **Reports**, **Settings**, **Help**, and **Cancel** during a flow.
Slash commands listed below remain available as shortcuts. Type dates, times and profile details when prompted. On your first text message after a restart, the bot removes the old typing-area keyboard.

Manual logging starts with **Log day → Today / Other day → Training / No training**.
Other day opens the same calendar grid used in Calendar; you can also type a date. Training continues to gym, start time and finish
time; No training asks for confirmation. Use this any time without a reminder.

- Open registration in private chats: send `/start` and complete your own profile. Groups are ignored. Setup prompts are cleaned up as you progress.
- Setup for first/last name, Sparte, IBAN, hourly rate, timezone, reminder time and gyms.
- IBAN accepts free-form text in setup and settings, preserving spaces, hyphens and case (one line, up to 100 characters). No checksum validation is applied.
- During registration, add gym names one at a time or separated by semicolons.
  **Use suggested gyms** adds the defaults to your draft list. **Remove gym** lets
  you remove entries before saving. **Next** finishes setup, even with an empty
  list. Draft lists survive restarts and stay separate for each user.
- Defaults: Europe/Berlin, 20:00 and RSG, with four optional suggested gyms.
- `/settings` changes profile details, rate by effective month, reminder preferences and gyms.
- `/train` asks for date, gym, start time and finish time separately, then asks you to save. Whole hours such as `9` mean `09:00`; minutes can be entered as `9:30`. Quick entries also accept `9-16`.
- Choose **Add new gym** in the gym picker, enter its name, then choose **Yes, add to my gyms** or **No, just this training** before entering the start time.
- Start-time buttons are **16 / 16:15** on weekdays and **10 / 11** on Saturday. Finish-time buttons are **19 / 19:15 / 20** on weekdays and **16** on Saturday. Sunday has no suggested times. Suggestions use the training date; you can always type a different time.
- Choose `Wettkampf` in the gym picker to enter a competition city. The location is saved as `Wettkampf Bielefeld`, for example.
- **Back** returns one step during training registration, including the competition city, new gym, date and time prompts. **Cancel** abandons the entry. A gym explicitly added to the saved list remains saved even if you cancel the training.
- Quick entry: `today Herschelschule 16:00-20:00` or `28.09.2026 Herschelschule 16:00-20:00`.
- `/no_training` records a confirmed day without training.
- **Calendar** (`/log`, or `/log 2026-08`) shows a Monday–Sunday calendar. Recorded days have a star (★) and bold-style digits, missing days use regular digits, and future days have a dot. Tap a day to see its sessions, times or no-training/missing status.
- **Earnings** (`/earnings`, or `/earnings 2026-08`) lists the amounts for each session and the total for the month, using the rate applicable to that month. Amounts use German separators; the total is calculated before rounding, as in the report template. Lists show ten entries per page with the full month's total on every page.
- Both sections have month arrows and Choose month within the message. Browsing screens show Back and Main menu. In Calendar, selecting a date reveals actions for that day; changing months hides them again.
- Day details and month navigation update existing messages. You can tap different dates without Cancel, then choose an action when you want to change a record.
- The bot removes its previous screens and your new input/button messages as you navigate. Current screens remain visible, and training data stays in SQLite. Message IDs are tracked across restarts. Cleanup starts with this version; older untracked chat history is not bulk-deleted. Telegram may refuse to delete messages older than 48 hours; the bot tries to remove any inactive buttons instead. See [Telegram's deletion limits](https://core.telegram.org/bots/api#deletemessage).
- There is at most one training per day. Dates identify trainings in the interface, without extra training numbers. Select a date and choose **Edit training** or **Delete training** to open that day's record directly.
- **Edit training** changes its date, gym, start or finish time. Choose Save changes to apply the draft, or Cancel to discard it. Moving an entry to another month uses that month's rate; moving it to an already occupied day is rejected.
- **Delete training** asks for confirmation. Moving or deleting a day's last session makes the day unknown; it does not automatically mark it as no training.
- **Correct day** lets you fill missing information, add training on a recorded day, or clear a no-training record after confirmation.
- Cancel discards an unfinished action and returns to its starting screen, including the selected calendar day or settings menu. Drafts and their return destination survive restarts.
- SQLite persistence with profiles, logs, gyms, rates, notifications and report data scoped by Telegram user ID. Reminders run for every completed profile; one user’s menus and cleanup do not affect another user.

Participant counts stay blank. Sessions must start and finish on the same day;
future dates are rejected. Dates without a year use the current year in your
configured timezone. Re-saving an identical entry does not add it again. A different
training on an occupied day is rejected; edit the existing one instead. Older
multi-entry days are preserved and can still be managed by selecting their times
and locations.

### Navigation

| Screen | Navigation buttons | Behavior |
| --- | --- | --- |
| Main menu | None | Choose a section or start logging. |
| Calendar, Earnings, Settings, Help | Back, Main menu | Back goes up one level; Main menu goes straight home. |
| Selected calendar day | Back, Main menu | Back returns to the month overview. |
| Gym list | Back, Main menu | Back returns to Settings. |
| Logging a day, after the first step | Back, Cancel | Back returns to the previous logging step; Cancel discards the unfinished entry. |
| Setup, first logging step, editing, setting changes, other confirmations, choosing a month | Cancel | Discard the unfinished action and return to where it started. |

Main menu cannot interrupt an active action, including through slash commands.
Back retraces training registration steps; other active actions must be saved or cancelled first. The older `/cancel` command
still acts as Back when browsing; visible browsing buttons use the label Back.

Renaming a gym changes its name in past logs. Archiving removes it from the picker
while keeping its history. A new place can be saved for one session or added to the
picker. The initial hourly rate covers backfilled months too. Subsequent rate
changes start at an explicit month and apply until the next effective rate.

### PDF reports

Choose **Reports → Current month / Previous month / Choose month → Generate PDF**.
You can also use `/report YYYY-MM`. The preview shows training count, hours, the
monthly total and days with missing information. Empty months have no export button.
The PDF includes recorded trainings only, uses your current profile information
and the rate applicable to the selected month, and is delivered directly in Telegram.
After editing logs, generate it again to receive an updated copy.

The existing renderer's behavior is unchanged. The real XLSX and instructions PDF
are private and not included in this repository; see [private assets](assets/README.md).
PDF messages stay in the chat
when you navigate elsewhere; temporary export files are removed after generation.
Generation runs in a worker thread, with a progress message and a retry button if
creation or delivery fails. LibreOffice Calc and Carlito fonts must be installed
on the machine running the bot.

### Reminders

- The **Evening log reminder** (default **20:00**, timezone **Europe/Berlin**)
  asks you to log today only if neither training nor no training is recorded.
- The **Monthly report reminder** defaults to the last day of the month at **20:00**. It reminds you to review that
  month's logs and generate your PDF. **It never generates or sends a PDF automatically.**
- When both prompts are due, they arrive in one message. **Log this day** opens
  that exact day's training/no-training choice; **Review monthly report** opens
  the report preview. Generating a PDF still requires **Generate PDF**.
- **Settings → Notifications** lists all built-in and custom notifications. Each
  has its own schedule, time, message and on/off switch. Existing reminder times
  and enabled settings are carried over when upgrading.
- **New notification** asks for a name, weekly or monthly schedule, time and text.
  Weekly schedules let you select several weekdays with checkmarks, then tap
  **Done**. Monthly schedules use a date from 1–31 or **Last day**; shorter months
  use their last day. Optional `{date}` and `{month}` in the message insert the
  local date and month. Custom messages repeat regardless of training logs.
- Changes stay in a draft until **Save notification**. **Cancel** discards them.
  Custom notifications can be deleted with confirmation; built-in reminders can
  be turned off. Up to 30 custom notifications are supported.
- All schedules use the timezone in Settings. Changes take effect on the next
  check (within 30 seconds). No notification generates a PDF automatically.
- After any notification, the current menu or unfinished input prompt is
  posted below it as a separate, silent message. Your draft is preserved.
- Recording training or no training for a day automatically removes its daily
  reminder, including when you use the calendar or enter an earlier day. If it
  shares a message with the monthly reminder, the monthly part stays. Daily
  reminders too old to delete are replaced with a recorded confirmation.
- New reminders include **Delete notification**. Other notifications stay in
  chat, with their buttons available, until you delete them. Deleting the message leaves its
  recurring schedule enabled. The delete button works across navigation, drafts
  and restarts. Telegram only allows bots to delete messages less than 48 hours
  old; older messages must be deleted manually in Telegram.
- Reminders do not change an unfinished draft. Finish or cancel it before using
  a reminder's buttons.
- Delivery records survive restarts. Failed sends retry after five minutes (or
  Telegram's longer retry delay). If the bot restarts after the reminder time,
  it catches up for the current local day; it does not send a backlog for days
  when it was offline. Daylight-saving changes follow the configured timezone.

The bot must be running to deliver reminders. VM deployment and backup instructions
are in [docs/deployment.md](docs/deployment.md); they must be installed on your server.

## Run locally

Python 3.12 is tested. From the project root:

On Ubuntu 24.04, install system dependencies first:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-venv python3-pip libreoffice-calc fonts-crosextra-carlito fonts-liberation
```

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally:

- `BOT_TOKEN`: a fresh token from BotFather. Revoke the token previously pasted into chat.
- `DATABASE_PATH`: defaults to `data/trainings.sqlite3`.
- `ADMIN_USER_IDS`: optional comma-separated numeric Telegram user IDs with read-only admin access. Empty means no admins. Use `/myid` to see your own ID.

Start:

```bash
.venv/bin/python -m trainings_bot
```

Anyone can open your bot in Telegram, send `/start` and complete their profile.
Setup codes are no longer used; an existing `SETUP_CODE` environment variable is ignored.
The bot registers its command menu on startup. Run only one polling process per token.

The bot reads `.env` from the current directory; existing environment variables take
precedence. Keep `.env`, the database and reference documents private. They are
ignored by Git. A database contains the IBAN and training history; its file mode is
restricted to its OS user. It is not encrypted.

### Adding users and upgrading

Share the bot's Telegram link. Each new user runs `/start` and enters their own
profile, hourly rate and gyms. Keep the existing database when upgrading: current
users keep their profiles and logs. Legacy owner metadata no longer restricts
registration. No reset or invitation code is needed.

PDF requests are rendered one at a time to limit memory use, while each request
uses only that user's data. The existing renderer and templates are unchanged.

### Admin access

Configured admins see **Admin** in their main menu and can also use `/admin`.
The button is hidden for everyone else, and each admin action checks the configured
IDs again. The shared command menu does not advertise `/admin`.

**Admin → select user** shows their profile summary, training calendar with monthly
totals, day details and notification settings. **Download PDF** sends the selected
user's monthly report to the requesting admin. The calendar can browse older months
or use **Choose month**. All these views are read-only: they leave the target user's
profile, logs, schedules and unfinished conversation untouched. IBANs are masked in
the profile summary; the existing PDF contains the full account information required
by its template.

Admins are configured in the server environment, never through public registration.
After changing `ADMIN_USER_IDS`, restart the bot. Removing an ID also prevents use of
that account's old admin buttons after restart. Current users keep their own menus
and data separation.

## Checks

No token or Telegram connection is needed:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Public CI creates synthetic assets with `tools/create_ci_assets.py` in a clean checkout.
Never run that script over your real assets. PDF integration checks require LibreOffice Calc and Carlito fonts. The database
export check uses synthetic logs; reference comparisons also need the original
local workbooks in `Templates/` and the private reference tests (not published):

```bash
RUN_PDF_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

The renderer uses `assets/template.xlsx` and `assets/instructions.pdf`. It fills
19 training rows per form, repeats the complete form for overflow, and appends the
instructions once. It retains German number/date conventions. The local reference
checks cover June (19 entries, 2 PDF pages) and August (24 entries, 3 PDF pages).

## Layout

- `trainings_bot/domain.py`: input validation and parsing.
- `trainings_bot/storage.py`: schema, per-user persistence and legacy-data compatibility.
- `trainings_bot/conversation.py`: setup, settings and logging flows.
- `trainings_bot/history.py`: monthly overview, navigation, edits and deletion.
- `trainings_bot/__main__.py`: Telegram adapter and polling entry point.
- `trainings_bot/reports.py`: saved-log snapshots and PDF delivery preparation.
- `trainings_bot/reminders.py`: scheduled notifications and persistent delivery records.
- `trainings_bot/admin.py`: authorized read-only user, calendar and notification views.
- `trainings_bot/notification_settings.py`: notification menus, weekday selection and editing drafts.
- `trainings_bot/report/`: existing workbook/PDF pipeline (unchanged).
- `tests/`: conversation, persistence and export checks.
- `docs/deployment.md`: VM setup, migration, updates, backups and troubleshooting.
- `deploy/trainings-bot.service`: systemd service for automatic startup and restart.

Telegram integration uses [aiogram 3](https://docs.aiogram.dev/en/latest/).

## Versions and release notes

**Help → What's new** shows the running bot's version and latest changes.
[Changelog](trainings_bot/CHANGELOG.md) records every version; its first entry is
also the single source for the version displayed in the bot. Version 0.1.0 names
the initial public deployment; 0.2.0 is the next release.

For each release, add a new changelog section in the pull request. Use patch
versions for fixes, minor versions for compatible features, and major versions
for incompatible changes. Keep previous sections so the history remains available.
Merge into `main` after CI passes to deploy. The VM retains releases by commit SHA,
so the deployed code and its version travel together. The changelog describes code
versions, not proof of successful deployment: check the deployment job for that.

