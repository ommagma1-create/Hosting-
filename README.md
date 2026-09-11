# SIGMA-HOSTING

A Telegram file / script hosting platform in a single Python file (`bot.py`),
built on [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) and SQLite.

## Features

- **File hosting** — upload documents, photos, audio and video straight from Telegram
- **Script runner** — execute `.py .js .ts .sh .rb .php .pl .go .lua .r .java` with per-plan
  timeouts, process slots, live logs and versioned edits
- **Cron jobs** — `every_5m`, `every_15m`, `every_30m`, `every_1h`, `every_6h`, `every_12h`,
  `daily_9am`, `daily_midnight`
- **Plans** — Free, Basic, Pro, Premium, Enterprise (file counts, size caps, RAM, API, terminal)
- **Economy** — points, coins, daily streaks, leaderboard, 12-item shop, referrals, transfers
- **Gamification** — 20 achievements, 10 badges, XP levels, 8 themes, 6 fonts
- **Support** — full ticket system with categories, priorities, threads and staff replies
- **Admin panel** — user management, upload review, broadcasts, audit log, DB backup/vacuum/
  integrity check, live process control, system metrics
- **25 SQLite tables** with indexes, WAL mode and automatic column migrations
- **5 background workers** — cron runner, cleanup, metrics, watchdog, notification queue

## Requirements

- Python 3.9 or newer
- `pyTelegramBotAPI`
- Optional interpreters for the languages you want to run (node, bash, ruby, php, etc.)

## Setup — Linux

```bash
# 1. Get the files
cd ~
mkdir -p sigma-hosting && cd sigma-hosting
# copy bot.py, requirements.txt, .env.example, start.sh here

# 2. Install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
nano .env          # set TOKEN and ADMIN_IDS

# 4. Run
chmod +x start.sh
./start.sh
```

### Run as a systemd service (optional)

```ini
# /etc/systemd/system/sigma-hosting.service
[Unit]
Description=SIGMA-HOSTING Telegram bot
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/sigma-hosting
ExecStart=/home/YOUR_USER/sigma-hosting/start.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sigma-hosting
sudo journalctl -u sigma-hosting -f
```

## Setup — Termux (Android)

```bash
# 1. Base packages
pkg update -y && pkg upgrade -y
pkg install -y python git nano

# 2. Get the files
cd ~
mkdir -p sigma-hosting && cd sigma-hosting
# copy bot.py, requirements.txt, .env.example, start.sh here

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure
cp .env.example .env
nano .env          # set TOKEN and ADMIN_IDS

# 5. Keep Termux awake, then run
termux-wake-lock
chmod +x start.sh
./start.sh
```

Optional extra runtimes in Termux:

```bash
pkg install -y nodejs ruby php perl lua54
```

To keep it running in the background, use `tmux`:

```bash
pkg install -y tmux
tmux new -s sigma
./start.sh
# detach with Ctrl-b then d, reattach with: tmux attach -t sigma
```

## Configuration

| Variable | Required | Description |
| --- | --- | --- |
| `TOKEN` | yes | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | yes | Comma separated Telegram user IDs with admin rights |
| `SIGMA_HOME` | no | Storage base path, defaults to `~/sigma_hosting` |
| `AUTO_APPROVE` | no | `1` to skip manual upload review |

`BOT_TOKEN` is accepted as an alias for `TOKEN`.

Find your Telegram user ID by messaging [@userinfobot](https://t.me/userinfobot).

## Storage layout

```
~/sigma_hosting/
├── sigma.db          SQLite database (WAL mode)
├── files/<uid>/      user uploads
│   └── .versions/    per-file version history
├── logs/<uid>/       script output logs
├── tmp/              scratch space (auto-cleaned daily)
├── backups/          database backups
├── scripts/          shared helper scripts
└── exports/          CSV / ZIP exports
```

## Commands

| Command | Description |
| --- | --- |
| `/start [ref_code]` | Start the bot, apply a referral code |
| `/menu`, `/help` | Main menu, command reference |
| `/profile`, `/daily`, `/stats` | Profile card, daily bonus, platform stats |
| `/files`, `/upload` | File manager, upload instructions |
| `/run <id> [args]` | Run a script |
| `/stop <id>` | Stop a running script |
| `/log <id> [lines]` | View script output |
| `/cron`, `/api` | Scheduled jobs, API keys |
| `/economy`, `/shop` | Points hub, coin shop |
| `/ticket <subject>` | Open a support ticket |
| `/cancel` | Abort the current input prompt |

Admin only: `/admin`, `/ban`, `/unban`, `/approve`, `/reject`, `/broadcast`,
`/addpts`, `/settier`, `/deluser`, `/backup`, `/vacuum`.

## Notes

- Uploads start in `pending` and must be approved before they can execute
  (set `AUTO_APPROVE=1` to change this).
- Running arbitrary user scripts executes untrusted code. Run the bot as an
  unprivileged user, ideally inside a container or dedicated VM.
- The watchdog enforces per-plan run timeouts; the cleanup worker prunes logs,
  expired share tokens and stale rate-limit rows every hour.
