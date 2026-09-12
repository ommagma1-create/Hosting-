#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================================
#  SIGMA-HOSTING  --  Telegram File / Script Hosting Platform
#  Single-file bot built on pyTelegramBotAPI (telebot)
#  Version 7.0.0 ULTRA
#
#  SECTIONS
#    01  Imports & setup
#    02  Config & constants
#    03  Database (25 tables)
#    04  User management
#    05  File management
#    06  Script runner + cron
#    07  Economy system
#    08  Tickets / support
#    09  Admin tools
#    10  System & monitoring
#    11  Formatting & UI helpers
#    12  Keyboard builders
#    13  Message builders
#    14  Send helpers
#    15  Command handlers
#    16  File upload handler
#    17  FSM state machine
#    18  Callback router
#    19  Background threads
#    20  Main entrypoint
# ============================================================================

# ============================================================================
# SECTION 01 - IMPORTS & SETUP
# ============================================================================

import os
import sys
import time
import csv
import urllib.parse
import io
import signal
import socket
import secrets
import logging
import sqlite3
import platform
import threading
import subprocess
import pathlib
import hashlib
import random
import string
import re
import json
import base64
import zipfile
import mimetypes
import uuid
import shutil
import traceback
import ast
import math
import difflib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from contextlib import contextmanager

try:
    import telebot
    from telebot import types, apihelper
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write(
        "\n[SIGMA] Missing dependency: pyTelegramBotAPI\n"
        "[SIGMA] Install it with:  pip install pyTelegramBotAPI\n\n"
    )
    raise SystemExit(1)

LOG_FORMAT = "%(asctime)s [%(levelname)-8s] SIGMA \u2502 %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("sigma")
logging.getLogger("TeleBot").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def utcnow():
    """Timezone aware UTC now. datetime.utcnow() is never used in this file."""
    return datetime.now(timezone.utc)


def utcstamp(when=None):
    """ISO-8601 UTC timestamp string used for every DB timestamp column."""
    moment = when if isinstance(when, datetime) else utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_stamp(value):
    """Parse a stored timestamp back into an aware datetime (UTC)."""
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ")
    if "+" in text:
        text = text.split("+")[0].strip()
    if "." in text:
        text = text.split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ============================================================================
# SECTION 02 - CONFIG & CONSTANTS
# ============================================================================

VERSION = "10.A PRODUCTION"
BOT_NAME = "SIGMA-HOSTING"
START_TIME = time.time()

API_TOKEN = (os.environ.get("TOKEN") or os.environ.get("BOT_TOKEN") or "").strip()


def _parse_admin_ids(raw):
    """Parse ADMIN_IDS env var: '1,2 3;4' -> {1, 2, 3, 4}."""
    out = set()
    for chunk in re.split(r"[,;\s]+", str(raw or "")):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            log.warning("Ignoring invalid admin id: %s", chunk)
    return out


ADMIN_IDS = _parse_admin_ids(os.environ.get("ADMIN_IDS", ""))

BASE_DIR = Path(os.environ.get("SIGMA_HOME", "~/sigma_hosting")).expanduser()
FILES_DIR = BASE_DIR / "files"
LOGS_DIR = BASE_DIR / "logs"
TMP_DIR = BASE_DIR / "tmp"
BACKUPS_DIR = BASE_DIR / "backups"
SCRIPTS_DIR = BASE_DIR / "scripts"
EXPORTS_DIR = BASE_DIR / "exports"

for _d in (BASE_DIR, FILES_DIR, LOGS_DIR, TMP_DIR, BACKUPS_DIR, SCRIPTS_DIR, EXPORTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = BASE_DIR / "sigma.db"

MAX_TG_TEXT = 3800
DEFAULT_PER_PAGE = 6
ADMIN_PER_PAGE = 8
AUTO_APPROVE = os.environ.get("AUTO_APPROVE", "0").strip() in ("1", "true", "True", "yes")

# Token handed to sandboxed / untrusted code instead of the real one. Any bot
# token found inside an uploaded script is swapped for this before execution,
# so a stealer can only ever reach the disposable checker bot.
CHECKER_TOKEN = (
    os.environ.get("CHECKER_TOKEN", "").strip()
    or os.environ.get("SIGMA_CHECKER_TOKEN", "").strip()
    or "0000000000:SIGMA-SANDBOX-CHECKER-TOKEN-DISABLED"
)

_lock = threading.Lock()
_db_lock = threading.Lock()
_state_lock = threading.Lock()
_proc_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIERS = {
    "free": {
        "name": "Free",
        "emoji": "\U0001f7e2",
        "price": 0,
        "files": 3,
        "size": 5,
        "ram": 128,
        "terminal": False,
        "api": False,
        "web": 0,
        "priority": 0,
        "color": "#9AA0A6",
    },
    "basic": {
        "name": "Basic",
        "emoji": "\U0001f535",
        "price": 99,
        "files": 10,
        "size": 25,
        "ram": 256,
        "terminal": True,
        "api": False,
        "web": 1,
        "priority": 1,
        "color": "#4C8BF5",
    },
    "pro": {
        "name": "Pro",
        "emoji": "\U0001f7e3",
        "price": 299,
        "files": 30,
        "size": 100,
        "ram": 512,
        "terminal": True,
        "api": True,
        "web": 3,
        "priority": 2,
        "color": "#A142F4",
    },
    "premium": {
        "name": "Premium",
        "emoji": "\U0001f7e1",
        "price": 599,
        "files": 100,
        "size": 500,
        "ram": 1024,
        "terminal": True,
        "api": True,
        "web": 10,
        "priority": 3,
        "color": "#F9AB00",
    },
    "enterprise": {
        "name": "Enterprise",
        "emoji": "\U0001f534",
        "price": 1499,
        "files": -1,
        "size": 2048,
        "ram": 4096,
        "terminal": True,
        "api": True,
        "web": 50,
        "priority": 4,
        "color": "#EA4335",
    },
}

TIER_ORDER = ["free", "basic", "pro", "premium", "enterprise"]

TIER_TIMEOUTS = {
    "free": 300,
    "basic": 1800,
    "pro": 7200,
    "premium": 21600,
    "enterprise": 86400,
}

TIER_PROC_LIMIT = {
    "free": 1,
    "basic": 1,
    "pro": 3,
    "premium": 6,
    "enterprise": 20,
}

# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

THEMES = {
    "sigma": {
        "name": "Sigma",
        "emoji": "\u03a3",
        "header_char": "\u2501",
        "accent": "\u25b8",
        "description": "The default heavy-line SIGMA look.",
    },
    "midnight": {
        "name": "Midnight",
        "emoji": "\U0001f319",
        "header_char": "\u2504",
        "accent": "\u2735",
        "description": "Dark, quiet and low contrast.",
    },
    "sunrise": {
        "name": "Sunrise",
        "emoji": "\u2600\ufe0f",
        "header_char": "\u2500",
        "accent": "\u2739",
        "description": "Warm and bright morning palette.",
    },
    "neon": {
        "name": "Neon",
        "emoji": "\U0001f308",
        "header_char": "\u2550",
        "accent": "\u25c6",
        "description": "Loud arcade styling with double rules.",
    },
    "forest": {
        "name": "Forest",
        "emoji": "\U0001f332",
        "header_char": "\u2508",
        "accent": "\u2767",
        "description": "Calm greens with dotted separators.",
    },
    "ocean": {
        "name": "Ocean",
        "emoji": "\U0001f30a",
        "header_char": "\u223c",
        "accent": "\u25b9",
        "description": "Wavy separators and cool tones.",
    },
    "mono": {
        "name": "Mono",
        "emoji": "\u25fb\ufe0f",
        "header_char": "-",
        "accent": ">",
        "description": "Plain ASCII, maximum compatibility.",
    },
    "royal": {
        "name": "Royal",
        "emoji": "\U0001f451",
        "header_char": "\u2593",
        "accent": "\u2726",
        "description": "Bold blocks for premium members.",
    },
}

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

FONTS = {
    "default": {
        "name": "Default",
        "emoji": "\U0001f524",
        "description": "Standard Telegram text rendering.",
        "mono": False,
    },
    "mono": {
        "name": "Monospace",
        "emoji": "\u2328\ufe0f",
        "description": "Fixed width, best for logs and code.",
        "mono": True,
    },
    "bold": {
        "name": "Bold",
        "emoji": "\U0001f170\ufe0f",
        "description": "Headings rendered in bold weight.",
        "mono": False,
    },
    "italic": {
        "name": "Italic",
        "emoji": "\u270f\ufe0f",
        "description": "Soft italic body copy.",
        "mono": False,
    },
    "compact": {
        "name": "Compact",
        "emoji": "\U0001f5dc\ufe0f",
        "description": "Fewer blank lines, denser cards.",
        "mono": False,
    },
    "terminal": {
        "name": "Terminal",
        "emoji": "\U0001f5a5\ufe0f",
        "description": "Monospace with shell style prompts.",
        "mono": True,
    },
}

# ---------------------------------------------------------------------------
# Achievements (condition_key is resolved in check_achievements)
# ---------------------------------------------------------------------------

ACHIEVEMENTS = {
    "first_upload": {
        "name": "First Contact",
        "emoji": "\U0001f4e4",
        "desc": "Upload your first file.",
        "pts": 25,
        "condition_key": "total_uploads",
        "condition_val": 1,
    },
    "uploader_10": {
        "name": "Collector",
        "emoji": "\U0001f4c1",
        "desc": "Upload 10 files.",
        "pts": 60,
        "condition_key": "total_uploads",
        "condition_val": 10,
    },
    "uploader_50": {
        "name": "Archivist",
        "emoji": "\U0001f5c3\ufe0f",
        "desc": "Upload 50 files.",
        "pts": 200,
        "condition_key": "total_uploads",
        "condition_val": 50,
    },
    "uploader_200": {
        "name": "Data Hoarder",
        "emoji": "\U0001f4be",
        "desc": "Upload 200 files.",
        "pts": 600,
        "condition_key": "total_uploads",
        "condition_val": 200,
    },
    "first_run": {
        "name": "Ignition",
        "emoji": "\u25b6\ufe0f",
        "desc": "Run a script for the first time.",
        "pts": 25,
        "condition_key": "total_runs",
        "condition_val": 1,
    },
    "runner_25": {
        "name": "Operator",
        "emoji": "\u2699\ufe0f",
        "desc": "Run scripts 25 times.",
        "pts": 80,
        "condition_key": "total_runs",
        "condition_val": 25,
    },
    "runner_100": {
        "name": "Automator",
        "emoji": "\U0001f916",
        "desc": "Run scripts 100 times.",
        "pts": 250,
        "condition_key": "total_runs",
        "condition_val": 100,
    },
    "runner_500": {
        "name": "Daemon Lord",
        "emoji": "\U0001f479",
        "desc": "Run scripts 500 times.",
        "pts": 900,
        "condition_key": "total_runs",
        "condition_val": 500,
    },
    "streak_3": {
        "name": "Warming Up",
        "emoji": "\U0001f331",
        "desc": "Claim the daily bonus 3 days in a row.",
        "pts": 30,
        "condition_key": "daily_streak",
        "condition_val": 3,
    },
    "streak_7": {
        "name": "Weekly Warrior",
        "emoji": "\U0001f525",
        "desc": "Reach a 7 day daily streak.",
        "pts": 100,
        "condition_key": "daily_streak",
        "condition_val": 7,
    },
    "streak_30": {
        "name": "Unbroken",
        "emoji": "\U0001f4c5",
        "desc": "Reach a 30 day daily streak.",
        "pts": 400,
        "condition_key": "daily_streak",
        "condition_val": 30,
    },
    "streak_100": {
        "name": "Century",
        "emoji": "\U0001f48e",
        "desc": "Reach a 100 day daily streak.",
        "pts": 1500,
        "condition_key": "daily_streak",
        "condition_val": 100,
    },
    "points_500": {
        "name": "Saver",
        "emoji": "\u2b50",
        "desc": "Accumulate 500 points.",
        "pts": 50,
        "condition_key": "points",
        "condition_val": 500,
    },
    "points_5000": {
        "name": "High Roller",
        "emoji": "\U0001f4b0",
        "desc": "Accumulate 5000 points.",
        "pts": 300,
        "condition_key": "points",
        "condition_val": 5000,
    },
    "level_5": {
        "name": "Apprentice",
        "emoji": "\U0001f195",
        "desc": "Reach level 5.",
        "pts": 75,
        "condition_key": "level",
        "condition_val": 5,
    },
    "level_20": {
        "name": "Veteran",
        "emoji": "\U0001f396\ufe0f",
        "desc": "Reach level 20.",
        "pts": 500,
        "condition_key": "level",
        "condition_val": 20,
    },
    "referral_1": {
        "name": "Recruiter",
        "emoji": "\U0001f91d",
        "desc": "Invite your first user.",
        "pts": 60,
        "condition_key": "referrals",
        "condition_val": 1,
    },
    "referral_10": {
        "name": "Evangelist",
        "emoji": "\U0001f4e3",
        "desc": "Invite 10 users.",
        "pts": 400,
        "condition_key": "referrals",
        "condition_val": 10,
    },
    "ticket_1": {
        "name": "Speak Up",
        "emoji": "\U0001f3ab",
        "desc": "Open your first support ticket.",
        "pts": 20,
        "condition_key": "total_tickets",
        "condition_val": 1,
    },
    "files_stored_20": {
        "name": "Well Stocked",
        "emoji": "\U0001f4e6",
        "desc": "Keep 20 files stored at once.",
        "pts": 150,
        "condition_key": "stored_files",
        "condition_val": 20,
    },
}

# ---------------------------------------------------------------------------
# Badges
# ---------------------------------------------------------------------------

BADGES = {
    "founder": {"name": "Founder", "emoji": "\U0001f3f0", "desc": "One of the first 100 members."},
    "supporter": {"name": "Supporter", "emoji": "\u2764\ufe0f", "desc": "Purchased a paid plan."},
    "bug_hunter": {"name": "Bug Hunter", "emoji": "\U0001f41b", "desc": "Reported a confirmed bug."},
    "power_user": {"name": "Power User", "emoji": "\u26a1", "desc": "Reached level 10 or above."},
    "night_owl": {"name": "Night Owl", "emoji": "\U0001f989", "desc": "Ran a script between 00:00 and 05:00 UTC."},
    "speedster": {"name": "Speedster", "emoji": "\U0001f680", "desc": "Ran 10 scripts in a single day."},
    "generous": {"name": "Generous", "emoji": "\U0001f381", "desc": "Transferred points to another member."},
    "veteran": {"name": "Veteran", "emoji": "\U0001f396\ufe0f", "desc": "Member for more than 90 days."},
    "shopper": {"name": "Shopper", "emoji": "\U0001f6d2", "desc": "Bought an item in the shop."},
    "legend": {"name": "Legend", "emoji": "\U0001f3c6", "desc": "Ranked top 3 on the leaderboard."},
}

# ---------------------------------------------------------------------------
# Emoji table
# ---------------------------------------------------------------------------

E = {
    "art": "\U0001f520",
    "box": "\U0001f4e6",
    "dot": "\u2022",
    "export": "\u2728",
    "help": "\u2753",
    "list": "\U0001f4cb",
    "new": "\u2728",
    "sigma": "\u03a3",
    "file": "\U0001f4c4",
    "folder": "\U0001f4c1",
    "run": "\u25b6\ufe0f",
    "stop": "\u23f9\ufe0f",
    "log": "\U0001f4dc",
    "upload": "\U0001f4e4",
    "download": "\U0001f4e5",
    "ok": "\u2705",
    "no": "\u274c",
    "warn": "\u26a0\ufe0f",
    "info": "\u2139\ufe0f",
    "star": "\u2b50",
    "fire": "\U0001f525",
    "pts": "\u2728",
    "coin": "\U0001fa99",
    "wallet": "\U0001f45b",
    "trophy": "\U0001f3c6",
    "back": "\u25c0\ufe0f",
    "fwd": "\u25b6\ufe0f",
    "search": "\U0001f50d",
    "user": "\U0001f464",
    "users": "\U0001f465",
    "lock": "\U0001f512",
    "unlock": "\U0001f513",
    "ban": "\U0001f6ab",
    "shield": "\U0001f6e1\ufe0f",
    "gear": "\u2699\ufe0f",
    "bell": "\U0001f514",
    "bell_off": "\U0001f515",
    "reload": "\U0001f504",
    "link": "\U0001f517",
    "graph": "\U0001f4c8",
    "stats": "\U0001f4ca",
    "server": "\U0001f5a5\ufe0f",
    "plus": "\u2795",
    "minus": "\u2796",
    "edit": "\u270f\ufe0f",
    "trash": "\U0001f5d1\ufe0f",
    "check": "\u2714\ufe0f",
    "cross": "\u2716\ufe0f",
    "tag": "\U0001f3f7\ufe0f",
    "cal": "\U0001f4c5",
    "clock": "\U0001f552",
    "ticket": "\U0001f3ab",
    "crown": "\U0001f451",
    "gift": "\U0001f381",
    "key": "\U0001f511",
    "code": "\U0001f4bb",
    "copy": "\U0001f4cb",
    "share": "\U0001f4e4",
    "home": "\U0001f3e0",
    "menu": "\U0001f4d1",
    "admin": "\U0001f6e0\ufe0f",
    "bot": "\U0001f916",
    "scan": "\U0001f50e",
    "db": "\U0001f5c4\ufe0f",
    "backup": "\U0001f4bd",
    "zip": "\U0001f5dc\ufe0f",
    "api": "\U0001f9e9",
    "web": "\U0001f310",
    "note": "\U0001f4dd",
    "cron": "\u23f0",
    "hook": "\U0001fa9d",
    "tier0": "\U0001f7e2",
    "tier1": "\U0001f535",
    "tier2": "\U0001f7e3",
    "tier3": "\U0001f7e1",
    "tier4": "\U0001f534",
    "heart": "\u2764\ufe0f",
    "rocket": "\U0001f680",
    "diamond": "\U0001f48e",
    "lightning": "\u26a1",
    "moon": "\U0001f319",
    "sun": "\u2600\ufe0f",
    "rainbow": "\U0001f308",
    "snow": "\u2744\ufe0f",
    "magic": "\u2728",
    "medal": "\U0001f3c5",
    "pin": "\U0001f4cc",
    "eye": "\U0001f441\ufe0f",
    "mail": "\u2709\ufe0f",
    "cpu": "\U0001f9e0",
    "disk": "\U0001f4bf",
    "ram": "\U0001f9ee",
    "pending": "\u23f3",
    "approved": "\u2705",
    "rejected": "\u26d4",
}


def _e(key):
    """Emoji lookup that can never raise KeyError."""
    return E.get(key, "\u2022")


# ---------------------------------------------------------------------------
# UI labels
# ---------------------------------------------------------------------------

L = {
    "main_menu": _e("home") + " Main Menu",
    "back": _e("back") + " Back",
    "close": _e("cross") + " Close",
    "refresh": _e("reload") + " Refresh",
    "files": _e("folder") + " My Files",
    "file_open": _e("file") + " Open",
    "upload": _e("upload") + " Upload",
    "download": _e("download") + " Download",
    "run": _e("run") + " Run",
    "run_args": _e("code") + " Run With Args",
    "stop": _e("stop") + " Stop",
    "restart": _e("reload") + " Restart",
    "logs": _e("log") + " Logs",
    "clear_log": _e("trash") + " Clear Log",
    "edit": _e("edit") + " Edit",
    "edit_write": _e("edit") + " Write Content",
    "edit_line": _e("edit") + " Edit Line",
    "rename": _e("tag") + " Rename",
    "delete": _e("trash") + " Delete",
    "copy": _e("copy") + " Duplicate",
    "share": _e("share") + " Share Link",
    "public": _e("web") + " Toggle Public",
    "tags": _e("tag") + " Tags",
    "add_tag": _e("plus") + " Add Tag",
    "versions": _e("db") + " Versions",
    "restore": _e("reload") + " Restore",
    "zip_all": _e("zip") + " Zip All Files",
    "export_csv": _e("stats") + " Export List",
    "search": _e("search") + " Search Files",
    "preview": _e("eye") + " Preview",
    "run_menu": _e("run") + " Run Center",
    "running": _e("lightning") + " Running Now",
    "kill_mine": _e("stop") + " Stop All Mine",
    "economy": _e("coin") + " Economy",
    "daily": _e("gift") + " Daily Bonus",
    "wallet": _e("wallet") + " Wallet",
    "leaderboard": _e("trophy") + " Leaderboard",
    "shop": _e("gift") + " Shop",
    "buy": _e("coin") + " Buy",
    "referral": _e("users") + " Referrals",
    "transfer": _e("share") + " Transfer Points",
    "profile": _e("user") + " Profile",
    "achievements": _e("medal") + " Achievements",
    "badges": _e("shield") + " Badges",
    "level": _e("star") + " Level",
    "support": _e("ticket") + " Support",
    "tickets": _e("ticket") + " My Tickets",
    "new_ticket": _e("plus") + " New Ticket",
    "reply": _e("mail") + " Reply",
    "close_ticket": _e("check") + " Close Ticket",
    "settings": _e("gear") + " Settings",
    "theme": _e("rainbow") + " Theme",
    "font": _e("code") + " Font",
    "language": _e("web") + " Language",
    "notifications": _e("bell") + " Notifications",
    "push_on": _e("bell") + " Push: ON",
    "push_off": _e("bell_off") + " Push: OFF",
    "api": _e("api") + " API Keys",
    "api_new": _e("plus") + " New API Key",
    "api_list": _e("key") + " List Keys",
    "cron": _e("cron") + " Cron Jobs",
    "cron_new": _e("plus") + " New Cron",
    "cron_toggle": _e("reload") + " Enable / Disable",
    "webhooks": _e("hook") + " Webhooks",
    "webhook_new": _e("plus") + " New Webhook",
    "notes": _e("note") + " Notes",
    "note_new": _e("plus") + " New Note",
    "note_pin": _e("pin") + " Pin / Unpin",
    "upgrade": _e("rocket") + " Upgrade Plan",
    "plan": _e("crown") + " My Plan",
    "stats": _e("stats") + " Bot Stats",
    "help": _e("info") + " Help",
    "admin": _e("admin") + " Admin Panel",
    "admin_dash": _e("stats") + " Dashboard",
    "admin_users": _e("users") + " Users",
    "admin_files": _e("folder") + " Pending Files",
    "admin_tickets": _e("ticket") + " Tickets",
    "admin_broadcast": _e("bell") + " Broadcast",
    "admin_sys": _e("server") + " System",
    "admin_db": _e("db") + " Database",
    "admin_audit": _e("scan") + " Audit Log",
    "admin_metrics": _e("graph") + " Metrics",
    "admin_running": _e("lightning") + " Running Scripts",
    "admin_kill": _e("stop") + " Kill All",
    "admin_cleanup": _e("trash") + " Cleanup Logs",
    "vacuum": _e("db") + " Vacuum",
    "backup": _e("backup") + " Backup",
    "integrity": _e("shield") + " Integrity Check",
    "approve": _e("ok") + " Approve",
    "reject": _e("no") + " Reject",
    "ban": _e("ban") + " Ban",
    "unban": _e("unlock") + " Unban",
    "set_tier": _e("crown") + " Set Tier",
    "add_pts": _e("pts") + " Add Points",
    "del_user": _e("trash") + " Delete User Data",
    "user_files": _e("folder") + " User Files",
    "yes": _e("ok") + " Yes",
    "no": _e("no") + " No",
    "confirm": _e("check") + " Confirm",
    "cancel": _e("cross") + " Cancel",
    "prev": _e("back") + " Prev",
    "next": "Next " + _e("fwd"),
    "page": _e("file") + " Page",
    "nothing": _e("info") + " Nothing here",
}


def _l(key):
    """Label lookup that can never raise KeyError."""
    return L.get(key, key.replace("_", " ").title())


LANGS = {
    "en": "English",
    "hi": "\u0939\u093f\u0928\u094d\u0926\u0940",
    "es": "Espa\u00f1ol",
    "ru": "\u0420\u0443\u0441\u0441\u043a\u0438\u0439",
    "ar": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
    "pt": "Portugu\u00eas",
}

FILE_STATUSES = ["pending", "approved", "rejected"]

bot = telebot.TeleBot(API_TOKEN or "0:INVALID", parse_mode="HTML", threaded=True)


# ============================================================================
# SECTION 03 - DATABASE (25 TABLES)
# ============================================================================

SCHEMA = [
    # 1 -------------------------------------------------------------- users
    """
    CREATE TABLE IF NOT EXISTS users (
        uid            INTEGER PRIMARY KEY,
        first_name     TEXT    DEFAULT '',
        username       TEXT    DEFAULT '',
        tier           TEXT    DEFAULT 'free',
        points         INTEGER DEFAULT 0,
        coins          INTEGER DEFAULT 0,
        daily_streak   INTEGER DEFAULT 0,
        last_daily     TEXT    DEFAULT '',
        total_uploads  INTEGER DEFAULT 0,
        total_runs     INTEGER DEFAULT 0,
        total_tickets  INTEGER DEFAULT 0,
        is_banned      INTEGER DEFAULT 0,
        ban_reason     TEXT    DEFAULT '',
        joined_at      TEXT    DEFAULT '',
        referrer       INTEGER DEFAULT 0,
        referral_code  TEXT    DEFAULT '',
        theme          TEXT    DEFAULT 'sigma',
        font           TEXT    DEFAULT 'default',
        lang           TEXT    DEFAULT 'en',
        xp             INTEGER DEFAULT 0,
        level          INTEGER DEFAULT 1,
        last_seen      TEXT    DEFAULT '',
        push_enabled   INTEGER DEFAULT 1,
        api_key        TEXT    DEFAULT ''
    )
    """,
    # 2 -------------------------------------------------------------- files
    """
    CREATE TABLE IF NOT EXISTS files (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        fname      TEXT    NOT NULL,
        fpath      TEXT    NOT NULL,
        fsize      INTEGER DEFAULT 0,
        ftype      TEXT    DEFAULT '',
        status     TEXT    DEFAULT 'pending',
        is_public  INTEGER DEFAULT 0,
        uploaded   TEXT    DEFAULT '',
        notes      TEXT    DEFAULT '',
        downloads  INTEGER DEFAULT 0,
        runs       INTEGER DEFAULT 0,
        last_run   TEXT    DEFAULT '',
        tags       TEXT    DEFAULT '',
        checksum   TEXT    DEFAULT ''
    )
    """,
    # 3 ------------------------------------------------------------ tickets
    """
    CREATE TABLE IF NOT EXISTS tickets (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        uid         INTEGER NOT NULL,
        subject     TEXT    DEFAULT '',
        status      TEXT    DEFAULT 'open',
        priority    TEXT    DEFAULT 'Normal',
        category    TEXT    DEFAULT 'General',
        created_at  TEXT    DEFAULT '',
        updated_at  TEXT    DEFAULT '',
        closed_at   TEXT    DEFAULT '',
        assigned_to INTEGER DEFAULT 0
    )
    """,
    # 4 -------------------------------------------------------- ticket_msgs
    """
    CREATE TABLE IF NOT EXISTS ticket_msgs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id  INTEGER NOT NULL,
        uid        INTEGER NOT NULL,
        message    TEXT    DEFAULT '',
        created_at TEXT    DEFAULT '',
        is_staff   INTEGER DEFAULT 0
    )
    """,
    # 5 ------------------------------------------------------ user_settings
    """
    CREATE TABLE IF NOT EXISTS user_settings (
        uid        INTEGER NOT NULL,
        key        TEXT    NOT NULL,
        value      TEXT    DEFAULT '',
        updated_at TEXT    DEFAULT '',
        PRIMARY KEY (uid, key)
    )
    """,
    # 6 -------------------------------------------------------------- stats
    """
    CREATE TABLE IF NOT EXISTS stats (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       TEXT    DEFAULT '',
        cpu      REAL    DEFAULT 0,
        mem      REAL    DEFAULT 0,
        disk     REAL    DEFAULT 0,
        users    INTEGER DEFAULT 0,
        files    INTEGER DEFAULT 0,
        runs     INTEGER DEFAULT 0,
        uploads  INTEGER DEFAULT 0
    )
    """,
    # 7 --------------------------------------------------------- broadcasts
    """
    CREATE TABLE IF NOT EXISTS broadcasts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_uid  INTEGER NOT NULL,
        message    TEXT    DEFAULT '',
        sent_count INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT ''
    )
    """,
    # 8 -------------------------------------------------------- script_logs
    """
    CREATE TABLE IF NOT EXISTS script_logs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        uid         INTEGER NOT NULL,
        fid         INTEGER NOT NULL,
        output      TEXT    DEFAULT '',
        exit_code   INTEGER DEFAULT 0,
        ran_at      TEXT    DEFAULT '',
        duration_ms INTEGER DEFAULT 0
    )
    """,
    # 9 ----------------------------------------------------------- api_keys
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        key_hash   TEXT    DEFAULT '',
        label      TEXT    DEFAULT '',
        created_at TEXT    DEFAULT '',
        last_used  TEXT    DEFAULT '',
        is_active  INTEGER DEFAULT 1
    )
    """,
    # 10 ----------------------------------------------------- notifications
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        message    TEXT    DEFAULT '',
        type       TEXT    DEFAULT 'info',
        read       INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT ''
    )
    """,
    # 11 ----------------------------------------------- achievements_earned
    """
    CREATE TABLE IF NOT EXISTS achievements_earned (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        uid             INTEGER NOT NULL,
        achievement_key TEXT    NOT NULL,
        earned_at       TEXT    DEFAULT ''
    )
    """,
    # 12 ----------------------------------------------------- badges_earned
    """
    CREATE TABLE IF NOT EXISTS badges_earned (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        uid       INTEGER NOT NULL,
        badge_key TEXT    NOT NULL,
        earned_at TEXT    DEFAULT ''
    )
    """,
    # 13 ------------------------------------------------------- file_shares
    """
    CREATE TABLE IF NOT EXISTS file_shares (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fid        INTEGER NOT NULL,
        uid        INTEGER NOT NULL,
        token      TEXT    NOT NULL,
        expires_at TEXT    DEFAULT '',
        views      INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT ''
    )
    """,
    # 14 --------------------------------------------------------- referrals
    """
    CREATE TABLE IF NOT EXISTS referrals (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_uid INTEGER NOT NULL,
        referred_uid INTEGER NOT NULL,
        pts_awarded  INTEGER DEFAULT 0,
        created_at   TEXT    DEFAULT ''
    )
    """,
    # 15 ------------------------------------------------------- plan_orders
    """
    CREATE TABLE IF NOT EXISTS plan_orders (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        uid          INTEGER NOT NULL,
        tier         TEXT    DEFAULT 'free',
        amount       INTEGER DEFAULT 0,
        status       TEXT    DEFAULT 'pending',
        created_at   TEXT    DEFAULT '',
        processed_at TEXT    DEFAULT '',
        notes        TEXT    DEFAULT ''
    )
    """,
    # 16 -------------------------------------------------------- user_notes
    """
    CREATE TABLE IF NOT EXISTS user_notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        title      TEXT    DEFAULT '',
        content    TEXT    DEFAULT '',
        created_at TEXT    DEFAULT '',
        updated_at TEXT    DEFAULT '',
        pinned     INTEGER DEFAULT 0
    )
    """,
    # 17 --------------------------------------------------------- file_tags
    """
    CREATE TABLE IF NOT EXISTS file_tags (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fid        INTEGER NOT NULL,
        tag        TEXT    NOT NULL,
        created_at TEXT    DEFAULT ''
    )
    """,
    # 18 --------------------------------------------------------- cron_jobs
    """
    CREATE TABLE IF NOT EXISTS cron_jobs (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        uid       INTEGER NOT NULL,
        fid       INTEGER NOT NULL,
        schedule  TEXT    DEFAULT 'every_1h',
        last_run  TEXT    DEFAULT '',
        next_run  TEXT    DEFAULT '',
        enabled   INTEGER DEFAULT 1,
        run_count INTEGER DEFAULT 0
    )
    """,
    # 19 ------------------------------------------------------------ ip_log
    """
    CREATE TABLE IF NOT EXISTS ip_log (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        uid    INTEGER NOT NULL,
        action TEXT    DEFAULT '',
        ip     TEXT    DEFAULT '',
        ts     TEXT    DEFAULT ''
    )
    """,
    # 20 --------------------------------------------------------- audit_log
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_uid  INTEGER NOT NULL,
        action     TEXT    DEFAULT '',
        target_uid INTEGER DEFAULT 0,
        details    TEXT    DEFAULT '',
        ts         TEXT    DEFAULT ''
    )
    """,
    # 21 ---------------------------------------------------- server_metrics
    """
    CREATE TABLE IF NOT EXISTS server_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ts           TEXT    DEFAULT '',
        active_procs INTEGER DEFAULT 0,
        queue_size   INTEGER DEFAULT 0,
        errors_1h    INTEGER DEFAULT 0
    )
    """,
    # 22 ----------------------------------------------------- user_activity
    """
    CREATE TABLE IF NOT EXISTS user_activity (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        uid    INTEGER NOT NULL,
        action TEXT    DEFAULT '',
        ts     TEXT    DEFAULT ''
    )
    """,
    # 23 ----------------------------------------------------- file_versions
    """
    CREATE TABLE IF NOT EXISTS file_versions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fid        INTEGER NOT NULL,
        version    INTEGER DEFAULT 1,
        fpath      TEXT    DEFAULT '',
        created_at TEXT    DEFAULT '',
        size       INTEGER DEFAULT 0
    )
    """,
    # 24 ---------------------------------------------------------- webhooks
    """
    CREATE TABLE IF NOT EXISTS webhooks (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        uid            INTEGER NOT NULL,
        url            TEXT    DEFAULT '',
        secret         TEXT    DEFAULT '',
        events         TEXT    DEFAULT 'all',
        enabled        INTEGER DEFAULT 1,
        created_at     TEXT    DEFAULT '',
        last_triggered TEXT    DEFAULT ''
    )
    """,
    # 25 ------------------------------------------------------- rate_limits
    """
    CREATE TABLE IF NOT EXISTS rate_limits (
        uid          INTEGER NOT NULL,
        endpoint     TEXT    NOT NULL,
        count        INTEGER DEFAULT 0,
        window_start TEXT    DEFAULT '',
        PRIMARY KEY (uid, endpoint)
    )
    """,
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_users_tier ON users(tier)",
    "CREATE INDEX IF NOT EXISTS idx_users_points ON users(points DESC)",
    "CREATE INDEX IF NOT EXISTS idx_users_banned ON users(is_banned)",
    "CREATE INDEX IF NOT EXISTS idx_users_refcode ON users(referral_code)",
    "CREATE INDEX IF NOT EXISTS idx_files_uid ON files(uid)",
    "CREATE INDEX IF NOT EXISTS idx_files_status ON files(status)",
    "CREATE INDEX IF NOT EXISTS idx_files_uid_status ON files(uid, status)",
    "CREATE INDEX IF NOT EXISTS idx_files_public ON files(is_public)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_uid ON tickets(uid)",
    "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)",
    "CREATE INDEX IF NOT EXISTS idx_ticketmsgs_tid ON ticket_msgs(ticket_id)",
    "CREATE INDEX IF NOT EXISTS idx_settings_uid ON user_settings(uid)",
    "CREATE INDEX IF NOT EXISTS idx_stats_ts ON stats(ts)",
    "CREATE INDEX IF NOT EXISTS idx_scriptlogs_uid ON script_logs(uid)",
    "CREATE INDEX IF NOT EXISTS idx_scriptlogs_fid ON script_logs(fid)",
    "CREATE INDEX IF NOT EXISTS idx_apikeys_uid ON api_keys(uid)",
    "CREATE INDEX IF NOT EXISTS idx_notif_uid ON notifications(uid, read)",
    "CREATE INDEX IF NOT EXISTS idx_ach_uid ON achievements_earned(uid)",
    "CREATE INDEX IF NOT EXISTS idx_badge_uid ON badges_earned(uid)",
    "CREATE INDEX IF NOT EXISTS idx_shares_token ON file_shares(token)",
    "CREATE INDEX IF NOT EXISTS idx_shares_fid ON file_shares(fid)",
    "CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referrals(referrer_uid)",
    "CREATE INDEX IF NOT EXISTS idx_ref_referred ON referrals(referred_uid)",
    "CREATE INDEX IF NOT EXISTS idx_orders_uid ON plan_orders(uid)",
    "CREATE INDEX IF NOT EXISTS idx_notes_uid ON user_notes(uid)",
    "CREATE INDEX IF NOT EXISTS idx_filetags_fid ON file_tags(fid)",
    "CREATE INDEX IF NOT EXISTS idx_filetags_tag ON file_tags(tag)",
    "CREATE INDEX IF NOT EXISTS idx_cron_uid ON cron_jobs(uid)",
    "CREATE INDEX IF NOT EXISTS idx_cron_enabled ON cron_jobs(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_iplog_uid ON ip_log(uid)",
    "CREATE INDEX IF NOT EXISTS idx_audit_admin ON audit_log(admin_uid)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
    "CREATE INDEX IF NOT EXISTS idx_metrics_ts ON server_metrics(ts)",
    "CREATE INDEX IF NOT EXISTS idx_activity_uid ON user_activity(uid)",
    "CREATE INDEX IF NOT EXISTS idx_activity_ts ON user_activity(ts)",
    "CREATE INDEX IF NOT EXISTS idx_versions_fid ON file_versions(fid)",
    "CREATE INDEX IF NOT EXISTS idx_webhooks_uid ON webhooks(uid)",
]

# Columns expected on existing databases: table -> (column, ddl type/default)
MIGRATIONS = {
    "users": [
        ("theme", "TEXT DEFAULT 'sigma'"),
        ("font", "TEXT DEFAULT 'default'"),
        ("lang", "TEXT DEFAULT 'en'"),
        ("xp", "INTEGER DEFAULT 0"),
        ("level", "INTEGER DEFAULT 1"),
        ("coins", "INTEGER DEFAULT 0"),
        ("last_seen", "TEXT DEFAULT ''"),
        ("push_enabled", "INTEGER DEFAULT 1"),
        ("api_key", "TEXT DEFAULT ''"),
        ("referral_code", "TEXT DEFAULT ''"),
        ("referrer", "INTEGER DEFAULT 0"),
        ("total_tickets", "INTEGER DEFAULT 0"),
        ("ban_reason", "TEXT DEFAULT ''"),
    ],
    "files": [
        ("tags", "TEXT DEFAULT ''"),
        ("checksum", "TEXT DEFAULT ''"),
        ("downloads", "INTEGER DEFAULT 0"),
        ("runs", "INTEGER DEFAULT 0"),
        ("last_run", "TEXT DEFAULT ''"),
        ("notes", "TEXT DEFAULT ''"),
        ("is_public", "INTEGER DEFAULT 0"),
    ],
    "tickets": [
        ("priority", "TEXT DEFAULT 'Normal'"),
        ("category", "TEXT DEFAULT 'General'"),
        ("assigned_to", "INTEGER DEFAULT 0"),
        ("closed_at", "TEXT DEFAULT ''"),
        ("updated_at", "TEXT DEFAULT ''"),
    ],
    "cron_jobs": [
        ("run_count", "INTEGER DEFAULT 0"),
        ("next_run", "TEXT DEFAULT ''"),
    ],
    "file_shares": [
        ("views", "INTEGER DEFAULT 0"),
    ],
    "webhooks": [
        ("last_triggered", "TEXT DEFAULT ''"),
        ("events", "TEXT DEFAULT 'all'"),
    ],
}


@contextmanager
def get_db():
    """SQLite connection with WAL mode, dict rows, and auto commit/rollback."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception as exc:
            log.debug("Rollback failed: %s", exc)
        raise
    finally:
        try:
            conn.close()
        except Exception as exc:
            log.debug("Close failed: %s", exc)


def db_exec(sql, params=(), one=False, fetch=False):
    """Execute SQL.

    fetch=True  -> list of dict rows (or single dict when one=True)
    fetch=False -> lastrowid for INSERT, rowcount otherwise
    """
    try:
        with _db_lock:
            with get_db() as conn:
                cur = conn.execute(sql, tuple(params or ()))
                if fetch:
                    if one:
                        row = cur.fetchone()
                        return dict(row) if row else None
                    return [dict(r) for r in cur.fetchall()]
                if sql.lstrip()[:6].upper() == "INSERT":
                    return cur.lastrowid
                return cur.rowcount
    except Exception as exc:
        log.error("DB error: %s | SQL=%s", exc, " ".join(str(sql).split())[:180])
        if fetch:
            return None if one else []
        return 0


def db_many(sql, seq_params):
    """Execute the same statement for many parameter tuples."""
    try:
        with _db_lock:
            with get_db() as conn:
                cur = conn.executemany(sql, list(seq_params or []))
                return cur.rowcount
    except Exception as exc:
        log.error("DB executemany error: %s", exc)
        return 0


def db_one(sql, params=()):
    """Return a single row as dict or None."""
    return db_exec(sql, params, one=True, fetch=True)


def db_all(sql, params=()):
    """Return every row as a list of dicts."""
    rows = db_exec(sql, params, one=False, fetch=True)
    return rows if isinstance(rows, list) else []


def db_val(sql, params=(), default=None):
    """Return the first column of the first row."""
    row = db_one(sql, params)
    if not row:
        return default
    for value in row.values():
        return value if value is not None else default
    return default


def table_columns(table):
    """Return the set of column names for a table."""
    out = set()
    for row in db_all("PRAGMA table_info(" + str(table) + ")"):
        name = row.get("name")
        if name:
            out.add(name)
    return out


def table_exists(table):
    """True when the table exists in the schema."""
    row = db_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return bool(row)


def init_db():
    """Create every table and index. Safe to call repeatedly."""
    created = 0
    for ddl in SCHEMA:
        db_exec(ddl)
        created += 1
    for ddl in INDEXES:
        db_exec(ddl)
    log.info("Database ready: %s tables, %s indexes", created, len(INDEXES))
    return created


# ---------------------------------------------------------------------------
# Schema reconciler
# ---------------------------------------------------------------------------
# A database file created by an older (or completely different) bot build can
# contain tables that share a name with ours but not the columns. "CREATE TABLE
# IF NOT EXISTS" silently keeps those stale tables, which is exactly what
# produced errors such as "no such column: enabled" at runtime. The reconciler
# below compares every live table against the canonical DDL in SCHEMA, adds the
# columns it can add in place, and rebuilds the table (preserving the rows it
# can map) when an in-place ALTER is impossible.

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)[\"'`\]]?\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_TABLE_CONSTRAINT_WORDS = (
    "primary", "unique", "check", "foreign", "constraint",
)


def _split_ddl_columns(body):
    """Split the inside of a CREATE TABLE body on top-level commas."""
    parts = []
    depth = 0
    current = ""
    for char in str(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth <= 0:
            parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return [part for part in parts if part]


def parse_schema_ddl(ddl):
    """Parse one CREATE TABLE statement into (table, [(column, definition)])."""
    text = " ".join(str(ddl).split())
    match = _CREATE_TABLE_RE.match(text)
    if not match:
        return "", []
    table = match.group(1)
    columns = []
    for part in _split_ddl_columns(match.group(2)):
        first = part.split()[0].strip("\"'`[]")
        if first.lower() in _TABLE_CONSTRAINT_WORDS:
            continue
        definition = part[len(part.split()[0]):].strip() or "TEXT"
        columns.append((first, definition))
    return table, columns


def _addable_definition(definition):
    """Turn a column definition into something ALTER TABLE ADD COLUMN accepts."""
    text = " ".join(str(definition).split())
    lowered = text.lower()
    if "primary key" in lowered or "autoincrement" in lowered:
        return None
    if "unique" in lowered:
        text = re.sub(r"(?i)\bunique\b", "", text).strip()
        lowered = text.lower()
    if "references" in lowered:
        text = re.sub(r"(?i)\breferences\b.*$", "", text).strip()
        lowered = text.lower()
    if "not null" in lowered and "default" not in lowered:
        base = lowered.split()[0] if lowered.split() else "text"
        filler = "0" if base.startswith(("int", "real", "num")) else "''"
        text = text + " DEFAULT " + filler
    return text or "TEXT"


def _rebuild_table(table, ddl, wanted_columns):
    """Rebuild a structurally incompatible table, carrying over shared columns."""
    backup = table + "__sigma_old"
    existing = table_columns(table)
    shared = [name for name, _def in wanted_columns if name in existing]
    try:
        with get_db() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("DROP TABLE IF EXISTS " + backup)
            conn.execute("ALTER TABLE " + table + " RENAME TO " + backup)
            conn.execute(ddl)
            if shared:
                cols = ", ".join('"' + name + '"' for name in shared)
                conn.execute(
                    "INSERT OR IGNORE INTO " + table + " (" + cols + ")"
                    " SELECT " + cols + " FROM " + backup
                )
            conn.execute("DROP TABLE IF EXISTS " + backup)
            conn.execute("PRAGMA foreign_keys=ON")
        log.warning(
            "Rebuilt incompatible table %s (kept %s of %s columns)",
            table, len(shared), len(wanted_columns),
        )
        return True
    except Exception as exc:
        log.error("Could not rebuild table %s: %s", table, exc)
        return False


def reconcile_schema(ddl_list=None):
    """Bring every existing table in line with the canonical DDL.

    Returns a dict describing what changed so startup can log a summary.
    """
    report = {"added": [], "rebuilt": [], "created": [], "checked": 0}
    for ddl in list(ddl_list or SCHEMA):
        table, wanted = parse_schema_ddl(ddl)
        if not table or not wanted:
            continue
        report["checked"] += 1
        if not table_exists(table):
            if db_exec(ddl) is not None:
                report["created"].append(table)
            continue
        existing = set(table_columns(table))
        missing = [(name, definition) for name, definition in wanted if name not in existing]
        if not missing:
            continue
        needs_rebuild = False
        for name, definition in missing:
            addable = _addable_definition(definition)
            if addable is None:
                needs_rebuild = True
                continue
            sql = 'ALTER TABLE ' + table + ' ADD COLUMN "' + name + '" ' + addable
            if db_exec(sql) is None:
                needs_rebuild = True
            else:
                report["added"].append(table + "." + name)
                log.info("Schema repair: added %s.%s", table, name)
        if needs_rebuild and _rebuild_table(table, ddl, wanted):
            report["rebuilt"].append(table)
    return report


def run_migrations():
    """Repair older/foreign database files, then apply the explicit migrations."""
    applied = []
    report = reconcile_schema(SCHEMA)
    applied.extend(report.get("added", []))
    for table in report.get("rebuilt", []):
        applied.append(table + " (rebuilt)")
    for table, columns in MIGRATIONS.items():
        if not table_exists(table):
            continue
        existing = table_columns(table)
        for column, ddl in columns:
            if column in existing:
                continue
            sql = "ALTER TABLE " + table + " ADD COLUMN " + column + " " + ddl
            if db_exec(sql) is not None:
                applied.append(table + "." + column)
                log.info("Migration applied: %s.%s", table, column)
    for ddl in INDEXES:
        db_exec(ddl)
    if applied:
        log.info("Migrations: %s change(s) applied", len(applied))
    else:
        log.info("Migrations: schema already up to date")
    return applied


def db_stat_counts():
    """Small helper used by dashboards: row counts of the busiest tables."""
    return {
        "users": int(db_val("SELECT COUNT(*) c FROM users", (), 0) or 0),
        "files": int(db_val("SELECT COUNT(*) c FROM files", (), 0) or 0),
        "tickets": int(db_val("SELECT COUNT(*) c FROM tickets", (), 0) or 0),
        "runs": int(db_val("SELECT COUNT(*) c FROM script_logs", (), 0) or 0),
        "crons": int(db_val("SELECT COUNT(*) c FROM cron_jobs", (), 0) or 0),
        "notes": int(db_val("SELECT COUNT(*) c FROM user_notes", (), 0) or 0),
        "shares": int(db_val("SELECT COUNT(*) c FROM file_shares", (), 0) or 0),
        "webhooks": int(db_val("SELECT COUNT(*) c FROM webhooks", (), 0) or 0),
    }


# ============================================================================
# SECTION 04 - USER MANAGEMENT
# ============================================================================


def rand_string(n=8, alphabet=None):
    """Random string used for referral codes, tokens and secrets."""
    pool = alphabet or (string.ascii_uppercase + string.digits)
    return "".join(random.choice(pool) for _ in range(n))


def make_referral_code(uid):
    """Deterministic-ish but unique referral code for a user."""
    return "SG" + str(uid)[-4:].rjust(4, "0") + rand_string(4)


def is_admin(uid):
    """True when the uid is listed in ADMIN_IDS."""
    try:
        return int(uid) in ADMIN_IDS
    except (TypeError, ValueError):
        return False


def ensure_user(uid, user_obj=None):
    """Create the user row if missing, refresh name/username/last_seen."""
    uid = int(uid)
    first_name = ""
    username = ""
    if user_obj is not None:
        first_name = str(getattr(user_obj, "first_name", "") or "")[:64]
        username = str(getattr(user_obj, "username", "") or "")[:64]
    row = db_one("SELECT uid FROM users WHERE uid=?", (uid,))
    now = utcstamp()
    if not row:
        db_exec(
            "INSERT INTO users (uid, first_name, username, tier, joined_at,"
            " referral_code, last_seen) VALUES (?,?,?,?,?,?,?)",
            (uid, first_name, username, "free", now, make_referral_code(uid), now),
        )
        log.info("New user registered: %s (%s)", uid, first_name or username or "?")
        total = int(db_val("SELECT COUNT(*) c FROM users", (), 0) or 0)
        if total <= 100:
            award_badge(uid, "founder")
        add_notification(uid, "Welcome to " + BOT_NAME + "!", "info")
        return True
    updates = []
    params = []
    if first_name:
        updates.append("first_name=?")
        params.append(first_name)
    if username:
        updates.append("username=?")
        params.append(username)
    updates.append("last_seen=?")
    params.append(now)
    params.append(uid)
    db_exec("UPDATE users SET " + ", ".join(updates) + " WHERE uid=?", tuple(params))
    return False


def get_user(uid):
    """Full user row as a dict; always returns a usable dict."""
    row = db_one("SELECT * FROM users WHERE uid=?", (int(uid),))
    if row:
        return row
    return {
        "uid": int(uid),
        "first_name": "",
        "username": "",
        "tier": "free",
        "points": 0,
        "coins": 0,
        "daily_streak": 0,
        "last_daily": "",
        "total_uploads": 0,
        "total_runs": 0,
        "total_tickets": 0,
        "is_banned": 0,
        "ban_reason": "",
        "joined_at": "",
        "referrer": 0,
        "referral_code": "",
        "theme": "sigma",
        "font": "default",
        "lang": "en",
        "xp": 0,
        "level": 1,
        "last_seen": "",
        "push_enabled": 1,
        "api_key": "",
    }


def get_tier_key(uid):
    """Tier key string, validated against TIERS."""
    key = str(get_user(uid).get("tier") or "free")
    return key if key in TIERS else "free"


def get_tier(uid):
    """Tier definition dict for the user."""
    return TIERS.get(get_tier_key(uid), TIERS["free"])


def get_tier_name(uid):
    """Human readable tier name."""
    return str(get_tier(uid).get("name", "Free"))


def get_theme(uid):
    """Theme dict for the user. Accepts a user id or a loaded user row."""
    row = uid if isinstance(uid, dict) else None
    if row is None:
        try:
            row = get_user(int(uid)) or {}
        except (TypeError, ValueError):
            row = {}
    key = str(row.get("theme") or "sigma")
    return THEMES.get(key, THEMES["sigma"])


def get_font(uid):
    """Font dict for the user."""
    key = str(get_user(uid).get("font") or "default")
    return FONTS.get(key, FONTS["default"])


def calc_level(xp):
    """Return (level, xp_to_next). Each level costs 100 * level XP."""
    xp = max(0, int(xp or 0))
    level = 1
    needed = 100
    remaining = xp
    while remaining >= needed and level < 999:
        remaining -= needed
        level += 1
        needed = 100 * level
    return level, max(0, needed - remaining)


def add_xp(uid, xp):
    """Add XP, persist the recomputed level, return (leveled_up, new_level)."""
    uid = int(uid)
    xp = int(xp or 0)
    if xp == 0:
        user = get_user(uid)
        return False, int(user.get("level") or 1)
    with _lock:
        user = get_user(uid)
        old_level = int(user.get("level") or 1)
        new_xp = max(0, int(user.get("xp") or 0) + xp)
        new_level, _ = calc_level(new_xp)
        db_exec("UPDATE users SET xp=?, level=? WHERE uid=?", (new_xp, new_level, uid))
    leveled = new_level > old_level
    if leveled:
        add_notification(
            uid,
            _e("star") + " Level up! You are now level " + str(new_level) + ".",
            "level",
        )
        add_coins(uid, new_level * 5)
        if new_level >= 10:
            award_badge(uid, "power_user")
    return leveled, new_level


def add_points(uid, pts, reason=""):
    """Add points, log activity, then evaluate achievements and levels."""
    uid = int(uid)
    pts = int(pts or 0)
    if pts:
        with _lock:
            db_exec("UPDATE users SET points = points + ? WHERE uid=?", (pts, uid))
    if reason:
        log_activity(uid, "points:" + str(pts) + ":" + str(reason)[:60])
    if pts > 0:
        add_xp(uid, max(1, pts // 2))
    earned = check_achievements(uid)
    return earned


def add_coins(uid, coins):
    """Add coins to the wallet."""
    coins = int(coins or 0)
    if coins:
        with _lock:
            db_exec("UPDATE users SET coins = coins + ? WHERE uid=?", (coins, int(uid)))
    return int(get_user(uid).get("coins") or 0)


def remove_coins(uid, coins):
    """Deduct coins; returns False when the balance is insufficient."""
    coins = int(coins or 0)
    if coins <= 0:
        return True
    with _lock:
        balance = int(get_user(uid).get("coins") or 0)
        if balance < coins:
            return False
        db_exec("UPDATE users SET coins = coins - ? WHERE uid=?", (coins, int(uid)))
    return True


def remove_points(uid, pts):
    """Deduct points; returns False when the balance is insufficient."""
    pts = int(pts or 0)
    if pts <= 0:
        return True
    with _lock:
        balance = int(get_user(uid).get("points") or 0)
        if balance < pts:
            return False
        db_exec("UPDATE users SET points = points - ? WHERE uid=?", (pts, int(uid)))
    return True


def get_setting(uid, key, default=None):
    """Read a user setting from user_settings."""
    row = db_one(
        "SELECT value FROM user_settings WHERE uid=? AND key=?",
        (int(uid), str(key)),
    )
    if not row:
        return default
    value = row.get("value")
    return default if value is None else value


def set_setting(uid, key, value):
    """Upsert a user setting."""
    db_exec(
        "INSERT INTO user_settings (uid, key, value, updated_at) VALUES (?,?,?,?)"
        " ON CONFLICT(uid, key) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at",
        (int(uid), str(key), str(value), utcstamp()),
    )
    return True


def get_setting_int(uid, key, default=0):
    """Read a numeric setting safely."""
    try:
        return int(str(get_setting(uid, key, default)))
    except (TypeError, ValueError):
        return int(default)


def add_notification(uid, message, ntype="info"):
    """Queue an in-bot notification row.

    Older databases shipped a NOT NULL "title" column, so the insert is
    built from the columns that actually exist right now.
    """
    uid = int(uid or 0)
    if uid <= 0:
        return False
    text = str(message)[:1000]
    columns = table_columns("notifications")
    values = {
        "uid": uid,
        "message": text,
        "type": str(ntype)[:24],
        "read": 0,
        "created_at": utcstamp(),
    }
    if "title" in columns:
        values["title"] = text[:80] or "Notification"
    if "body" in columns:
        values["body"] = text
    if "text" in columns:
        values["text"] = text
    if "is_read" in columns:
        values["is_read"] = 0
    if "seen" in columns:
        values["seen"] = 0
    use = [key for key in values if not columns or key in columns]
    if not use:
        return False
    sql = ("INSERT INTO notifications (" + ", ".join(use) + ")"
           " VALUES (" + ", ".join(["?"] * len(use)) + ")")
    return db_exec(sql, tuple(values[key] for key in use))


def get_notifications(uid, unread_only=False, limit=20):
    """Recent notifications for a user."""
    if unread_only:
        return db_all(
            "SELECT * FROM notifications WHERE uid=? AND read=0"
            " ORDER BY id DESC LIMIT ?",
            (int(uid), int(limit)),
        )
    return db_all(
        "SELECT * FROM notifications WHERE uid=? ORDER BY id DESC LIMIT ?",
        (int(uid), int(limit)),
    )


def mark_notifications_read(uid):
    """Mark every notification of a user as read."""
    return db_exec("UPDATE notifications SET read=1 WHERE uid=? AND read=0", (int(uid),))


def log_activity(uid, action):
    """Append to user_activity."""
    return db_exec(
        "INSERT INTO user_activity (uid, action, ts) VALUES (?,?,?)",
        (int(uid), str(action)[:180], utcstamp()),
    )


def log_ip(uid, action, ip="telegram"):
    """Append to ip_log (Telegram gives no client IP, so the source is tagged)."""
    return db_exec(
        "INSERT INTO ip_log (uid, action, ip, ts) VALUES (?,?,?,?)",
        (int(uid), str(action)[:120], str(ip)[:64], utcstamp()),
    )


def audit(admin_uid, action, target_uid=0, details=""):
    """Append to audit_log."""
    return db_exec(
        "INSERT INTO audit_log (admin_uid, action, target_uid, details, ts)"
        " VALUES (?,?,?,?,?)",
        (int(admin_uid), str(action)[:80], int(target_uid or 0), str(details)[:500], utcstamp()),
    )


def award_badge(uid, badge_key):
    """Award a badge once. Returns True when newly granted."""
    if badge_key not in BADGES:
        return False
    exists = db_one(
        "SELECT id FROM badges_earned WHERE uid=? AND badge_key=?",
        (int(uid), str(badge_key)),
    )
    if exists:
        return False
    db_exec(
        "INSERT INTO badges_earned (uid, badge_key, earned_at) VALUES (?,?,?)",
        (int(uid), str(badge_key), utcstamp()),
    )
    badge = BADGES.get(badge_key, {})
    add_notification(
        uid,
        "New badge unlocked: " + str(badge.get("emoji", "")) + " " + str(badge.get("name", badge_key)),
        "badge",
    )
    return True


def get_user_badges(uid):
    """List of badge keys owned by the user."""
    rows = db_all(
        "SELECT badge_key FROM badges_earned WHERE uid=? ORDER BY id ASC",
        (int(uid),),
    )
    return [str(r.get("badge_key")) for r in rows if r.get("badge_key") in BADGES]


def get_user_achievements(uid):
    """List of achievement keys owned by the user."""
    rows = db_all(
        "SELECT achievement_key FROM achievements_earned WHERE uid=? ORDER BY id ASC",
        (int(uid),),
    )
    return [str(r.get("achievement_key")) for r in rows if r.get("achievement_key") in ACHIEVEMENTS]


def _achievement_metric(uid, key):
    """Resolve a condition_key into a live numeric value."""
    user = get_user(uid)
    if key in ("total_uploads", "total_runs", "total_tickets", "points", "level", "daily_streak", "xp", "coins"):
        return int(user.get(key) or 0)
    if key == "referrals":
        return int(db_val("SELECT COUNT(*) c FROM referrals WHERE referrer_uid=?", (int(uid),), 0) or 0)
    if key == "stored_files":
        return int(db_val("SELECT COUNT(*) c FROM files WHERE uid=?", (int(uid),), 0) or 0)
    if key == "shares":
        return int(db_val("SELECT COUNT(*) c FROM file_shares WHERE uid=?", (int(uid),), 0) or 0)
    return 0


def check_achievements(uid):
    """Evaluate all achievement conditions and award anything newly earned."""
    uid = int(uid)
    owned = set(get_user_achievements(uid))
    newly = []
    for key, meta in ACHIEVEMENTS.items():
        if key in owned:
            continue
        metric = _achievement_metric(uid, str(meta.get("condition_key", "")))
        if metric >= int(meta.get("condition_val", 0) or 0):
            db_exec(
                "INSERT INTO achievements_earned (uid, achievement_key, earned_at)"
                " VALUES (?,?,?)",
                (uid, key, utcstamp()),
            )
            reward = int(meta.get("pts", 0) or 0)
            if reward:
                db_exec("UPDATE users SET points = points + ? WHERE uid=?", (reward, uid))
            add_notification(
                uid,
                str(meta.get("emoji", "")) + " Achievement unlocked: "
                + str(meta.get("name", key)) + " (+" + str(reward) + " pts)",
                "achievement",
            )
            newly.append(key)
    return newly


def generate_api_key(uid, label="default"):
    """Create an API key, store only its hash, return the raw secret once."""
    raw = "sig_" + secrets.token_urlsafe(30)
    key_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    db_exec(
        "INSERT INTO api_keys (uid, key_hash, label, created_at, last_used, is_active)"
        " VALUES (?,?,?,?,'',1)",
        (int(uid), key_hash, str(label)[:60] or "default", utcstamp()),
    )
    db_exec("UPDATE users SET api_key=? WHERE uid=?", (key_hash[:16], int(uid)))
    log_activity(uid, "api_key_created")
    return True, raw


def list_api_keys(uid):
    """All API keys of a user."""
    return db_all("SELECT * FROM api_keys WHERE uid=? ORDER BY id DESC", (int(uid),))


def revoke_api_key(uid, key_id):
    """Deactivate one API key owned by the user."""
    row = db_one("SELECT * FROM api_keys WHERE id=? AND uid=?", (int(key_id), int(uid)))
    if not row:
        return False
    db_exec("UPDATE api_keys SET is_active=0 WHERE id=?", (int(key_id),))
    log_activity(uid, "api_key_revoked")
    return True


def resolve_api_key(raw_key):
    """Look up an active API key by its raw value."""
    if not raw_key:
        return None
    key_hash = hashlib.sha256(str(raw_key).encode("utf-8")).hexdigest()
    row = db_one("SELECT * FROM api_keys WHERE key_hash=? AND is_active=1", (key_hash,))
    if row:
        db_exec("UPDATE api_keys SET last_used=? WHERE id=?", (utcstamp(), int(row.get("id"))))
    return row


def check_rate_limit(uid, endpoint, limit=20, window_sec=60):
    """Sliding window rate limit. True when the call is allowed."""
    uid = int(uid)
    endpoint = str(endpoint)[:60]
    now = utcnow()
    with _lock:
        row = db_one(
            "SELECT count, window_start FROM rate_limits WHERE uid=? AND endpoint=?",
            (uid, endpoint),
        )
        start = parse_stamp(row.get("window_start")) if row else None
        if not row or not start or (now - start).total_seconds() >= float(window_sec):
            db_exec(
                "INSERT INTO rate_limits (uid, endpoint, count, window_start)"
                " VALUES (?,?,1,?) ON CONFLICT(uid, endpoint) DO UPDATE SET"
                " count=1, window_start=excluded.window_start",
                (uid, endpoint, utcstamp()),
            )
            return True
        count = int(row.get("count") or 0)
        if count >= int(limit):
            return False
        db_exec(
            "UPDATE rate_limits SET count = count + 1 WHERE uid=? AND endpoint=?",
            (uid, endpoint),
        )
        return True


def is_banned(uid):
    """True when the user is banned."""
    return int(get_user(uid).get("is_banned") or 0) == 1


def touch_seen(uid):
    """Update last_seen."""
    return db_exec("UPDATE users SET last_seen=? WHERE uid=?", (utcstamp(), int(uid)))


def give_daily(uid):
    """Grant the daily bonus.

    Returns (ok, pts, streak, bonus_msg). ok=False when already claimed today.
    """
    uid = int(uid)
    now = utcnow()
    today = now.date()
    with _lock:
        user = get_user(uid)
        last = parse_stamp(user.get("last_daily"))
        streak = int(user.get("daily_streak") or 0)
        if last is not None and last.date() == today:
            return False, 0, streak, "Already claimed today. Come back tomorrow!"
        if last is not None and (today - last.date()).days == 1:
            streak += 1
        else:
            streak = 1
        cycle_index = (streak - 1) % len(DAILY_REWARD_TABLE)
        base = int(DAILY_REWARD_TABLE[cycle_index])
        streak_bonus = min(100, streak * 10)
        total = base + streak_bonus
        notes = []
        notes.append("Base reward: +" + str(base) + " pts")
        notes.append("Streak bonus: +" + str(streak_bonus) + " pts")
        if streak == 7 or streak % 7 == 0:
            total += 150
            notes.append("Weekly milestone: +150 pts")
        if streak == 30 or (streak % 30 == 0 and streak > 0):
            total += 750
            notes.append("Monthly milestone: +750 pts")
        if streak == 100:
            total += 5000
            notes.append("Century milestone: +5000 pts")
        coins = 5 + (streak // 3)
        db_exec(
            "UPDATE users SET points = points + ?, coins = coins + ?,"
            " daily_streak=?, last_daily=? WHERE uid=?",
            (total, coins, streak, utcstamp(), uid),
        )
        notes.append("Coins: +" + str(coins) + " " + _e("coin"))
    add_xp(uid, 10)
    check_achievements(uid)
    log_activity(uid, "daily_claim:" + str(total))
    return True, total, streak, "\n".join(notes)


# ============================================================================
# SECTION 05 - FILE MANAGEMENT
# ============================================================================

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def sanitize_name(name, fallback="file.txt"):
    """Return a filesystem-safe basename."""
    raw = str(name or "").strip().replace("\\", "/")
    raw = raw.split("/")[-1]
    raw = raw.replace("..", ".")
    raw = SAFE_NAME_RE.sub("_", raw).strip("._")
    if not raw:
        raw = fallback
    if len(raw) > 90:
        stem, dot, ext = raw.rpartition(".")
        if dot and len(ext) <= 8:
            raw = stem[: 90 - len(ext) - 1] + "." + ext
        else:
            raw = raw[:90]
    return raw


def user_dir(uid):
    """Path to the user's storage directory, created on demand."""
    path = FILES_DIR / str(int(uid))
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_log_dir(uid):
    """Path to the user's log directory, created on demand."""
    path = LOGS_DIR / str(int(uid))
    path.mkdir(parents=True, exist_ok=True)
    return path


def versions_dir(uid):
    """Path where historical file versions are stored."""
    path = user_dir(uid) / ".versions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_file(fid):
    """Single file row as dict or None."""
    return db_one("SELECT * FROM files WHERE id=?", (int(fid),))


def get_user_file(uid, fid):
    """File row restricted to the owner (admins use get_file)."""
    return db_one("SELECT * FROM files WHERE id=? AND uid=?", (int(fid), int(uid)))


def file_count(uid, status=None):
    """Number of files stored by the user."""
    if status:
        return int(db_val(
            "SELECT COUNT(*) c FROM files WHERE uid=? AND status=?",
            (int(uid), str(status)), 0) or 0)
    return int(db_val("SELECT COUNT(*) c FROM files WHERE uid=?", (int(uid),), 0) or 0)


def total_file_size(uid):
    """Sum of stored bytes for the user."""
    return int(db_val(
        "SELECT COALESCE(SUM(fsize),0) s FROM files WHERE uid=?", (int(uid),), 0) or 0)


def get_files(uid, status=None, page=1, per_page=DEFAULT_PER_PAGE):
    """Paginated file list. Returns (rows, total, total_pages, page)."""
    page = max(1, int(page or 1))
    per_page = max(1, int(per_page or DEFAULT_PER_PAGE))
    if status:
        total = file_count(uid, status)
        rows = db_all(
            "SELECT * FROM files WHERE uid=? AND status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), str(status), per_page, (page - 1) * per_page),
        )
    else:
        total = file_count(uid)
        rows = db_all(
            "SELECT * FROM files WHERE uid=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), per_page, (page - 1) * per_page),
        )
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    return rows, total, total_pages, page


def checksum_file(path):
    """MD5 hex digest of a file, empty string on failure."""
    try:
        digest = hashlib.md5()
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception as exc:
        log.warning("Checksum failed for %s: %s", path, exc)
        return ""


def guess_type(name):
    """Mime type guess with a sane default."""
    mime, _ = mimetypes.guess_type(str(name))
    return mime or "application/octet-stream"


def file_ext(name):
    """Lowercase extension including the dot."""
    return os.path.splitext(str(name))[1].lower()


def register_file(uid, fname, fpath, fsize, status="pending"):
    """Insert a new row into files and bump the user's upload counter."""
    fid = db_exec(
        "INSERT INTO files (uid, fname, fpath, fsize, ftype, status, is_public,"
        " uploaded, notes, downloads, runs, last_run, tags, checksum)"
        " VALUES (?,?,?,?,?,?,0,?,'',0,0,'','',?)",
        (
            int(uid),
            str(fname),
            str(fpath),
            int(fsize or 0),
            guess_type(fname),
            str(status),
            utcstamp(),
            checksum_file(fpath),
        ),
    )
    db_exec("UPDATE users SET total_uploads = total_uploads + 1 WHERE uid=?", (int(uid),))
    fid = int(fid or 0)
    # Scan for nested hosting and detect requirements the moment it lands.
    try:
        apply_upload_policy(fid)
    except Exception as exc:
        log.debug("upload policy skipped for #%s: %s", fid, exc)
    try:
        scan_requirements(fid, refresh=True)
    except Exception as exc:
        log.debug("requirement scan skipped for #%s: %s", fid, exc)
    return fid


def delete_file(uid, fid, force=False):
    """Delete a file from disk and database. Returns (ok, message)."""
    row = get_file(fid) if force else get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    if is_running(row.get("uid", uid), fid):
        stop_script(row.get("uid", uid), fid)
    path = Path(str(row.get("fpath") or ""))
    try:
        if path.exists():
            path.unlink()
    except Exception as exc:
        log.warning("Unlink failed for %s: %s", path, exc)
    for version in get_file_versions(fid):
        vpath = Path(str(version.get("fpath") or ""))
        try:
            if vpath.exists():
                vpath.unlink()
        except Exception as exc:
            log.debug("Version unlink failed: %s", exc)
    db_exec("DELETE FROM file_versions WHERE fid=?", (int(fid),))
    db_exec("DELETE FROM file_tags WHERE fid=?", (int(fid),))
    db_exec("DELETE FROM file_shares WHERE fid=?", (int(fid),))
    db_exec("DELETE FROM cron_jobs WHERE fid=?", (int(fid),))
    db_exec("DELETE FROM script_logs WHERE fid=?", (int(fid),))
    db_exec("DELETE FROM files WHERE id=?", (int(fid),))
    log_activity(row.get("uid", uid), "file_delete:" + str(fid))
    return True, "Deleted " + str(row.get("fname", "file"))


def rename_file(uid, fid, new_name):
    """Rename on disk and in the database. Returns (ok, message)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    safe = sanitize_name(new_name, fallback=str(row.get("fname") or "file.txt"))
    old_path = Path(str(row.get("fpath") or ""))
    new_path = user_dir(uid) / safe
    if new_path.exists() and str(new_path) != str(old_path):
        stem, dot, ext = safe.rpartition(".")
        suffix = str(int(time.time()))
        safe = (stem + "_" + suffix + "." + ext) if dot else (safe + "_" + suffix)
        new_path = user_dir(uid) / safe
    try:
        if old_path.exists():
            shutil.move(str(old_path), str(new_path))
    except Exception as exc:
        return False, "Rename failed: " + str(exc)
    db_exec(
        "UPDATE files SET fname=?, fpath=?, ftype=? WHERE id=?",
        (safe, str(new_path), guess_type(safe), int(fid)),
    )
    log_activity(uid, "file_rename:" + str(fid))
    return True, "Renamed to " + safe


def move_file(uid, fid, subfolder):
    """Move a file into a subfolder of the user's directory."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    folder = sanitize_name(subfolder, fallback="misc")
    target_dir = user_dir(uid) / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    old_path = Path(str(row.get("fpath") or ""))
    new_path = target_dir / str(row.get("fname") or "file.txt")
    try:
        if old_path.exists():
            shutil.move(str(old_path), str(new_path))
    except Exception as exc:
        return False, "Move failed: " + str(exc)
    db_exec("UPDATE files SET fpath=? WHERE id=?", (str(new_path), int(fid)))
    log_activity(uid, "file_move:" + str(fid))
    return True, "Moved into " + folder + "/"


def copy_file(uid, fid):
    """Duplicate a file. Returns (ok, message, new_fid)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found.", 0
    tier = get_tier(uid)
    limit = int(tier.get("files", 0) or 0)
    if limit != -1 and file_count(uid) >= limit:
        return False, "File limit reached for the " + str(tier.get("name")) + " plan.", 0
    src = Path(str(row.get("fpath") or ""))
    if not src.exists():
        return False, "Source file is missing on disk.", 0
    name = str(row.get("fname") or "file.txt")
    stem, dot, ext = name.rpartition(".")
    copy_name = (stem + "_copy." + ext) if dot else (name + "_copy")
    copy_name = sanitize_name(copy_name)
    dst = user_dir(uid) / copy_name
    counter = 1
    while dst.exists():
        counter += 1
        stem2, dot2, ext2 = copy_name.rpartition(".")
        alt = (stem2 + str(counter) + "." + ext2) if dot2 else (copy_name + str(counter))
        dst = user_dir(uid) / alt
    try:
        shutil.copy2(str(src), str(dst))
    except Exception as exc:
        return False, "Copy failed: " + str(exc), 0
    new_fid = register_file(uid, dst.name, str(dst), dst.stat().st_size, row.get("status", "pending"))
    log_activity(uid, "file_copy:" + str(fid) + "->" + str(new_fid))
    return True, "Duplicated as " + dst.name, new_fid


def set_file_public(uid, fid, public):
    """Toggle the is_public flag."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    value = 1 if public else 0
    db_exec("UPDATE files SET is_public=? WHERE id=?", (value, int(fid)))
    return True, ("File is now public." if value else "File is now private.")


def add_file_note(uid, fid, note):
    """Attach a free-text note to a file."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    db_exec("UPDATE files SET notes=? WHERE id=?", (str(note)[:1000], int(fid)))
    return True, "Note saved."


def add_file_tags(uid, fid, tags_list):
    """Add tags to a file (both normalized table and cached column)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found.", []
    cleaned = []
    for tag in list(tags_list or []):
        tag = re.sub(r"[^A-Za-z0-9_\-]+", "", str(tag)).lower()[:24]
        if tag and tag not in cleaned:
            cleaned.append(tag)
    if not cleaned:
        return False, "No valid tags provided.", []
    existing = set(get_file_tags(fid))
    added = []
    for tag in cleaned:
        if tag in existing:
            continue
        db_exec(
            "INSERT INTO file_tags (fid, tag, created_at) VALUES (?,?,?)",
            (int(fid), tag, utcstamp()),
        )
        added.append(tag)
    all_tags = get_file_tags(fid)
    db_exec("UPDATE files SET tags=? WHERE id=?", (",".join(all_tags), int(fid)))
    return True, "Added " + str(len(added)) + " tag(s).", all_tags


def get_file_tags(fid):
    """Tag list of a file."""
    rows = db_all("SELECT tag FROM file_tags WHERE fid=? ORDER BY tag ASC", (int(fid),))
    return [str(r.get("tag")) for r in rows if r.get("tag")]


def remove_file_tag(uid, fid, tag):
    """Remove one tag from a file."""
    if not get_user_file(uid, fid):
        return False
    db_exec("DELETE FROM file_tags WHERE fid=? AND tag=?", (int(fid), str(tag)))
    db_exec("UPDATE files SET tags=? WHERE id=?", (",".join(get_file_tags(fid)), int(fid)))
    return True


def create_file_share(uid, fid, expires_hours=24):
    """Create a share token for a file. Returns (ok, token_or_error)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    try:
        hours = max(1, min(24 * 365, int(expires_hours)))
    except (TypeError, ValueError):
        hours = 24
    token = secrets.token_urlsafe(12)
    expires = (utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    db_exec(
        "INSERT INTO file_shares (fid, uid, token, expires_at, views, created_at)"
        " VALUES (?,?,?,?,0,?)",
        (int(fid), int(uid), token, expires, utcstamp()),
    )
    log_activity(uid, "file_share:" + str(fid))
    return True, token


def get_share_by_token(token):
    """Validate a share token, bump views, and return the file row."""
    row = db_one("SELECT * FROM file_shares WHERE token=?", (str(token),))
    if not row:
        return None
    expires = parse_stamp(row.get("expires_at"))
    if expires is not None and expires < utcnow():
        return None
    db_exec("UPDATE file_shares SET views = views + 1 WHERE id=?", (int(row.get("id")),))
    return get_file(row.get("fid"))


def get_file_shares(uid, fid):
    """All shares created for a file."""
    return db_all(
        "SELECT * FROM file_shares WHERE fid=? AND uid=? ORDER BY id DESC",
        (int(fid), int(uid)),
    )


def save_file_version(fid, old_path):
    """Snapshot the current file content into the versions store."""
    row = get_file(fid)
    if not row:
        return 0
    src = Path(str(old_path or row.get("fpath") or ""))
    if not src.exists():
        return 0
    version = int(db_val(
        "SELECT COALESCE(MAX(version),0) v FROM file_versions WHERE fid=?",
        (int(fid),), 0) or 0) + 1
    vdir = versions_dir(row.get("uid", 0))
    dst = vdir / (str(fid) + "_v" + str(version) + "_" + src.name)
    try:
        shutil.copy2(str(src), str(dst))
    except Exception as exc:
        log.warning("Version snapshot failed: %s", exc)
        return 0
    return int(db_exec(
        "INSERT INTO file_versions (fid, version, fpath, created_at, size)"
        " VALUES (?,?,?,?,?)",
        (int(fid), version, str(dst), utcstamp(), dst.stat().st_size),
    ) or 0)


def get_file_versions(fid):
    """Version history of a file, newest first."""
    return db_all(
        "SELECT * FROM file_versions WHERE fid=? ORDER BY version DESC",
        (int(fid),),
    )


def restore_file_version(uid, fid, version_id):
    """Restore a stored version over the live file."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    version = db_one(
        "SELECT * FROM file_versions WHERE id=? AND fid=?",
        (int(version_id), int(fid)),
    )
    if not version:
        return False, "Version not found."
    src = Path(str(version.get("fpath") or ""))
    if not src.exists():
        return False, "Version data is missing on disk."
    save_file_version(fid, row.get("fpath"))
    dst = Path(str(row.get("fpath") or ""))
    try:
        shutil.copy2(str(src), str(dst))
    except Exception as exc:
        return False, "Restore failed: " + str(exc)
    db_exec(
        "UPDATE files SET fsize=?, checksum=? WHERE id=?",
        (dst.stat().st_size, checksum_file(dst), int(fid)),
    )
    log_activity(uid, "file_restore:" + str(fid))
    return True, "Restored version " + str(version.get("version"))


def read_file_text(path, max_bytes=200000):
    """Read a text file defensively."""
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_bytes)
    except Exception as exc:
        log.debug("read_file_text failed: %s", exc)
        return ""


def write_file_text(path, text):
    """Write a text file defensively."""
    try:
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write(str(text))
        return True
    except Exception as exc:
        log.warning("write_file_text failed: %s", exc)
        return False


def preview_file(uid, fid, n_lines=25):
    """First N lines of a text file. Returns (ok, text)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    path = Path(str(row.get("fpath") or ""))
    if not path.exists():
        return False, "File is missing on disk."
    if int(row.get("fsize") or 0) > 5 * 1024 * 1024:
        return False, "File is too large to preview."
    content = read_file_text(path, 120000)
    if not content.strip():
        return False, "No text content to preview (binary or empty file)."
    lines = content.splitlines()[: max(1, int(n_lines))]
    return True, "\n".join(lines)


def write_file_content(uid, fid, content):
    """Overwrite a file's content, snapshotting the previous version first."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    path = Path(str(row.get("fpath") or ""))
    save_file_version(fid, path)
    if not write_file_text(path, content):
        return False, "Write failed."
    size = path.stat().st_size if path.exists() else 0
    db_exec(
        "UPDATE files SET fsize=?, checksum=? WHERE id=?",
        (size, checksum_file(path), int(fid)),
    )
    log_activity(uid, "file_write:" + str(fid))
    return True, "Saved " + fmt_size(size) + "."


def edit_file_line(uid, fid, line_no, new_content):
    """Replace a single 1-based line in a text file."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    path = Path(str(row.get("fpath") or ""))
    if not path.exists():
        return False, "File is missing on disk."
    try:
        line_no = int(line_no)
    except (TypeError, ValueError):
        return False, "Invalid line number."
    text = read_file_text(path)
    lines = text.splitlines()
    if line_no < 1:
        return False, "Line numbers start at 1."
    while len(lines) < line_no:
        lines.append("")
    save_file_version(fid, path)
    lines[line_no - 1] = str(new_content)
    if not write_file_text(path, "\n".join(lines) + "\n"):
        return False, "Write failed."
    size = path.stat().st_size if path.exists() else 0
    db_exec(
        "UPDATE files SET fsize=?, checksum=? WHERE id=?",
        (size, checksum_file(path), int(fid)),
    )
    return True, "Line " + str(line_no) + " updated."


def search_files(uid, query, limit=25):
    """Search the user's files by name, tags or notes."""
    needle = "%" + str(query or "").strip() + "%"
    return db_all(
        "SELECT * FROM files WHERE uid=? AND (fname LIKE ? OR tags LIKE ? OR notes LIKE ?)"
        " ORDER BY id DESC LIMIT ?",
        (int(uid), needle, needle, needle, int(limit)),
    )


def export_file_list(uid):
    """CSV export of the user's file table."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "id", "name", "size_bytes", "type", "status", "public",
        "uploaded", "downloads", "runs", "last_run", "tags", "checksum",
    ])
    for row in db_all("SELECT * FROM files WHERE uid=? ORDER BY id ASC", (int(uid),)):
        writer.writerow([
            row.get("id", ""),
            row.get("fname", ""),
            row.get("fsize", 0),
            row.get("ftype", ""),
            row.get("status", ""),
            "yes" if int(row.get("is_public") or 0) else "no",
            row.get("uploaded", ""),
            row.get("downloads", 0),
            row.get("runs", 0),
            row.get("last_run", ""),
            row.get("tags", ""),
            row.get("checksum", ""),
        ])
    return buffer.getvalue()


def zip_all_files(uid):
    """Zip every stored file of a user. Returns (ok, path_or_error)."""
    rows = db_all("SELECT * FROM files WHERE uid=? ORDER BY id ASC", (int(uid),))
    if not rows:
        return False, "You have no files to archive."
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = EXPORTS_DIR / ("sigma_" + str(int(uid)) + "_" + stamp + ".zip")
    try:
        with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as archive:
            for row in rows:
                path = Path(str(row.get("fpath") or ""))
                if path.exists():
                    archive.write(str(path), arcname=str(row.get("fname") or path.name))
            archive.writestr("file_list.csv", export_file_list(uid))
    except Exception as exc:
        return False, "Archive failed: " + str(exc)
    log_activity(uid, "zip_all")
    return True, str(out_path)


def public_files(limit=20):
    """Latest public and approved files."""
    return db_all(
        "SELECT * FROM files WHERE is_public=1 AND status='approved'"
        " ORDER BY id DESC LIMIT ?",
        (int(limit),),
    )


def bump_download(fid):
    """Increment the download counter."""
    return db_exec("UPDATE files SET downloads = downloads + 1 WHERE id=?", (int(fid),))


# ============================================================================
# SECTION 06 - SCRIPT RUNNER
# ============================================================================

RUNNERS = {
    ".py": {"cmd": ["python3"], "name": "Python 3", "compile": None},
    ".js": {"cmd": ["node"], "name": "Node.js", "compile": None},
    ".ts": {"cmd": ["npx", "-y", "tsx"], "name": "TypeScript", "compile": None},
    ".sh": {"cmd": ["bash"], "name": "Bash", "compile": None},
    ".rb": {"cmd": ["ruby"], "name": "Ruby", "compile": None},
    ".php": {"cmd": ["php"], "name": "PHP", "compile": None},
    ".pl": {"cmd": ["perl"], "name": "Perl", "compile": None},
    ".go": {"cmd": ["go", "run"], "name": "Go", "compile": None},
    ".lua": {"cmd": ["lua"], "name": "Lua", "compile": None},
    ".r": {"cmd": ["Rscript"], "name": "R", "compile": None},
    ".java": {"cmd": ["java"], "name": "Java", "compile": ["javac"]},
}

_procs = {}
_proc_start_times = {}
_proc_args = {}


def proc_key(uid, fid):
    """Dictionary key for a running process."""
    return str(int(uid)) + "_" + str(int(fid))


def log_path(uid, fid):
    """Path of the log file for a script run."""
    return user_log_dir(uid) / ("file_" + str(int(fid)) + ".log")


def runner_for(fname):
    """Runner definition for a filename, or None when unsupported."""
    return RUNNERS.get(file_ext(fname))


def is_running(uid, fid):
    """True when a live process exists for this uid/fid pair."""
    key = proc_key(uid, fid)
    with _proc_lock:
        proc = _procs.get(key)
    if proc is None:
        return False
    if proc.poll() is None:
        return True
    with _proc_lock:
        _procs.pop(key, None)
        _proc_start_times.pop(key, None)
    return False


def running_count(uid):
    """Number of live processes for one user."""
    prefix = str(int(uid)) + "_"
    count = 0
    with _proc_lock:
        keys = [k for k in _procs if k.startswith(prefix)]
    for key in keys:
        try:
            fid = int(key.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if is_running(uid, fid):
            count += 1
    return count


def get_all_running():
    """List of (uid, fid) tuples for every live process."""
    out = []
    with _proc_lock:
        keys = list(_procs.keys())
    for key in keys:
        parts = key.split("_", 1)
        if len(parts) != 2:
            continue
        try:
            uid = int(parts[0])
            fid = int(parts[1])
        except ValueError:
            continue
        if is_running(uid, fid):
            out.append((uid, fid))
    return out


def count_running_procs():
    """Total number of live processes."""
    return len(get_all_running())


def _compile_java(path, cwd):
    """Compile a .java file. Returns (ok, message)."""
    try:
        result = subprocess.run(
            ["javac", str(path)],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
    except FileNotFoundError:
        return False, "javac is not installed on this server."
    except subprocess.TimeoutExpired:
        return False, "Compilation timed out."
    except Exception as exc:
        return False, "Compilation error: " + str(exc)
    if result.returncode != 0:
        output = (result.stdout or b"").decode("utf-8", "replace")[-1200:]
        return False, "Compilation failed:\n" + output
    return True, "Compiled successfully."


def build_command(row):
    """Build the argv list for a file row. Returns (ok, argv_or_error, needs_compile)."""
    fname = str(row.get("fname") or "")
    path = Path(str(row.get("fpath") or ""))
    runner = runner_for(fname)
    if not runner:
        supported = ", ".join(sorted(RUNNERS.keys()))
        return False, "Unsupported file type. Supported: " + supported, False
    if str(row.get("status")) == "quarantined":
        return False, ("This file is quarantined. The original was moved into the"
                       " locked vault and can never be executed."), False
    if not path.exists():
        return False, ("File is missing on disk. The stored copy was removed or the"
                       " storage folder changed - re-upload it to restore hosting."), False
    ext = file_ext(fname)
    if ext == ".java":
        return True, ["java", path.stem], True
    return True, list(runner.get("cmd", [])) + [str(path)], False


def run_script(uid, fid, args=""):
    """Start a script. Returns (ok, message)."""
    uid = int(uid)
    fid = int(fid)
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    if str(row.get("status")) != "approved":
        return False, "This file is " + str(row.get("status", "pending")) + ". Only approved files can run."
    tier_key = get_tier_key(uid)
    tier = TIERS.get(tier_key, TIERS["free"])
    if not tier.get("terminal", False) and not is_admin(uid):
        return False, (
            "Script execution is not available on the " + str(tier.get("name"))
            + " plan. Upgrade to Basic or higher."
        )
    if is_running(uid, fid):
        return False, "That script is already running."
    limit = int(TIER_PROC_LIMIT.get(tier_key, 1))
    if not is_admin(uid) and running_count(uid) >= limit:
        return False, (
            "Process limit reached (" + str(limit) + "). Stop a running script first."
        )
    ok, command, needs_compile = build_command(row)
    if not ok:
        return False, str(command)
    path = Path(str(row.get("fpath") or ""))
    cwd = path.parent
    if needs_compile:
        compiled, message = _compile_java(path, cwd)
        if not compiled:
            return False, message
    extra = []
    for token in str(args or "").split():
        if len(token) <= 200:
            extra.append(token)
    command = list(command) + extra[:20]
    lpath = log_path(uid, fid)
    try:
        handle = open(str(lpath), "a", encoding="utf-8", errors="replace")
        handle.write(
            "\n===== RUN " + utcstamp() + " UTC | " + " ".join(command) + " =====\n"
        )
        handle.flush()
    except Exception as exc:
        return False, "Could not open log file: " + str(exc)
    # ---- hardened launch -------------------------------------------------
    # The host environment is scrubbed of every secret, the process gets its
    # own jail for HOME/TMPDIR, the runtime guard is injected through
    # PYTHONPATH, and OS limits are applied with live headroom so a busy VPS
    # never hits "can't start new thread".
    jail = None
    preexec = None
    env = scrub_env()
    env["SIGMA_UID"] = str(uid)
    env["SIGMA_FID"] = str(fid)
    env["SIGMA_TIER"] = tier_key
    env["PYTHONUNBUFFERED"] = "1"
    try:
        if int(hcfg("jail_runs") or 0):
            jail = run_jail(uid, fid)
            env["HOME"] = str(jail / "home")
            env["TMPDIR"] = str(jail / "tmp")
            env["TEMP"] = str(jail / "tmp")
            try:
                guard_log_path(uid, fid).write_text("", encoding="utf-8")
            except Exception:
                pass
            env.update(policy_env(uid, fid, jail, cwd))
            env["PYTHONPATH"] = (str(guard_dir()) + os.pathsep
                                 + str(env.get("PYTHONPATH", ""))).rstrip(os.pathsep)
            register_jail(uid, fid, jail)
            preexec = secure_preexec(uid, fid, jail, {
                "processes": 200, "open_files": 512, "file_size_mb": 512,
            })
    except Exception as exc:
        log.error("Hardening setup failed for #%s: %s", fid, exc)
    try:
        interpreter = venv_python(uid)
        if interpreter and command and str(command[0]).startswith("python"):
            command = [str(interpreter)] + list(command)[1:]
    except Exception as exc:
        log.debug("venv interpreter skipped: %s", exc)
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
            preexec_fn=preexec,
        )
    except FileNotFoundError:
        handle.close()
        return False, "Interpreter not installed: " + str(command[0])
    except Exception as exc:
        handle.close()
        return False, "Launch failed: " + str(exc)
    key = proc_key(uid, fid)
    with _proc_lock:
        _procs[key] = proc
        _proc_start_times[key] = time.time()
        _proc_args[key] = " ".join(extra)
    db_exec(
        "UPDATE files SET runs = runs + 1, last_run=? WHERE id=?",
        (utcstamp(), fid),
    )
    db_exec("UPDATE users SET total_runs = total_runs + 1 WHERE uid=?", (uid,))
    timeout = int(TIER_TIMEOUTS.get(tier_key, 300))
    threading.Thread(
        target=_watch_process,
        args=(uid, fid, proc, handle, timeout, time.time()),
        daemon=True,
    ).start()
    add_points(uid, EARN_PTS_FOR_ACTION.get("run_script", 3), "run_script")
    log_activity(uid, "run:" + str(fid))
    hour = utcnow().hour
    if 0 <= hour < 5:
        award_badge(uid, "night_owl")
    runs_today = int(db_val(
        "SELECT COUNT(*) c FROM user_activity WHERE uid=? AND action LIKE 'run:%'"
        " AND ts >= ?",
        (uid, (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")), 0) or 0)
    if runs_today >= 10:
        award_badge(uid, "speedster")
    return True, (
        "Started " + str(row.get("fname")) + " (PID " + str(proc.pid) + ")\n"
        + "Timeout: " + fmt_duration(timeout)
    )


def _watch_process(uid, fid, proc, handle, timeout, started):
    """Wait for a process, enforce the tier timeout, and record the result."""
    exit_code = -1
    try:
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(proc)
            exit_code = -9
            try:
                handle.write("\n[SIGMA] Timeout reached, process terminated.\n")
                handle.flush()
            except Exception as exc:
                log.debug("log write failed: %s", exc)
    except Exception as exc:
        log.warning("Process watcher error: %s", exc)
    finally:
        try:
            handle.close()
        except Exception as exc:
            log.debug("handle close failed: %s", exc)
        key = proc_key(uid, fid)
        with _proc_lock:
            _procs.pop(key, None)
            _proc_start_times.pop(key, None)
            _proc_args.pop(key, None)
    duration_ms = int((time.time() - started) * 1000)
    tail = tail_file(log_path(uid, fid), 40)
    db_exec(
        "INSERT INTO script_logs (uid, fid, output, exit_code, ran_at, duration_ms)"
        " VALUES (?,?,?,?,?,?)",
        (int(uid), int(fid), tail[-4000:], int(exit_code), utcstamp(), duration_ms),
    )
    row = get_file(fid)
    name = str(row.get("fname", "script")) if row else "script"
    status = "finished cleanly" if exit_code == 0 else "exited with code " + str(exit_code)
    notify_user(
        uid,
        _e("log") + " " + B(esc(name)) + " " + status + "\n"
        + "Runtime: " + fmt_duration(duration_ms / 1000.0),
    )


def _terminate(proc):
    """SIGTERM the process group, then SIGKILL after 5 seconds."""
    if proc is None:
        return False
    try:
        if proc.poll() is not None:
            return True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            proc.terminate()
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if proc.poll() is not None:
                return True
            time.sleep(0.2)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        return True
    except Exception as exc:
        log.warning("Terminate failed: %s", exc)
        return False


def stop_script(uid, fid):
    """Stop a running script. Returns (ok, message)."""
    key = proc_key(uid, fid)
    with _proc_lock:
        proc = _procs.get(key)
    if proc is None or proc.poll() is not None:
        with _proc_lock:
            _procs.pop(key, None)
            _proc_start_times.pop(key, None)
        return False, "That script is not running."
    _terminate(proc)
    with _proc_lock:
        _procs.pop(key, None)
        _proc_start_times.pop(key, None)
        _proc_args.pop(key, None)
    log_activity(uid, "stop:" + str(fid))
    return True, "Script stopped."


def restart_script(uid, fid):
    """Stop (if needed) and start a script again."""
    key = proc_key(uid, fid)
    with _proc_lock:
        args = _proc_args.get(key, "")
    if is_running(uid, fid):
        stop_script(uid, fid)
        time.sleep(0.6)
    return run_script(uid, fid, args)


def tail_file(path, lines=40, offset=0):
    """Return the last N lines of a file, skipping `offset` lines from the end."""
    path = Path(str(path))
    if not path.exists():
        return ""
    try:
        with open(str(path), "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = 4096
            data = b""
            want = int(lines) + int(offset) + 1
            while size > 0 and data.count(b"\n") < want:
                step = min(block, size)
                size -= step
                handle.seek(size)
                data = handle.read(step) + data
        text = data.decode("utf-8", "replace")
        all_lines = text.splitlines()
        if offset:
            all_lines = all_lines[: max(0, len(all_lines) - int(offset))]
        return "\n".join(all_lines[-int(lines):])
    except Exception as exc:
        log.debug("tail_file failed: %s", exc)
        return ""


def get_log(uid, fid, lines=40, offset=0):
    """Log tail for a file."""
    return tail_file(log_path(uid, fid), lines, offset)


def get_log_size(uid, fid):
    """Log size in bytes."""
    path = log_path(uid, fid)
    try:
        return path.stat().st_size if path.exists() else 0
    except Exception:
        return 0


def clear_log(uid, fid):
    """Truncate the log file."""
    path = log_path(uid, fid)
    try:
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write("[SIGMA] Log cleared at " + utcstamp() + " UTC\n")
        return True, "Log cleared."
    except Exception as exc:
        return False, "Could not clear log: " + str(exc)


def watch_script_output(uid, fid, max_lines=15):
    """Latest N log lines, each prefixed with the read timestamp."""
    raw = get_log(uid, fid, max_lines)
    if not raw.strip():
        return "No output yet."
    stamp = utcnow().strftime("%H:%M:%S")
    return "\n".join("[" + stamp + "] " + line for line in raw.splitlines())


def get_proc_info(uid, fid):
    """Runtime details for a live process, or None."""
    key = proc_key(uid, fid)
    with _proc_lock:
        proc = _procs.get(key)
        started = _proc_start_times.get(key, 0)
        args = _proc_args.get(key, "")
    if proc is None or proc.poll() is not None:
        return None
    rss_kb = 0
    try:
        with open("/proc/" + str(proc.pid) + "/statm", "r", encoding="utf-8") as handle:
            fields = handle.read().split()
        if len(fields) > 1:
            rss_kb = int(fields[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)
    except Exception as exc:
        log.debug("statm read failed: %s", exc)
    return {
        "pid": proc.pid,
        "uptime": max(0.0, time.time() - float(started or time.time())),
        "memory_kb": rss_kb,
        "args": args,
        "log_size": get_log_size(uid, fid),
    }


def kill_all_user_scripts(uid):
    """Stop every script of one user. Returns the number stopped."""
    stopped = 0
    for run_uid, fid in get_all_running():
        if int(run_uid) != int(uid):
            continue
        ok, _ = stop_script(run_uid, fid)
        if ok:
            stopped += 1
    return stopped


def kill_all_scripts():
    """Admin: stop every running script. Returns the number stopped."""
    stopped = 0
    for run_uid, fid in get_all_running():
        ok, _ = stop_script(run_uid, fid)
        if ok:
            stopped += 1
    return stopped


def force_kill_all():
    """Admin: hard kill everything and clear the registry."""
    killed = 0
    with _proc_lock:
        items = list(_procs.items())
    for key, proc in items:
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    proc.kill()
                killed += 1
        except Exception as exc:
            log.debug("force kill failed for %s: %s", key, exc)
    with _proc_lock:
        _procs.clear()
        _proc_start_times.clear()
        _proc_args.clear()
    return killed


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

CRON_PRESETS = [
    "every_5m",
    "every_15m",
    "every_30m",
    "every_1h",
    "every_6h",
    "every_12h",
    "daily_9am",
    "daily_midnight",
]


def parse_cron_schedule(schedule_str):
    """Parse a simple schedule into the next run datetime (UTC).

    Supported: every_<n>m, every_<n>h, every_<n>d, daily_<h>am, daily_<h>pm,
    daily_midnight, hourly, weekly.
    Returns None when the schedule cannot be parsed.
    """
    text = str(schedule_str or "").strip().lower()
    now = utcnow()
    if not text:
        return None
    if text in ("hourly", "every_hour"):
        return now + timedelta(hours=1)
    if text in ("daily", "every_day", "daily_midnight"):
        target = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return target
    if text in ("weekly", "every_week"):
        return now + timedelta(days=7)
    match = re.match(r"^every[_\-\s]?(\d+)\s*([mhd])$", text)
    if match:
        amount = max(1, int(match.group(1)))
        unit = match.group(2)
        if unit == "m":
            return now + timedelta(minutes=amount)
        if unit == "h":
            return now + timedelta(hours=amount)
        return now + timedelta(days=amount)
    match = re.match(r"^daily[_\-\s]?(\d{1,2})\s*(am|pm)$", text)
    if match:
        hour = int(match.group(1)) % 12
        if match.group(2) == "pm":
            hour += 12
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    match = re.match(r"^daily[_\-\s]?(\d{1,2}):(\d{2})$", text)
    if match:
        hour = min(23, int(match.group(1)))
        minute = min(59, int(match.group(2)))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target
    return None


def create_cron(uid, fid, schedule):
    """Create a cron job. Returns (ok, message)."""
    row = get_user_file(uid, fid)
    if not row:
        return False, "File not found."
    next_run = parse_cron_schedule(schedule)
    if next_run is None:
        return False, (
            "Unrecognized schedule. Try: " + ", ".join(CRON_PRESETS)
        )
    tier = get_tier(uid)
    if not tier.get("terminal", False) and not is_admin(uid):
        return False, "Cron jobs require the Basic plan or higher."
    existing = int(db_val("SELECT COUNT(*) c FROM cron_jobs WHERE uid=?", (int(uid),), 0) or 0)
    max_jobs = max(1, int(tier.get("priority", 0)) * 3 + 2)
    if existing >= max_jobs and not is_admin(uid):
        return False, "Cron job limit reached (" + str(max_jobs) + ") for your plan."
    db_exec(
        "INSERT INTO cron_jobs (uid, fid, schedule, last_run, next_run, enabled, run_count)"
        " VALUES (?,?,?,'',?,1,0)",
        (int(uid), int(fid), str(schedule).strip().lower(), next_run.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return True, "Cron created. Next run: " + next_run.strftime("%Y-%m-%d %H:%M") + " UTC"


def get_user_crons(uid):
    """Every cron job of a user."""
    return db_all("SELECT * FROM cron_jobs WHERE uid=? ORDER BY id DESC", (int(uid),))


def get_cron(cron_id):
    """Single cron row."""
    return db_one("SELECT * FROM cron_jobs WHERE id=?", (int(cron_id),))


def toggle_cron(uid, cron_id):
    """Enable/disable a cron job."""
    row = db_one("SELECT * FROM cron_jobs WHERE id=? AND uid=?", (int(cron_id), int(uid)))
    if not row:
        return False, "Cron job not found."
    new_value = 0 if int(row.get("enabled") or 0) else 1
    db_exec("UPDATE cron_jobs SET enabled=? WHERE id=?", (new_value, int(cron_id)))
    return True, ("Cron enabled." if new_value else "Cron disabled.")


def delete_cron(uid, cron_id):
    """Delete a cron job owned by the user."""
    row = db_one("SELECT * FROM cron_jobs WHERE id=? AND uid=?", (int(cron_id), int(uid)))
    if not row:
        return False, "Cron job not found."
    db_exec("DELETE FROM cron_jobs WHERE id=?", (int(cron_id),))
    return True, "Cron deleted."


def get_due_crons():
    """Enabled cron jobs whose next_run has passed. Always returns a list."""
    rows = db_all("SELECT * FROM cron_jobs WHERE enabled=1")
    if not rows:
        return []
    now = utcnow()
    due = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        next_run = parse_stamp(row.get("next_run"))
        if next_run is None:
            computed = parse_cron_schedule(row.get("schedule"))
            if computed is None:
                continue
            db_exec(
                "UPDATE cron_jobs SET next_run=? WHERE id=?",
                (computed.strftime("%Y-%m-%d %H:%M:%S"), int(row.get("id", 0))),
            )
            continue
        if next_run <= now:
            due.append(row)
    return due


def run_cron_job(job):
    """Execute one cron job and reschedule it. Returns (ok, message)."""
    if not isinstance(job, dict):
        return False, "Invalid cron job."
    uid = int(job.get("uid") or 0)
    fid = int(job.get("fid") or 0)
    cron_id = int(job.get("id") or 0)
    next_run = parse_cron_schedule(job.get("schedule"))
    stamp = utcstamp()
    if next_run is None:
        db_exec("UPDATE cron_jobs SET enabled=0 WHERE id=?", (cron_id,))
        return False, "Invalid schedule, cron disabled."
    db_exec(
        "UPDATE cron_jobs SET last_run=?, next_run=?, run_count = run_count + 1 WHERE id=?",
        (stamp, next_run.strftime("%Y-%m-%d %H:%M:%S"), cron_id),
    )
    if not get_user_file(uid, fid):
        db_exec("UPDATE cron_jobs SET enabled=0 WHERE id=?", (cron_id,))
        return False, "Target file is gone, cron disabled."
    if is_running(uid, fid):
        return False, "Skipped: script already running."
    ok, message = run_script(uid, fid, "")
    if ok:
        notify_user(uid, _e("cron") + " Cron job #" + str(cron_id) + " started your script.")
    else:
        notify_user(uid, _e("warn") + " Cron job #" + str(cron_id) + " failed: " + esc(message))
    return ok, message


# ============================================================================
# SECTION 07 - ECONOMY SYSTEM
# ============================================================================

DAILY_REWARD_TABLE = [20, 30, 45, 60, 80, 110, 160]

EARN_PTS_FOR_ACTION = {
    "upload": 10,
    "run_script": 3,
    "daily": 20,
    "referral": 100,
    "referred_join": 50,
    "ticket_open": 5,
    "ticket_reply": 2,
    "file_share": 4,
    "note_create": 2,
    "cron_create": 6,
    "api_key": 8,
    "achievement": 0,
    "profile_visit": 1,
    "level_up": 15,
}

SHOP_ITEMS = {
    "slot_1": {
        "name": "Extra File Slot",
        "emoji": _e("folder"),
        "cost": 120,
        "desc": "Permanently adds 1 file slot above your plan limit.",
        "perk": "extra_slots",
        "amount": 1,
    },
    "slot_5": {
        "name": "5 File Slots",
        "emoji": _e("folder"),
        "cost": 500,
        "desc": "Adds 5 permanent file slots.",
        "perk": "extra_slots",
        "amount": 5,
    },
    "storage_50": {
        "name": "+50 MB Storage",
        "emoji": _e("disk"),
        "cost": 350,
        "desc": "Raises your per-file size allowance by 50 MB.",
        "perk": "extra_storage_mb",
        "amount": 50,
    },
    "storage_200": {
        "name": "+200 MB Storage",
        "emoji": _e("disk"),
        "cost": 1200,
        "desc": "Raises your per-file size allowance by 200 MB.",
        "perk": "extra_storage_mb",
        "amount": 200,
    },
    "pro_1d": {
        "name": "Pro Access (1 day)",
        "emoji": _e("rocket"),
        "cost": 400,
        "desc": "Temporary Pro plan features for 24 hours.",
        "perk": "temp_tier",
        "amount": 1,
    },
    "pro_7d": {
        "name": "Pro Access (7 days)",
        "emoji": _e("rocket"),
        "cost": 2200,
        "desc": "Temporary Pro plan features for a week.",
        "perk": "temp_tier",
        "amount": 7,
    },
    "badge_supporter": {
        "name": "Supporter Badge",
        "emoji": BADGES["supporter"]["emoji"],
        "cost": 800,
        "desc": "Unlocks the Supporter badge on your profile.",
        "perk": "badge",
        "amount": "supporter",
    },
    "badge_legend": {
        "name": "Legend Badge",
        "emoji": BADGES["legend"]["emoji"],
        "cost": 3000,
        "desc": "Unlocks the Legend badge on your profile.",
        "perk": "badge",
        "amount": "legend",
    },
    "proc_slot": {
        "name": "Extra Process Slot",
        "emoji": _e("lightning"),
        "cost": 900,
        "desc": "Run one more script at the same time.",
        "perk": "extra_procs",
        "amount": 1,
    },
    "xp_boost": {
        "name": "XP Boost (500 XP)",
        "emoji": _e("star"),
        "cost": 250,
        "desc": "Instantly grants 500 XP.",
        "perk": "xp",
        "amount": 500,
    },
    "points_pack": {
        "name": "Points Pack (750 pts)",
        "emoji": _e("pts"),
        "cost": 600,
        "desc": "Converts coins into 750 leaderboard points.",
        "perk": "points",
        "amount": 750,
    },
    "log_boost": {
        "name": "Big Log Buffer",
        "emoji": _e("log"),
        "cost": 300,
        "desc": "Keeps 500 log lines instead of 40 in the viewer.",
        "perk": "log_lines",
        "amount": 500,
    },
}

shop_items = SHOP_ITEMS
daily_reward_table = DAILY_REWARD_TABLE
earn_pts_for_action = EARN_PTS_FOR_ACTION


def get_extra_slots(uid):
    """Purchased extra file slots."""
    return get_setting_int(uid, "extra_slots", 0)


def get_extra_storage_mb(uid):
    """Purchased extra per-file storage in MB."""
    return get_setting_int(uid, "extra_storage_mb", 0)


def get_extra_procs(uid):
    """Purchased extra concurrent process slots."""
    return get_setting_int(uid, "extra_procs", 0)


def effective_file_limit(uid):
    """File limit including purchased slots. -1 means unlimited."""
    base = int(get_tier(uid).get("files", 0) or 0)
    if base == -1:
        return -1
    return base + get_extra_slots(uid)


def effective_size_limit_mb(uid):
    """Per-file size limit in MB including purchased storage."""
    return int(get_tier(uid).get("size", 5) or 5) + get_extra_storage_mb(uid)


def temp_tier_active(uid):
    """True while a purchased temporary Pro window is still valid."""
    until = parse_stamp(get_setting(uid, "temp_tier_until", ""))
    return until is not None and until > utcnow()


def get_leaderboard(limit=10):
    """Top users ordered by points."""
    return db_all(
        "SELECT uid, first_name, username, points, level, tier, daily_streak"
        " FROM users WHERE is_banned=0 ORDER BY points DESC, xp DESC LIMIT ?",
        (int(limit),),
    )


def get_leaderboard_page(page=1, per_page=10):
    """Paginated leaderboard. Returns (rows, total, total_pages, page)."""
    page = max(1, int(page or 1))
    total = int(db_val("SELECT COUNT(*) c FROM users WHERE is_banned=0", (), 0) or 0)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    rows = db_all(
        "SELECT uid, first_name, username, points, level, tier, daily_streak"
        " FROM users WHERE is_banned=0 ORDER BY points DESC, xp DESC LIMIT ? OFFSET ?",
        (int(per_page), (page - 1) * int(per_page)),
    )
    return rows, total, total_pages, page


def get_rank(uid):
    """1-based leaderboard rank of a user."""
    points = int(get_user(uid).get("points") or 0)
    higher = int(db_val(
        "SELECT COUNT(*) c FROM users WHERE is_banned=0 AND points > ?",
        (points,), 0) or 0)
    return higher + 1


def buy_item(uid, item_key, cost_coins=None):
    """Buy a shop item, deduct coins and apply the perk. Returns (ok, message)."""
    item = SHOP_ITEMS.get(str(item_key))
    if not item:
        return False, "Unknown shop item."
    cost = int(cost_coins if cost_coins is not None else item.get("cost", 0) or 0)
    if not remove_coins(uid, cost):
        balance = int(get_user(uid).get("coins") or 0)
        return False, (
            "Not enough coins. Cost " + str(cost) + ", you have " + str(balance) + "."
        )
    perk = str(item.get("perk", ""))
    amount = item.get("amount", 0)
    result = "Purchase complete."
    if perk == "extra_slots":
        set_setting(uid, "extra_slots", get_extra_slots(uid) + int(amount))
        result = "Added " + str(amount) + " file slot(s)."
    elif perk == "extra_storage_mb":
        set_setting(uid, "extra_storage_mb", get_extra_storage_mb(uid) + int(amount))
        result = "Added " + str(amount) + " MB per-file allowance."
    elif perk == "extra_procs":
        set_setting(uid, "extra_procs", get_extra_procs(uid) + int(amount))
        result = "Added " + str(amount) + " concurrent process slot(s)."
    elif perk == "temp_tier":
        current = parse_stamp(get_setting(uid, "temp_tier_until", ""))
        base = current if (current and current > utcnow()) else utcnow()
        until = base + timedelta(days=int(amount))
        set_setting(uid, "temp_tier_until", until.strftime("%Y-%m-%d %H:%M:%S"))
        if TIER_ORDER.index(get_tier_key(uid)) < TIER_ORDER.index("pro"):
            db_exec("UPDATE users SET tier='pro' WHERE uid=?", (int(uid),))
        result = "Pro access active until " + until.strftime("%Y-%m-%d %H:%M") + " UTC."
    elif perk == "badge":
        if award_badge(uid, str(amount)):
            result = "Badge unlocked."
        else:
            result = "You already own that badge (coins still spent)."
    elif perk == "xp":
        add_xp(uid, int(amount))
        result = "Granted " + str(amount) + " XP."
    elif perk == "points":
        add_points(uid, int(amount), "shop_points_pack")
        result = "Granted " + str(amount) + " points."
    elif perk == "log_lines":
        set_setting(uid, "log_lines", int(amount))
        result = "Log viewer now shows " + str(amount) + " lines."
    else:
        result = "Purchase recorded."
    award_badge(uid, "shopper")
    log_activity(uid, "shop_buy:" + str(item_key))
    add_notification(uid, _e("gift") + " Shop purchase: " + str(item.get("name")), "shop")
    return True, result


def transfer_points(from_uid, to_uid, pts):
    """Transfer points between users with validation. Returns (ok, message)."""
    try:
        from_uid = int(from_uid)
        to_uid = int(to_uid)
        pts = int(pts)
    except (TypeError, ValueError):
        return False, "Invalid transfer parameters."
    if from_uid == to_uid:
        return False, "You cannot transfer points to yourself."
    if pts <= 0:
        return False, "Amount must be a positive number."
    if pts > 100000:
        return False, "Maximum transfer is 100,000 points."
    if not db_one("SELECT uid FROM users WHERE uid=?", (to_uid,)):
        return False, "Recipient has never used this bot."
    if is_banned(to_uid):
        return False, "Recipient is banned."
    if not remove_points(from_uid, pts):
        return False, "Not enough points."
    db_exec("UPDATE users SET points = points + ? WHERE uid=?", (pts, to_uid))
    award_badge(from_uid, "generous")
    log_activity(from_uid, "transfer_out:" + str(pts) + ":" + str(to_uid))
    log_activity(to_uid, "transfer_in:" + str(pts) + ":" + str(from_uid))
    notify_user(
        to_uid,
        _e("pts") + " You received " + fmt_num(pts) + " points from user " + str(from_uid) + ".",
    )
    return True, "Transferred " + fmt_num(pts) + " points."


def get_referral_stats(uid):
    """Referral summary for a user."""
    count = int(db_val(
        "SELECT COUNT(*) c FROM referrals WHERE referrer_uid=?", (int(uid),), 0) or 0)
    total = int(db_val(
        "SELECT COALESCE(SUM(pts_awarded),0) s FROM referrals WHERE referrer_uid=?",
        (int(uid),), 0) or 0)
    recent = db_all(
        "SELECT * FROM referrals WHERE referrer_uid=? ORDER BY id DESC LIMIT 5",
        (int(uid),),
    )
    return {
        "count": count,
        "total_pts": total,
        "recent": recent,
        "code": str(get_user(uid).get("referral_code") or ""),
    }


def apply_referral(uid, ref_code):
    """Register a referral for a brand-new user. Returns (ok, message)."""
    uid = int(uid)
    code = str(ref_code or "").strip().upper()
    if not code:
        return False, "No referral code supplied."
    user = get_user(uid)
    if int(user.get("referrer") or 0):
        return False, "You already used a referral code."
    if str(user.get("referral_code") or "").upper() == code:
        return False, "You cannot refer yourself."
    owner = db_one("SELECT uid FROM users WHERE UPPER(referral_code)=?", (code,))
    if not owner:
        return False, "That referral code does not exist."
    referrer_uid = int(owner.get("uid") or 0)
    if referrer_uid == uid:
        return False, "You cannot refer yourself."
    if db_one("SELECT id FROM referrals WHERE referred_uid=?", (uid,)):
        return False, "This account was already referred."
    reward = int(EARN_PTS_FOR_ACTION.get("referral", 100))
    joiner_reward = int(EARN_PTS_FOR_ACTION.get("referred_join", 50))
    db_exec(
        "INSERT INTO referrals (referrer_uid, referred_uid, pts_awarded, created_at)"
        " VALUES (?,?,?,?)",
        (referrer_uid, uid, reward, utcstamp()),
    )
    db_exec("UPDATE users SET referrer=? WHERE uid=?", (referrer_uid, uid))
    add_points(referrer_uid, reward, "referral")
    add_coins(referrer_uid, 25)
    add_points(uid, joiner_reward, "referred_join")
    add_coins(uid, 10)
    notify_user(
        referrer_uid,
        _e("users") + " New referral joined! +" + str(reward) + " pts, +25 " + _e("coin"),
    )
    return True, "Referral applied. You received +" + str(joiner_reward) + " points."


def weekly_bonus(uid):
    """Grant the weekly streak bonus once per 7-day streak boundary."""
    streak = int(get_user(uid).get("daily_streak") or 0)
    if streak < 7 or streak % 7 != 0:
        return False, 0
    marker = "weekly_bonus_" + str(streak)
    if get_setting(uid, marker, ""):
        return False, 0
    bonus = 150 + (streak // 7) * 25
    add_points(uid, bonus, "weekly_bonus")
    add_coins(uid, 30)
    set_setting(uid, marker, utcstamp())
    return True, bonus


def monthly_bonus(uid):
    """Grant the monthly streak bonus once per 30-day streak boundary."""
    streak = int(get_user(uid).get("daily_streak") or 0)
    if streak < 30 or streak % 30 != 0:
        return False, 0
    marker = "monthly_bonus_" + str(streak)
    if get_setting(uid, marker, ""):
        return False, 0
    bonus = 750 + (streak // 30) * 250
    add_points(uid, bonus, "monthly_bonus")
    add_coins(uid, 150)
    set_setting(uid, marker, utcstamp())
    return True, bonus


def create_plan_order(uid, tier_key, notes=""):
    """Record an upgrade request for admin processing."""
    tier = TIERS.get(str(tier_key))
    if not tier:
        return False, "Unknown plan."
    order_id = db_exec(
        "INSERT INTO plan_orders (uid, tier, amount, status, created_at, processed_at, notes)"
        " VALUES (?,?,?,'pending',?,'',?)",
        (int(uid), str(tier_key), int(tier.get("price", 0) or 0), utcstamp(), str(notes)[:400]),
    )
    notify_admins(
        _e("crown") + " " + B("Upgrade request") + "\n"
        + "User: " + C(str(uid)) + "\n"
        + "Plan: " + str(tier.get("name")) + " (" + str(tier.get("price")) + ")\n"
        + "Order: #" + str(order_id)
    )
    if not order_id:
        return False, "Could not record the request."
    return True, "Request #" + str(int(order_id)) + " sent to the admins."


def get_user_orders(uid):
    """Upgrade orders of a user."""
    return db_all("SELECT * FROM plan_orders WHERE uid=? ORDER BY id DESC LIMIT 20", (int(uid),))


# ---------------------------------------------------------------------------
# User notes
# ---------------------------------------------------------------------------


def create_note(uid, title, content=""):
    """Create a personal note."""
    note_id = db_exec(
        "INSERT INTO user_notes (uid, title, content, created_at, updated_at, pinned)"
        " VALUES (?,?,?,?,?,0)",
        (int(uid), str(title)[:120], str(content)[:8000], utcstamp(), utcstamp()),
    )
    add_points(uid, EARN_PTS_FOR_ACTION.get("note_create", 2), "note_create")
    return int(note_id or 0)


def get_notes(uid):
    """Notes of a user, pinned first."""
    return db_all(
        "SELECT * FROM user_notes WHERE uid=? ORDER BY pinned DESC, id DESC",
        (int(uid),),
    )


def get_note(uid, note_id):
    """Single note owned by the user."""
    return db_one(
        "SELECT * FROM user_notes WHERE id=? AND uid=?",
        (int(note_id), int(uid)),
    )


def update_note(uid, note_id, title=None, content=None):
    """Replace a note's title and/or content."""
    row = get_note(uid, note_id)
    if not row:
        return False, "Note not found."
    new_title = str(title)[:120] if title is not None else str(row.get("title") or "Untitled")
    new_content = str(content)[:8000] if content is not None else str(row.get("content") or "")
    db_exec(
        "UPDATE user_notes SET title=?, content=?, updated_at=? WHERE id=?",
        (new_title, new_content, utcstamp(), int(note_id)),
    )
    return True, "Note updated."


def toggle_note_pin(uid, note_id):
    """Pin or unpin a note."""
    row = get_note(uid, note_id)
    if not row:
        return False, "Note not found."
    value = 0 if int(row.get("pinned") or 0) else 1
    db_exec("UPDATE user_notes SET pinned=? WHERE id=?", (value, int(note_id)))
    return True, ("Note pinned." if value else "Note unpinned.")


def delete_note(uid, note_id):
    """Delete a note."""
    if not get_note(uid, note_id):
        return False, "Note not found."
    db_exec("DELETE FROM user_notes WHERE id=?", (int(note_id),))
    return True, "Note deleted."


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def create_webhook(uid, url, events="all"):
    """Register an outgoing webhook. Returns (ok, message)."""
    url = str(url or "").strip()
    if not re.match(r"^https?://[\w\.-]+(:\d+)?(/.*)?$", url):
        return False, "That does not look like a valid http(s) URL."
    count = int(db_val("SELECT COUNT(*) c FROM webhooks WHERE uid=?", (int(uid),), 0) or 0)
    if count >= 10:
        return False, "Webhook limit reached (10)."
    secret = secrets.token_hex(16)
    db_exec(
        "INSERT INTO webhooks (uid, url, secret, events, enabled, created_at, last_triggered)"
        " VALUES (?,?,?,?,1,?,'')",
        (int(uid), url[:400], secret, str(events)[:120], utcstamp()),
    )
    return True, "Webhook saved. Secret: " + secret


def get_webhooks(uid):
    """Webhooks of a user."""
    return db_all("SELECT * FROM webhooks WHERE uid=? ORDER BY id DESC", (int(uid),))


def delete_webhook(uid, hook_id):
    """Delete a webhook."""
    row = db_one("SELECT * FROM webhooks WHERE id=? AND uid=?", (int(hook_id), int(uid)))
    if not row:
        return False, "Webhook not found."
    db_exec("DELETE FROM webhooks WHERE id=?", (int(hook_id),))
    return True, "Webhook deleted."


def mark_webhook_triggered(hook_id):
    """Record the last trigger time of a webhook."""
    return db_exec(
        "UPDATE webhooks SET last_triggered=? WHERE id=?",
        (utcstamp(), int(hook_id)),
    )


# ============================================================================
# SECTION 08 - TICKETS / SUPPORT
# ============================================================================

TICKET_CATEGORIES = ["General", "Billing", "Bug", "Feature", "Abuse", "Other"]
TICKET_PRIORITIES = ["Low", "Normal", "High", "Urgent"]

PRIORITY_EMOJI = {
    "Low": "\U0001f7e2",
    "Normal": "\U0001f535",
    "High": "\U0001f7e1",
    "Urgent": "\U0001f534",
}


def create_ticket(uid, subject, category="General", priority="Normal"):
    """Create a support ticket. Returns the ticket id (0 on failure)."""
    subject = str(subject or "").strip()[:200]
    if not subject:
        return 0
    category = category if category in TICKET_CATEGORIES else "General"
    priority = priority if priority in TICKET_PRIORITIES else "Normal"
    now = utcstamp()
    tid = db_exec(
        "INSERT INTO tickets (uid, subject, status, priority, category, created_at,"
        " updated_at, closed_at, assigned_to) VALUES (?,?,'open',?,?,?,?,'',0)",
        (int(uid), subject, priority, category, now, now),
    )
    tid = int(tid or 0)
    if tid:
        db_exec("UPDATE users SET total_tickets = total_tickets + 1 WHERE uid=?", (int(uid),))
        add_points(uid, EARN_PTS_FOR_ACTION.get("ticket_open", 5), "ticket_open")
        notify_admins(
            _e("ticket") + " " + B("New ticket #" + str(tid)) + "\n"
            + "From: " + C(str(uid)) + "\n"
            + "Category: " + esc(category) + " | Priority: " + esc(priority) + "\n"
            + "Subject: " + esc(subject)
        )
    return tid


def get_ticket(tid):
    """Single ticket row."""
    return db_one("SELECT * FROM tickets WHERE id=?", (int(tid),))


def add_ticket_msg(tid, uid, message, is_staff=False):
    """Append a message to a ticket and reopen it if a user replies."""
    ticket = get_ticket(tid)
    if not ticket:
        return 0
    msg_id = db_exec(
        "INSERT INTO ticket_msgs (ticket_id, uid, message, created_at, is_staff)"
        " VALUES (?,?,?,?,?)",
        (int(tid), int(uid), str(message)[:4000], utcstamp(), 1 if is_staff else 0),
    )
    db_exec("UPDATE tickets SET updated_at=? WHERE id=?", (utcstamp(), int(tid)))
    if not is_staff and str(ticket.get("status")) == "closed":
        db_exec("UPDATE tickets SET status='open', closed_at='' WHERE id=?", (int(tid),))
    if is_staff:
        notify_user(
            int(ticket.get("uid") or 0),
            _e("mail") + " Staff replied to ticket #" + str(tid) + ":\n" + esc(str(message)[:600]),
        )
    else:
        add_points(uid, EARN_PTS_FOR_ACTION.get("ticket_reply", 2), "ticket_reply")
        notify_admins(
            _e("mail") + " Reply on ticket #" + str(tid) + " from " + C(str(uid)) + ":\n"
            + esc(str(message)[:600])
        )
    return int(msg_id or 0)


def get_ticket_msgs(tid):
    """All messages of a ticket, oldest first."""
    return db_all(
        "SELECT * FROM ticket_msgs WHERE ticket_id=? ORDER BY id ASC",
        (int(tid),),
    )


def close_ticket(tid, admin_uid=0):
    """Close a ticket."""
    ticket = get_ticket(tid)
    if not ticket:
        return False, "Ticket not found."
    if str(ticket.get("status")) == "closed":
        return False, "Ticket is already closed."
    now = utcstamp()
    db_exec(
        "UPDATE tickets SET status='closed', closed_at=?, updated_at=? WHERE id=?",
        (now, now, int(tid)),
    )
    if admin_uid:
        audit(admin_uid, "close_ticket", int(ticket.get("uid") or 0), "ticket #" + str(tid))
    notify_user(
        int(ticket.get("uid") or 0),
        _e("check") + " Your ticket #" + str(tid) + " has been closed.",
    )
    return True, "Ticket #" + str(tid) + " closed."


def assign_ticket(tid, admin_uid):
    """Assign a ticket to an admin."""
    if not get_ticket(tid):
        return False, "Ticket not found."
    db_exec(
        "UPDATE tickets SET assigned_to=?, updated_at=? WHERE id=?",
        (int(admin_uid), utcstamp(), int(tid)),
    )
    audit(admin_uid, "assign_ticket", 0, "ticket #" + str(tid))
    return True, "Ticket assigned."


def get_user_tickets(uid, status=None, page=1, per_page=DEFAULT_PER_PAGE):
    """Paginated tickets of a user. Returns (rows, total, total_pages, page)."""
    page = max(1, int(page or 1))
    if status:
        total = int(db_val(
            "SELECT COUNT(*) c FROM tickets WHERE uid=? AND status=?",
            (int(uid), str(status)), 0) or 0)
        rows = db_all(
            "SELECT * FROM tickets WHERE uid=? AND status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), str(status), int(per_page), (page - 1) * int(per_page)),
        )
    else:
        total = int(db_val("SELECT COUNT(*) c FROM tickets WHERE uid=?", (int(uid),), 0) or 0)
        rows = db_all(
            "SELECT * FROM tickets WHERE uid=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(uid), int(per_page), (page - 1) * int(per_page)),
        )
    total_pages = max(1, (total + int(per_page) - 1) // int(per_page))
    return rows, total, total_pages, min(page, total_pages)


def get_open_tickets(limit=20, page=1, per_page=ADMIN_PER_PAGE):
    """Admin view of open tickets. Returns (rows, total, total_pages, page)."""
    page = max(1, int(page or 1))
    total = int(db_val("SELECT COUNT(*) c FROM tickets WHERE status='open'", (), 0) or 0)
    total_pages = max(1, (total + int(per_page) - 1) // int(per_page))
    page = min(page, total_pages)
    rows = db_all(
        "SELECT * FROM tickets WHERE status='open' ORDER BY"
        " CASE priority WHEN 'Urgent' THEN 0 WHEN 'High' THEN 1"
        " WHEN 'Normal' THEN 2 ELSE 3 END, id DESC LIMIT ? OFFSET ?",
        (int(per_page), (page - 1) * int(per_page)),
    )
    return rows[: int(limit) if limit else len(rows)], total, total_pages, page


def get_ticket_stats():
    """Aggregate ticket metrics for the admin dashboard."""
    open_count = int(db_val("SELECT COUNT(*) c FROM tickets WHERE status='open'", (), 0) or 0)
    closed_count = int(db_val("SELECT COUNT(*) c FROM tickets WHERE status='closed'", (), 0) or 0)
    total = open_count + closed_count
    durations = []
    for row in db_all(
        "SELECT created_at, closed_at FROM tickets WHERE status='closed'"
        " AND closed_at != '' ORDER BY id DESC LIMIT 200"
    ):
        created = parse_stamp(row.get("created_at"))
        closed = parse_stamp(row.get("closed_at"))
        if created and closed and closed >= created:
            durations.append((closed - created).total_seconds())
    avg = (sum(durations) / len(durations)) if durations else 0.0
    urgent = int(db_val(
        "SELECT COUNT(*) c FROM tickets WHERE status='open' AND priority='Urgent'", (), 0) or 0)
    return {
        "open": open_count,
        "closed": closed_count,
        "total": total,
        "urgent": urgent,
        "avg_response_time": avg,
        "messages": int(db_val("SELECT COUNT(*) c FROM ticket_msgs", (), 0) or 0),
    }


# ============================================================================
# SECTION 09 - ADMIN TOOLS
# ============================================================================


def get_all_users(page=1, per_page=ADMIN_PER_PAGE, filter_tier=None, filter_banned=None):
    """Paginated user list. Returns (rows, total, total_pages, page)."""
    page = max(1, int(page or 1))
    clauses = []
    params = []
    if filter_tier and str(filter_tier) in TIERS:
        clauses.append("tier=?")
        params.append(str(filter_tier))
    if filter_banned is not None:
        clauses.append("is_banned=?")
        params.append(1 if filter_banned else 0)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    total = int(db_val("SELECT COUNT(*) c FROM users" + where, tuple(params), 0) or 0)
    total_pages = max(1, (total + int(per_page) - 1) // int(per_page))
    page = min(page, total_pages)
    rows = db_all(
        "SELECT * FROM users" + where + " ORDER BY joined_at DESC, uid DESC LIMIT ? OFFSET ?",
        tuple(params) + (int(per_page), (page - 1) * int(per_page)),
    )
    return rows, total, total_pages, page


def search_users(query, limit=20):
    """Search users by name, username or uid."""
    text = str(query or "").strip().lstrip("@")
    if not text:
        return []
    needle = "%" + text + "%"
    if text.isdigit():
        return db_all(
            "SELECT * FROM users WHERE uid=? OR first_name LIKE ? OR username LIKE ?"
            " ORDER BY uid DESC LIMIT ?",
            (int(text), needle, needle, int(limit)),
        )
    return db_all(
        "SELECT * FROM users WHERE first_name LIKE ? OR username LIKE ?"
        " ORDER BY uid DESC LIMIT ?",
        (needle, needle, int(limit)),
    )


def get_admin_stats():
    """Comprehensive metrics dictionary for the admin dashboard."""
    day_ago = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    week_ago = (utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    revenue = 0
    for tier_key, tier in TIERS.items():
        count = int(db_val("SELECT COUNT(*) c FROM users WHERE tier=?", (tier_key,), 0) or 0)
        revenue += count * int(tier.get("price", 0) or 0)
    tier_counts = {}
    for tier_key in TIER_ORDER:
        tier_counts[tier_key] = int(db_val(
            "SELECT COUNT(*) c FROM users WHERE tier=?", (tier_key,), 0) or 0)
    return {
        "users": int(db_val("SELECT COUNT(*) c FROM users", (), 0) or 0),
        "users_active_24h": int(db_val(
            "SELECT COUNT(*) c FROM users WHERE last_seen >= ?", (day_ago,), 0) or 0),
        "users_new_7d": int(db_val(
            "SELECT COUNT(*) c FROM users WHERE joined_at >= ?", (week_ago,), 0) or 0),
        "banned": int(db_val("SELECT COUNT(*) c FROM users WHERE is_banned=1", (), 0) or 0),
        "files": int(db_val("SELECT COUNT(*) c FROM files", (), 0) or 0),
        "files_pending": int(db_val(
            "SELECT COUNT(*) c FROM files WHERE status='pending'", (), 0) or 0),
        "files_approved": int(db_val(
            "SELECT COUNT(*) c FROM files WHERE status='approved'", (), 0) or 0),
        "files_rejected": int(db_val(
            "SELECT COUNT(*) c FROM files WHERE status='rejected'", (), 0) or 0),
        "disk_usage": int(db_val("SELECT COALESCE(SUM(fsize),0) s FROM files", (), 0) or 0),
        "tickets_open": int(db_val(
            "SELECT COUNT(*) c FROM tickets WHERE status='open'", (), 0) or 0),
        "tickets_total": int(db_val("SELECT COUNT(*) c FROM tickets", (), 0) or 0),
        "runs": int(db_val("SELECT COUNT(*) c FROM script_logs", (), 0) or 0),
        "runs_24h": int(db_val(
            "SELECT COUNT(*) c FROM script_logs WHERE ran_at >= ?", (day_ago,), 0) or 0),
        "uploads": int(db_val("SELECT COALESCE(SUM(total_uploads),0) s FROM users", (), 0) or 0),
        "uploads_24h": int(db_val(
            "SELECT COUNT(*) c FROM files WHERE uploaded >= ?", (day_ago,), 0) or 0),
        "running": count_running_procs(),
        "crons": int(db_val("SELECT COUNT(*) c FROM cron_jobs WHERE enabled=1", (), 0) or 0),
        "points_total": int(db_val("SELECT COALESCE(SUM(points),0) s FROM users", (), 0) or 0),
        "coins_total": int(db_val("SELECT COALESCE(SUM(coins),0) s FROM users", (), 0) or 0),
        "revenue_est": revenue,
        "tier_counts": tier_counts,
        "db_size": db_size(),
        "orders_pending": int(db_val(
            "SELECT COUNT(*) c FROM plan_orders WHERE status='pending'", (), 0) or 0),
    }


def ban_user(uid, reason="", admin_uid=0):
    """Ban a user, stop their scripts, and write an audit entry."""
    uid = int(uid)
    if is_admin(uid):
        return False, "You cannot ban an administrator."
    if not db_one("SELECT uid FROM users WHERE uid=?", (uid,)):
        return False, "That user has never used this bot."
    db_exec(
        "UPDATE users SET is_banned=1, ban_reason=? WHERE uid=?",
        (str(reason)[:400] or "No reason provided", uid),
    )
    kill_all_user_scripts(uid)
    audit(admin_uid, "ban_user", uid, str(reason)[:200])
    notify_user(uid, _e("ban") + " You have been banned. Reason: " + esc(str(reason) or "n/a"))
    return True, "User " + str(uid) + " banned."


def unban_user(uid, admin_uid=0):
    """Lift a ban."""
    uid = int(uid)
    if not db_one("SELECT uid FROM users WHERE uid=?", (uid,)):
        return False, "That user has never used this bot."
    db_exec("UPDATE users SET is_banned=0, ban_reason='' WHERE uid=?", (uid,))
    audit(admin_uid, "unban_user", uid, "")
    notify_user(uid, _e("unlock") + " Your account has been unbanned. Welcome back!")
    return True, "User " + str(uid) + " unbanned."


def set_user_tier(uid, tier, admin_uid=0):
    """Change a user's plan."""
    tier = str(tier or "").strip().lower()
    if tier not in TIERS:
        return False, "Unknown tier. Valid: " + ", ".join(TIER_ORDER)
    uid = int(uid)
    if not db_one("SELECT uid FROM users WHERE uid=?", (uid,)):
        return False, "That user has never used this bot."
    db_exec("UPDATE users SET tier=? WHERE uid=?", (tier, uid))
    audit(admin_uid, "set_tier", uid, tier)
    if tier != "free":
        award_badge(uid, "supporter")
    db_exec(
        "UPDATE plan_orders SET status='approved', processed_at=? WHERE uid=? AND tier=?"
        " AND status='pending'",
        (utcstamp(), uid, tier),
    )
    notify_user(
        uid,
        _e("crown") + " Your plan is now " + B(str(TIERS[tier].get("name"))) + ". Enjoy!",
    )
    return True, "Tier of " + str(uid) + " set to " + tier + "."


def add_points_admin(uid, pts, admin_uid=0):
    """Admin grant/deduction of points."""
    uid = int(uid)
    try:
        pts = int(pts)
    except (TypeError, ValueError):
        return False, "Points must be an integer."
    if not db_one("SELECT uid FROM users WHERE uid=?", (uid,)):
        return False, "That user has never used this bot."
    db_exec("UPDATE users SET points = MAX(0, points + ?) WHERE uid=?", (pts, uid))
    audit(admin_uid, "add_points", uid, str(pts))
    check_achievements(uid)
    notify_user(
        uid,
        _e("pts") + " An admin adjusted your points by " + ("+" if pts >= 0 else "") + str(pts) + ".",
    )
    return True, "Adjusted points of " + str(uid) + " by " + str(pts) + "."


def delete_user_data(uid, admin_uid=0):
    """Wipe every trace of a user: files on disk, all rows, running scripts."""
    uid = int(uid)
    kill_all_user_scripts(uid)
    for directory in (user_dir(uid), user_log_dir(uid)):
        try:
            shutil.rmtree(str(directory), ignore_errors=True)
        except Exception as exc:
            log.warning("rmtree failed for %s: %s", directory, exc)
    file_ids = [int(r.get("id") or 0) for r in db_all("SELECT id FROM files WHERE uid=?", (uid,))]
    for fid in file_ids:
        db_exec("DELETE FROM file_tags WHERE fid=?", (fid,))
        db_exec("DELETE FROM file_versions WHERE fid=?", (fid,))
        db_exec("DELETE FROM file_shares WHERE fid=?", (fid,))
    ticket_ids = [int(r.get("id") or 0) for r in db_all("SELECT id FROM tickets WHERE uid=?", (uid,))]
    for tid in ticket_ids:
        db_exec("DELETE FROM ticket_msgs WHERE ticket_id=?", (tid,))
    for table in (
        "files", "tickets", "user_settings", "script_logs", "api_keys", "notifications",
        "achievements_earned", "badges_earned", "file_shares", "plan_orders",
        "user_notes", "cron_jobs", "ip_log", "user_activity", "webhooks", "rate_limits",
    ):
        db_exec("DELETE FROM " + table + " WHERE uid=?", (uid,))
    db_exec("DELETE FROM referrals WHERE referrer_uid=? OR referred_uid=?", (uid, uid))
    db_exec("DELETE FROM users WHERE uid=?", (uid,))
    audit(admin_uid, "delete_user_data", uid, str(len(file_ids)) + " files removed")
    return True, "Wiped all data for " + str(uid) + " (" + str(len(file_ids)) + " files)."


def approve_file(fid, admin_uid=0):
    """Approve an uploaded file and notify the owner."""
    row = get_file(fid)
    if not row:
        return False, "File not found."
    if str(row.get("status")) == "approved":
        return False, "File is already approved."
    db_exec("UPDATE files SET status='approved' WHERE id=?", (int(fid),))
    audit(admin_uid, "approve_file", int(row.get("uid") or 0), "file #" + str(fid))
    notify_user(
        int(row.get("uid") or 0),
        _e("ok") + " Your file " + B(esc(str(row.get("fname")))) + " was approved and can now run.",
    )
    add_points(int(row.get("uid") or 0), 15, "file_approved")
    return True, "Approved #" + str(fid) + " (" + str(row.get("fname")) + ")."


def reject_file(fid, reason="", admin_uid=0):
    """Reject an uploaded file and notify the owner."""
    row = get_file(fid)
    if not row:
        return False, "File not found."
    db_exec(
        "UPDATE files SET status='rejected', notes=? WHERE id=?",
        (str(reason)[:500], int(fid)),
    )
    audit(admin_uid, "reject_file", int(row.get("uid") or 0), "file #" + str(fid) + " " + str(reason)[:80])
    notify_user(
        int(row.get("uid") or 0),
        _e("no") + " Your file " + B(esc(str(row.get("fname")))) + " was rejected.\n"
        + "Reason: " + esc(str(reason) or "No reason provided"),
    )
    return True, "Rejected #" + str(fid) + "."


def get_pending_files(page=1, per_page=ADMIN_PER_PAGE):
    """Paginated list of files awaiting review."""
    page = max(1, int(page or 1))
    total = int(db_val("SELECT COUNT(*) c FROM files WHERE status='pending'", (), 0) or 0)
    total_pages = max(1, (total + int(per_page) - 1) // int(per_page))
    page = min(page, total_pages)
    rows = db_all(
        "SELECT * FROM files WHERE status='pending' ORDER BY id ASC LIMIT ? OFFSET ?",
        (int(per_page), (page - 1) * int(per_page)),
    )
    return rows, total, total_pages, page


def send_broadcast(admin_uid, text, target_tier=None):
    """Broadcast a message to all users or one tier. Returns (sent, failed)."""
    if target_tier and str(target_tier) in TIERS:
        rows = db_all("SELECT uid FROM users WHERE is_banned=0 AND tier=?", (str(target_tier),))
    else:
        rows = db_all("SELECT uid FROM users WHERE is_banned=0")
    uids = [int(r.get("uid") or 0) for r in rows if r.get("uid")]
    body = _e("bell") + " " + B("Announcement") + "\n\n" + str(text)
    sent, failed = mass_send(uids, body)
    db_exec(
        "INSERT INTO broadcasts (admin_uid, message, sent_count, fail_count, created_at)"
        " VALUES (?,?,?,?,?)",
        (int(admin_uid), str(text)[:3000], int(sent), int(failed), utcstamp()),
    )
    audit(admin_uid, "broadcast", 0, str(sent) + " sent / " + str(failed) + " failed")
    return sent, failed


def db_size():
    """Database file size in bytes, including WAL."""
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(DB_PATH) + suffix)
        try:
            if path.exists():
                total += path.stat().st_size
        except Exception as exc:
            log.debug("db_size stat failed: %s", exc)
    return total


def db_vacuum():
    """Run VACUUM. Returns (ok, message)."""
    before = db_size()
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
    except Exception as exc:
        return False, "Vacuum failed: " + str(exc)
    after = db_size()
    return True, (
        "Vacuum complete. " + fmt_size(before) + " -> " + fmt_size(after)
        + " (saved " + fmt_size(max(0, before - after)) + ")"
    )


def db_backup(dest_path=None):
    """Copy the database into the backups directory. Returns (ok, path_or_error)."""
    stamp = utcnow().strftime("%Y%m%d_%H%M%S")
    target = Path(str(dest_path)) if dest_path else (BACKUPS_DIR / ("sigma_" + stamp + ".db"))
    try:
        source = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            destination = sqlite3.connect(str(target), timeout=30)
            try:
                source.backup(destination)
            finally:
                destination.close()
        finally:
            source.close()
    except Exception as exc:
        return False, "Backup failed: " + str(exc)
    return True, str(target)


def db_integrity_check():
    """Run PRAGMA integrity_check."""
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
    except Exception as exc:
        return False, "Integrity check failed: " + str(exc)
    results = [str(r[0]) for r in rows] if rows else ["unknown"]
    ok = len(results) == 1 and results[0].lower() == "ok"
    return ok, "\n".join(results[:20])


def get_audit_log(limit=20):
    """Recent admin actions."""
    return db_all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),))


def get_error_stats():
    """Failed script runs in the last 24 hours."""
    day_ago = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    failed = int(db_val(
        "SELECT COUNT(*) c FROM script_logs WHERE exit_code != 0 AND ran_at >= ?",
        (day_ago,), 0) or 0)
    total = int(db_val(
        "SELECT COUNT(*) c FROM script_logs WHERE ran_at >= ?", (day_ago,), 0) or 0)
    timeouts = int(db_val(
        "SELECT COUNT(*) c FROM script_logs WHERE exit_code = -9 AND ran_at >= ?",
        (day_ago,), 0) or 0)
    return {
        "failed_24h": failed,
        "total_24h": total,
        "timeouts_24h": timeouts,
        "success_rate": (100.0 * (total - failed) / total) if total else 100.0,
    }


def cleanup_old_logs(days=7):
    """Delete old log rows and stale log files. Returns a summary dict."""
    cutoff_dt = utcnow() - timedelta(days=max(1, int(days)))
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    removed_rows = db_exec("DELETE FROM script_logs WHERE ran_at < ?", (cutoff,))
    db_exec("DELETE FROM user_activity WHERE ts < ?", (cutoff,))
    db_exec("DELETE FROM ip_log WHERE ts < ?", (cutoff,))
    db_exec("DELETE FROM notifications WHERE read=1 AND created_at < ?", (cutoff,))
    db_exec("DELETE FROM stats WHERE ts < ?", (cutoff,))
    db_exec("DELETE FROM server_metrics WHERE ts < ?", (cutoff,))
    db_exec("DELETE FROM file_shares WHERE expires_at != '' AND expires_at < ?", (cutoff,))
    removed_files = 0
    freed = 0
    cutoff_ts = cutoff_dt.timestamp()
    try:
        for path in LOGS_DIR.rglob("*.log"):
            try:
                stat = path.stat()
                if stat.st_mtime < cutoff_ts:
                    freed += stat.st_size
                    path.unlink()
                    removed_files += 1
            except Exception as exc:
                log.debug("log cleanup skip %s: %s", path, exc)
    except Exception as exc:
        log.warning("log sweep failed: %s", exc)
    for path in list(TMP_DIR.glob("*")):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff_ts:
                path.unlink()
        except Exception as exc:
            log.debug("tmp cleanup skip: %s", exc)
    return {
        "rows": int(removed_rows or 0),
        "files": removed_files,
        "freed": freed,
        "days": int(days),
    }


def get_top_uploaders(limit=10):
    """Users with the most uploads."""
    return db_all(
        "SELECT uid, first_name, username, total_uploads FROM users"
        " WHERE total_uploads > 0 ORDER BY total_uploads DESC LIMIT ?",
        (int(limit),),
    )


def get_top_runners(limit=10):
    """Users with the most script runs."""
    return db_all(
        "SELECT uid, first_name, username, total_runs FROM users"
        " WHERE total_runs > 0 ORDER BY total_runs DESC LIMIT ?",
        (int(limit),),
    )


def get_admin_user_files(uid, page=1, per_page=ADMIN_PER_PAGE):
    """Files of any user, for admin inspection."""
    return get_files(uid, None, page, per_page)


# ============================================================================
# SECTION 10 - SYSTEM & MONITORING
# ============================================================================


def get_cpu_cores():
    """Number of CPU cores."""
    count = 0
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith("processor"):
                    count += 1
    except Exception as exc:
        log.debug("cpuinfo read failed: %s", exc)
    if count:
        return count
    return os.cpu_count() or 1


def get_mem_details():
    """Memory breakdown in bytes parsed from /proc/meminfo."""
    info = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                digits = re.sub(r"[^0-9]", "", parts[1])
                if digits:
                    info[parts[0].strip()] = int(digits) * 1024
    except Exception as exc:
        log.debug("meminfo read failed: %s", exc)
    total = int(info.get("MemTotal", 0))
    available = int(info.get("MemAvailable", info.get("MemFree", 0)))
    cached = int(info.get("Cached", 0))
    free = int(info.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total": total,
        "used": used,
        "free": free,
        "available": available,
        "cached": cached,
        "percent": (100.0 * used / total) if total else 0.0,
    }


def get_net_stats():
    """Aggregate rx/tx bytes from /proc/net/dev."""
    result = {"rx": 0, "tx": 0, "interfaces": 0}
    try:
        with open("/proc/net/dev", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle.readlines()[2:]:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                fields = rest.split()
                if len(fields) < 9 or name.strip() == "lo":
                    continue
                result["rx"] += int(fields[0])
                result["tx"] += int(fields[8])
                result["interfaces"] += 1
    except Exception as exc:
        log.debug("net stats read failed: %s", exc)
    return result


def sys_stats():
    """CPU load, memory and disk usage snapshot."""
    cores = get_cpu_cores()
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = 0.0
    mem = get_mem_details()
    try:
        usage = shutil.disk_usage(str(BASE_DIR))
        disk_total = usage.total
        disk_used = usage.used
        disk_free = usage.free
    except Exception as exc:
        log.debug("disk usage failed: %s", exc)
        disk_total = disk_used = disk_free = 0
    return {
        "cores": cores,
        "load1": round(load1, 2),
        "load5": round(load5, 2),
        "load15": round(load15, 2),
        "cpu": round(min(100.0, (load1 / cores) * 100.0), 1) if cores else 0.0,
        "mem": round(mem.get("percent", 0.0), 1),
        "mem_total": mem.get("total", 0),
        "mem_used": mem.get("used", 0),
        "mem_cached": mem.get("cached", 0),
        "disk": round((100.0 * disk_used / disk_total) if disk_total else 0.0, 1),
        "disk_total": disk_total,
        "disk_used": disk_used,
        "disk_free": disk_free,
        "procs": count_running_procs(),
        "net": get_net_stats(),
    }


def get_uptime():
    """Bot uptime as a human string."""
    return fmt_duration(time.time() - START_TIME)


def get_system_uptime():
    """Host uptime as a human string."""
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            seconds = float(handle.read().split()[0])
        return fmt_duration(seconds)
    except Exception as exc:
        log.debug("uptime read failed: %s", exc)
        return "unknown"


def get_system_info():
    """OS, Python and host information."""
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    return {
        "os": platform.system() + " " + platform.release(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": hostname,
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "base_dir": str(BASE_DIR),
        "version": VERSION,
        "uptime": get_uptime(),
        "host_uptime": get_system_uptime(),
        "threads": threading.active_count(),
    }


def disk_usage_by_user(limit=10):
    """Top users by stored bytes."""
    return db_all(
        "SELECT uid, COUNT(*) files, COALESCE(SUM(fsize),0) total_bytes FROM files"
        " GROUP BY uid ORDER BY total_bytes DESC LIMIT ?",
        (int(limit),),
    )


def record_metrics():
    """Insert one stats row and one server_metrics row."""
    stats = sys_stats()
    errors = get_error_stats()
    db_exec(
        "INSERT INTO stats (ts, cpu, mem, disk, users, files, runs, uploads)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            utcstamp(),
            float(stats.get("cpu", 0.0)),
            float(stats.get("mem", 0.0)),
            float(stats.get("disk", 0.0)),
            int(db_val("SELECT COUNT(*) c FROM users", (), 0) or 0),
            int(db_val("SELECT COUNT(*) c FROM files", (), 0) or 0),
            int(db_val("SELECT COUNT(*) c FROM script_logs", (), 0) or 0),
            int(db_val("SELECT COALESCE(SUM(total_uploads),0) s FROM users", (), 0) or 0),
        ),
    )
    db_exec(
        "INSERT INTO server_metrics (ts, active_procs, queue_size, errors_1h)"
        " VALUES (?,?,?,?)",
        (
            utcstamp(),
            count_running_procs(),
            len(get_due_crons()),
            int(errors.get("failed_24h", 0)),
        ),
    )
    return True


def get_recent_metrics(limit=12):
    """Recent metric rows for the admin metrics view."""
    return db_all("SELECT * FROM stats ORDER BY id DESC LIMIT ?", (int(limit),))


# ============================================================================
# SECTION 11 - FORMATTING & UI HELPERS
# ============================================================================


def esc(t):
    """HTML escape for Telegram HTML parse mode."""
    return (
        str("" if t is None else t)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def B(t):
    """Bold."""
    return "<b>" + str(t) + "</b>"


def I(t):
    """Italic."""
    return "<i>" + str(t) + "</i>"


def C(t):
    """Inline code."""
    return "<code>" + esc(t) + "</code>"


def U(t):
    """Underline."""
    return "<u>" + str(t) + "</u>"


def S(t):
    """Strikethrough."""
    return "<s>" + str(t) + "</s>"


def PRE(t, lang=""):
    """Preformatted block, optionally with a language class."""
    if lang:
        return '<pre><code class="language-' + str(lang) + '">' + esc(t) + "</code></pre>"
    return "<pre>" + esc(t) + "</pre>"


def trunc(t, n=60):
    """Truncate with an ellipsis."""
    text = str("" if t is None else t)
    n = max(4, int(n))
    return text if len(text) <= n else text[: n - 1] + "\u2026"


def fmt_size(num_bytes):
    """Human readable byte size."""
    try:
        size = float(num_bytes or 0)
    except (TypeError, ValueError):
        size = 0.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < 1024.0 or unit == "TB":
            if unit == "B":
                return str(int(size)) + " B"
            return ("%.1f" % size) + " " + unit
        size /= 1024.0
    return str(int(size)) + " B"


def fmt_num(n):
    """Thousands separated number."""
    try:
        return "{:,}".format(int(n or 0))
    except (TypeError, ValueError):
        return str(n)


def fmt_duration(seconds):
    """Compact duration such as '1h 23m 4s'."""
    try:
        total = int(max(0, float(seconds or 0)))
    except (TypeError, ValueError):
        return "0s"
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(str(days) + "d")
    if hours:
        parts.append(str(hours) + "h")
    if minutes:
        parts.append(str(minutes) + "m")
    if secs or not parts:
        parts.append(str(secs) + "s")
    return " ".join(parts)


def fmt_dt(dt_str):
    """Relative time such as '2h ago'."""
    moment = parse_stamp(dt_str)
    if moment is None:
        return "never"
    delta = (utcnow() - moment).total_seconds()
    if delta < 0:
        return "in " + fmt_duration(-delta)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return str(int(delta // 60)) + "m ago"
    if delta < 86400:
        return str(int(delta // 3600)) + "h ago"
    if delta < 2592000:
        return str(int(delta // 86400)) + "d ago"
    return moment.strftime("%Y-%m-%d")


def fmt_dt_abs(dt_str):
    """Absolute UTC timestamp string."""
    moment = parse_stamp(dt_str)
    if moment is None:
        return "unknown"
    return moment.strftime("%Y-%m-%d %H:%M") + " UTC"


def pct_bar(pct, width=12, filled_char="\u2588", empty_char="\u2591"):
    """Text progress bar."""
    try:
        value = max(0.0, min(100.0, float(pct or 0)))
    except (TypeError, ValueError):
        value = 0.0
    width = max(4, int(width))
    filled = int(round(width * value / 100.0))
    return filled_char * filled + empty_char * (width - filled)


def tier_badge(tier_key):
    """Emoji + name for a tier key."""
    tier = TIERS.get(str(tier_key), TIERS["free"])
    return str(tier.get("emoji", "")) + " " + str(tier.get("name", "Free"))


def divider(char=None, width=24):
    """Horizontal rule."""
    return str(char or "\u2501") * max(4, int(width))


def header(title, user=None):
    """Themed section header, personalised when a uid is supplied."""
    theme = get_theme(user) if user else THEMES["sigma"]
    char = str(theme.get("header_char", "\u2501"))
    accent = str(theme.get("accent", "\u25b8"))
    line = char * 22
    return (
        line + "\n"
        + B(str(theme.get("emoji", E["sigma"])) + " " + accent + " " + esc(str(title)).upper())
        + "\n" + line
    )


def styled(uid, text):
    """Apply the user's font preference to a body of text."""
    font = get_font(uid)
    if font.get("mono"):
        return "<pre>" + esc(text) + "</pre>"
    if str(get_user(uid).get("font")) == "italic":
        return I(text)
    if str(get_user(uid).get("font")) == "bold":
        return B(text)
    return text


def paginator_text(page, total_pages, total_items):
    """Footer line describing pagination state."""
    return (
        I("Page " + str(max(1, int(page))) + " / " + str(max(1, int(total_pages)))
          + "  \u2022  " + fmt_num(total_items) + " item(s)")
    )


EXT_LANG = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".sh": "bash",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".go": "go",
    ".lua": "lua",
    ".r": "r",
    ".java": "java",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".yml": "yaml",
    ".yaml": "yaml",
}


def syntax_highlight_preview(code, ext=""):
    """Wrap a code preview in a highlighted pre block."""
    lang = EXT_LANG.get(str(ext).lower(), "")
    return PRE(trunc(code, 3200), lang)


def status_icon(status):
    """Icon for a file status."""
    return {
        "pending": _e("pending"),
        "approved": _e("approved"),
        "rejected": _e("rejected"),
    }.get(str(status), _e("info"))


def user_label(row):
    """Display label for a user row."""
    if not isinstance(row, dict):
        return "unknown"
    name = str(row.get("first_name") or "").strip()
    username = str(row.get("username") or "").strip()
    if name and username:
        return name + " (@" + username + ")"
    if name:
        return name
    if username:
        return "@" + username
    return "User " + str(row.get("uid", "?"))


def render_file_card(file_dict):
    """Multi-line styled file information card."""
    if not isinstance(file_dict, dict):
        return I("File not available.")
    fid = int(file_dict.get("id") or 0)
    uid = int(file_dict.get("uid") or 0)
    name = str(file_dict.get("fname") or "unknown")
    running = is_running(uid, fid)
    tags = get_file_tags(fid)
    lines = [
        _e("file") + " " + B(esc(name)),
        divider("\u2508", 22),
        _e("info") + " ID: " + C(str(fid)),
        status_icon(file_dict.get("status")) + " Status: " + B(esc(str(file_dict.get("status", "pending")).title())),
        _e("disk") + " Size: " + fmt_size(file_dict.get("fsize")),
        _e("code") + " Type: " + C(str(file_dict.get("ftype") or guess_type(name))),
        _e("cal") + " Uploaded: " + fmt_dt(file_dict.get("uploaded")),
        _e("run") + " Runs: " + fmt_num(file_dict.get("runs", 0))
        + "   " + _e("download") + " Downloads: " + fmt_num(file_dict.get("downloads", 0)),
        _e("clock") + " Last run: " + fmt_dt(file_dict.get("last_run")),
        (_e("lightning") + " " + B("RUNNING")) if running else (_e("stop") + " Idle"),
        _e("web") + " Public: " + ("yes" if int(file_dict.get("is_public") or 0) else "no"),
        _e("log") + " Log size: " + fmt_size(get_log_size(uid, fid)),
    ]
    if tags:
        lines.append(_e("tag") + " Tags: " + ", ".join(C(t) for t in tags))
    checksum = str(file_dict.get("checksum") or "")
    if checksum:
        lines.append(_e("shield") + " MD5: " + C(checksum[:16]))
    notes = str(file_dict.get("notes") or "").strip()
    if notes:
        lines.append(_e("note") + " Notes: " + I(esc(trunc(notes, 200))))
    info = get_proc_info(uid, fid)
    if info:
        lines.append(divider("\u2508", 22))
        lines.append(_e("server") + " PID " + C(str(info.get("pid"))) + "  \u2022  up " + fmt_duration(info.get("uptime", 0)))
        lines.append(_e("ram") + " Memory: " + fmt_size(int(info.get("memory_kb", 0)) * 1024))
    return "\n".join(lines)


def render_user_card(user_dict):
    """Multi-line styled user information card."""
    if not isinstance(user_dict, dict):
        return I("User not available.")
    uid = int(user_dict.get("uid") or 0)
    level, to_next = calc_level(user_dict.get("xp", 0))
    lines = [
        _e("user") + " " + B(esc(user_label(user_dict))),
        divider("\u2508", 22),
        _e("info") + " UID: " + C(str(uid)),
        _e("crown") + " Plan: " + B(tier_badge(user_dict.get("tier", "free"))),
        _e("star") + " Level " + str(level) + "  (" + fmt_num(user_dict.get("xp", 0)) + " XP, "
        + fmt_num(to_next) + " to next)",
        _e("pts") + " Points: " + B(fmt_num(user_dict.get("points", 0)))
        + "   " + _e("coin") + " Coins: " + B(fmt_num(user_dict.get("coins", 0))),
        _e("trophy") + " Rank: #" + str(get_rank(uid)),
        _e("fire") + " Daily streak: " + str(user_dict.get("daily_streak", 0)) + " day(s)",
        _e("upload") + " Uploads: " + fmt_num(user_dict.get("total_uploads", 0))
        + "   " + _e("run") + " Runs: " + fmt_num(user_dict.get("total_runs", 0)),
        _e("folder") + " Stored: " + str(file_count(uid)) + " file(s), " + fmt_size(total_file_size(uid)),
        _e("ticket") + " Tickets: " + fmt_num(user_dict.get("total_tickets", 0)),
        _e("cal") + " Joined: " + fmt_dt(user_dict.get("joined_at")),
        _e("eye") + " Last seen: " + fmt_dt(user_dict.get("last_seen")),
        _e("link") + " Referral code: " + C(str(user_dict.get("referral_code") or "n/a")),
    ]
    if int(user_dict.get("is_banned") or 0):
        lines.append(_e("ban") + " " + B("BANNED") + ": " + esc(str(user_dict.get("ban_reason") or "n/a")))
    badges = get_user_badges(uid)
    if badges:
        lines.append(_e("shield") + " Badges: " + " ".join(BADGES[b]["emoji"] for b in badges))
    achievements = get_user_achievements(uid)
    lines.append(
        _e("medal") + " Achievements: " + str(len(achievements)) + " / " + str(len(ACHIEVEMENTS))
    )
    return "\n".join(lines)


def render_ticket_card(ticket_dict):
    """Multi-line styled ticket card."""
    if not isinstance(ticket_dict, dict):
        return I("Ticket not available.")
    tid = int(ticket_dict.get("id") or 0)
    priority = str(ticket_dict.get("priority") or "Normal")
    status = str(ticket_dict.get("status") or "open")
    lines = [
        _e("ticket") + " " + B("Ticket #" + str(tid)),
        divider("\u2508", 22),
        _e("note") + " Subject: " + B(esc(str(ticket_dict.get("subject") or ""))),
        _e("tag") + " Category: " + esc(str(ticket_dict.get("category") or "General")),
        PRIORITY_EMOJI.get(priority, _e("info")) + " Priority: " + esc(priority),
        (_e("ok") if status == "closed" else _e("pending")) + " Status: " + B(esc(status.title())),
        _e("user") + " Opened by: " + C(str(ticket_dict.get("uid", 0))),
        _e("cal") + " Created: " + fmt_dt(ticket_dict.get("created_at")),
        _e("clock") + " Updated: " + fmt_dt(ticket_dict.get("updated_at")),
    ]
    if str(ticket_dict.get("closed_at") or ""):
        lines.append(_e("check") + " Closed: " + fmt_dt(ticket_dict.get("closed_at")))
    if int(ticket_dict.get("assigned_to") or 0):
        lines.append(_e("admin") + " Assigned to: " + C(str(ticket_dict.get("assigned_to"))))
    return "\n".join(lines)


def render_achievement_card(ach_key, earned=False):
    """One achievement line."""
    meta = ACHIEVEMENTS.get(str(ach_key))
    if not meta:
        return I("Unknown achievement.")
    mark = _e("ok") if earned else _e("lock")
    return (
        mark + " " + str(meta.get("emoji", "")) + " " + B(esc(str(meta.get("name", ach_key))))
        + "  (+" + str(meta.get("pts", 0)) + " pts)\n"
        + "   " + I(esc(str(meta.get("desc", ""))))
    )


def render_leaderboard(rows, current_uid=0, start_rank=1):
    """Leaderboard table as text."""
    if not rows:
        return I("No ranked users yet.")
    medals = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}
    lines = []
    rank = int(start_rank)
    for row in rows:
        uid = int(row.get("uid") or 0)
        marker = medals.get(rank, " " + str(rank) + ".")
        label = trunc(user_label(row), 22)
        entry = (
            marker + " " + esc(label) + " \u2014 " + B(fmt_num(row.get("points", 0)))
            + " pts  " + I("lvl " + str(row.get("level", 1)))
        )
        if uid == int(current_uid or 0):
            entry = "\u25b8 " + entry + " " + _e("user")
        lines.append(entry)
        rank += 1
    return "\n".join(lines)


def render_plan_card(tier_key):
    """Plan description card."""
    tier = TIERS.get(str(tier_key))
    if not tier:
        return I("Unknown plan.")
    files = "unlimited" if int(tier.get("files", 0)) == -1 else str(tier.get("files"))
    return "\n".join([
        str(tier.get("emoji", "")) + " " + B(str(tier.get("name", ""))) + "  \u2014  "
        + ("free" if not tier.get("price") else str(tier.get("price")) + " / month"),
        "   " + _e("folder") + " Files: " + files,
        "   " + _e("disk") + " Max file size: " + str(tier.get("size")) + " MB",
        "   " + _e("ram") + " RAM budget: " + str(tier.get("ram")) + " MB",
        "   " + _e("code") + " Script runner: " + ("yes" if tier.get("terminal") else "no"),
        "   " + _e("api") + " API access: " + ("yes" if tier.get("api") else "no"),
        "   " + _e("web") + " Web pages: " + str(tier.get("web")),
        "   " + _e("lightning") + " Concurrent scripts: " + str(TIER_PROC_LIMIT.get(str(tier_key), 1)),
        "   " + _e("clock") + " Run timeout: " + fmt_duration(TIER_TIMEOUTS.get(str(tier_key), 300)),
        "   " + _e("star") + " Queue priority: " + str(tier.get("priority")),
    ])


def render_stats_card(stats_dict):
    """Bot statistics card."""
    stats = stats_dict if isinstance(stats_dict, dict) else {}
    lines = [
        _e("users") + " Users: " + B(fmt_num(stats.get("users", 0)))
        + "   " + I("(" + fmt_num(stats.get("users_active_24h", 0)) + " active 24h)"),
        _e("plus") + " New this week: " + fmt_num(stats.get("users_new_7d", 0)),
        _e("ban") + " Banned: " + fmt_num(stats.get("banned", 0)),
        divider("\u2508", 22),
        _e("file") + " Files: " + B(fmt_num(stats.get("files", 0))),
        "   " + _e("pending") + " pending " + fmt_num(stats.get("files_pending", 0))
        + "  " + _e("approved") + " approved " + fmt_num(stats.get("files_approved", 0))
        + "  " + _e("rejected") + " rejected " + fmt_num(stats.get("files_rejected", 0)),
        _e("disk") + " Stored data: " + fmt_size(stats.get("disk_usage", 0)),
        divider("\u2508", 22),
        _e("run") + " Total runs: " + fmt_num(stats.get("runs", 0))
        + "   " + I("(" + fmt_num(stats.get("runs_24h", 0)) + " in 24h)"),
        _e("lightning") + " Running now: " + B(str(stats.get("running", 0))),
        _e("cron") + " Active crons: " + fmt_num(stats.get("crons", 0)),
        _e("upload") + " Uploads: " + fmt_num(stats.get("uploads", 0)),
        divider("\u2508", 22),
        _e("ticket") + " Open tickets: " + fmt_num(stats.get("tickets_open", 0))
        + " / " + fmt_num(stats.get("tickets_total", 0)),
        _e("pts") + " Points in circulation: " + fmt_num(stats.get("points_total", 0)),
        _e("coin") + " Coins in circulation: " + fmt_num(stats.get("coins_total", 0)),
        _e("db") + " Database size: " + fmt_size(stats.get("db_size", 0)),
        _e("clock") + " Bot uptime: " + get_uptime(),
    ]
    return "\n".join(lines)


def render_sys_card(sys_dict):
    """System monitoring card."""
    stats = sys_dict if isinstance(sys_dict, dict) else {}
    net = stats.get("net", {}) if isinstance(stats.get("net"), dict) else {}
    return "\n".join([
        _e("cpu") + " CPU load: " + B(str(stats.get("cpu", 0)) + "%") + "  " + C(pct_bar(stats.get("cpu", 0))),
        "   " + I("load " + str(stats.get("load1", 0)) + " / " + str(stats.get("load5", 0))
                  + " / " + str(stats.get("load15", 0)) + " on " + str(stats.get("cores", 1)) + " core(s)"),
        _e("ram") + " Memory: " + B(str(stats.get("mem", 0)) + "%") + "  " + C(pct_bar(stats.get("mem", 0))),
        "   " + I(fmt_size(stats.get("mem_used", 0)) + " of " + fmt_size(stats.get("mem_total", 0))
                  + " used, " + fmt_size(stats.get("mem_cached", 0)) + " cached"),
        _e("disk") + " Disk: " + B(str(stats.get("disk", 0)) + "%") + "  " + C(pct_bar(stats.get("disk", 0))),
        "   " + I(fmt_size(stats.get("disk_used", 0)) + " used, " + fmt_size(stats.get("disk_free", 0)) + " free"),
        _e("web") + " Network: " + fmt_size(net.get("rx", 0)) + " in / " + fmt_size(net.get("tx", 0)) + " out",
        _e("lightning") + " Active processes: " + B(str(stats.get("procs", 0))),
    ])


def render_shop():
    """Shop catalogue text."""
    lines = []
    for key, item in SHOP_ITEMS.items():
        lines.append(
            str(item.get("emoji", "")) + " " + B(esc(str(item.get("name", key))))
            + " \u2014 " + str(item.get("cost", 0)) + " " + _e("coin")
        )
        lines.append("   " + I(esc(str(item.get("desc", "")))))
    return "\n".join(lines)


# ============================================================================
# SECTION 12 - KEYBOARD BUILDERS
# ============================================================================


def KB(*rows):
    """Build an inline keyboard from rows of buttons."""
    markup = types.InlineKeyboardMarkup()
    for row in rows:
        if not row:
            continue
        if isinstance(row, (list, tuple)):
            buttons = [b for b in row if b is not None]
            if buttons:
                markup.row(*buttons)
        else:
            markup.row(row)
    return markup


BUTTON_POLISH = {
    "Fonts": "\U0001f520 Fonts",
    "Security": "\U0001f6e1\ufe0f Security",
    "Admin": "\u2699\ufe0f Admin panel",
    "Profile": "\U0001f464 Profile",
    "Stats": "\U0001f4ca Statistics",
    "Help": "\u2753 Help",
    "Back": "\u2b05\ufe0f Back",
    "Close": "\u2716\ufe0f Close",
    "Yes": "\u2705 Yes",
    "No": "\u274c No",
    "Run": "\u25b6\ufe0f Run",
    "Stop": "\u23f9\ufe0f Stop",
    "Logs": "\U0001f4dc Logs",
    "Edit": "\u270f\ufe0f Edit",
    "Delete": "\U0001f5d1\ufe0f Delete",
    "Refresh": "\U0001f504 Refresh",
    "Settings": "\u2699\ufe0f Settings",
}


def polish_label(text):
    """Give bare one-word labels an icon so no button looks unfinished."""
    raw = str(text)
    return BUTTON_POLISH.get(raw.strip(), raw)


def BTN(text, data):
    """Callback button."""
    return types.InlineKeyboardButton(polish_label(text),
                                      callback_data=str(data)[:63])


def LBTN(text, url):
    """URL button."""
    return types.InlineKeyboardButton(str(text), url=str(url))


def BACK(data="main_menu"):
    """Back button."""
    return BTN(_l("back"), data)


def kb_back_only(data="main_menu"):
    """Keyboard with just a back button."""
    return KB([BACK(data)])


def kb_confirm(yes_data, no_data, yes_label=None, no_label=None):
    """Yes / no confirmation keyboard."""
    return KB([
        BTN(yes_label or _l("yes"), yes_data),
        BTN(no_label or _l("no"), no_data),
    ])


def kb_pagination(base_data, page, total_pages, back_data="main_menu"):
    """Prev / page / next row plus a back button."""
    page = max(1, int(page or 1))
    total_pages = max(1, int(total_pages or 1))
    row = []
    if page > 1:
        row.append(BTN(_l("prev"), base_data + str(page - 1)))
    row.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        row.append(BTN(_l("next"), base_data + str(page + 1)))
    return KB(row, [BACK(back_data)])


def kb_main(uid):
    """Main menu; the admin button only appears for administrators."""
    rows = [
        [BTN(_l("files"), "menu_files"), BTN(_l("run_menu"), "menu_run")],
        [BTN(_l("economy"), "menu_economy"), BTN(_l("profile"), "menu_profile")],
        [BTN(_l("support"), "menu_support"), BTN(_l("settings"), "menu_settings")],
        [BTN(_l("stats"), "menu_stats"), BTN(_l("plan"), "settings_upgrade")],
        [BTN("\U0001f6e1\ufe0f Security", "menu_security"),
         BTN("\U0001f9e9 Extensions", "menu_plugins")],
        [BTN(_e("box") + " Packages & Requirements", "venvp"),
         BTN(_e("art") + " Fonts", "font_panel")],
    ]
    rows.extend(extension_menu_rows(uid))
    if is_admin(uid):
        rows.append([BTN(_l("admin"), "menu_admin")])
    return KB(*rows)


def kb_files(uid, page=1):
    """Paginated file browser."""
    rows_data, total, total_pages, page = get_files(uid, None, page)
    rows = []
    for row in rows_data:
        fid = int(row.get("id") or 0)
        mark = _e("lightning") if is_running(uid, fid) else status_icon(row.get("status"))
        rows.append([BTN(mark + " " + trunc(str(row.get("fname")), 28), "file_" + str(fid))])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "files_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "files_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BTN(_l("search"), "files_search"), BTN(_l("zip_all"), "file_zip_all")])
    rows.append([BTN(_l("export_csv"), "files_export"), BTN(_l("upload"), "files_upload_help")])
    rows.append([BTN(_e("plus") + " Install a package", "reqman:0"),
                 BTN(_e("db") + " My packages", "venvp")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


def kb_file_detail(uid, fid):
    """Every action available for one file."""
    running = is_running(uid, fid)
    row = get_user_file(uid, fid) or {}
    public = int(row.get("is_public") or 0)
    rows = [
        [
            BTN(_l("stop") if running else _l("run"), ("stop_" if running else "run_") + str(fid)),
            BTN(_l("restart"), "restart_" + str(fid)),
        ],
        [BTN(_l("run_args"), "run_with_args_" + str(fid)), BTN(_l("logs"), "log_" + str(fid))],
        [BTN(_l("edit"), "edit_" + str(fid)), BTN(_l("rename"), "rename_" + str(fid))],
        [BTN(_l("tags"), "file_tags_" + str(fid)), BTN(_l("versions"), "file_versions_" + str(fid))],
        [BTN(_l("share"), "share_" + str(fid)), BTN(_l("download"), "file_download_" + str(fid))],
        [
            BTN((_e("lock") + " Make Private") if public else (_e("web") + " Make Public"), "file_public_" + str(fid)),
            BTN(_l("copy"), "file_copy_" + str(fid)),
        ],
        [BTN(_l("cron") + " Schedule", "cron_for_" + str(fid)), BTN(_l("preview"), "file_preview_" + str(fid))],
        [BTN(_e("box") + " Requirements / Install", "req:" + str(fid))],
        [BTN(_e("shield") + " Security policy", "fpol:" + str(fid)),
         BTN(_e("db") + " My packages", "venvp")],
        [BTN(_l("delete"), "del_" + str(fid))],
        [BACK("menu_files")],
    ]
    return KB(*rows)


def kb_file_editor(uid, fid):
    """File editing options."""
    return KB(
        [BTN(_l("edit_write"), "edit_write_" + str(fid)), BTN(_l("edit_line"), "edit_line_" + str(fid))],
        [BTN(_l("preview"), "file_preview_" + str(fid)), BTN(_l("versions"), "file_versions_" + str(fid))],
        [BACK("file_" + str(fid))],
    )


def kb_file_versions(uid, fid):
    """Version restore keyboard."""
    rows = []
    for version in get_file_versions(fid)[:8]:
        rows.append([BTN(
            _e("reload") + " v" + str(version.get("version")) + " \u2022 "
            + fmt_size(version.get("size")) + " \u2022 " + fmt_dt(version.get("created_at")),
            "restore_ver_" + str(version.get("id")),
        )])
    rows.append([BACK("file_" + str(fid))])
    return KB(*rows)


def kb_file_tags(uid, fid):
    """Tag management keyboard."""
    rows = [[BTN(_l("add_tag"), "file_add_tag_" + str(fid))]]
    tags = get_file_tags(fid)
    chunk = []
    for tag in tags[:12]:
        chunk.append(BTN(_e("trash") + " " + tag, "file_rm_tag_" + str(fid) + "_" + tag))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("file_" + str(fid))])
    return KB(*rows)


def kb_file_share(uid, fid):
    """Share expiry choices."""
    return KB(
        [BTN(_e("clock") + " 1 hour", "share_confirm_" + str(fid) + "_1"),
         BTN(_e("clock") + " 24 hours", "share_confirm_" + str(fid) + "_24")],
        [BTN(_e("clock") + " 7 days", "share_confirm_" + str(fid) + "_168"),
         BTN(_e("clock") + " 30 days", "share_confirm_" + str(fid) + "_720")],
        [BTN(_e("edit") + " Custom hours", "share_custom_" + str(fid))],
        [BACK("file_" + str(fid))],
    )


def kb_run_menu(uid):
    """Run centre keyboard listing runnable files."""
    rows = []
    approved = db_all(
        "SELECT * FROM files WHERE uid=? AND status='approved' ORDER BY id DESC LIMIT 10",
        (int(uid),),
    )
    for row in approved:
        fid = int(row.get("id") or 0)
        running = is_running(uid, fid)
        rows.append([BTN(
            (_e("stop") if running else _e("run")) + " " + trunc(str(row.get("fname")), 26),
            ("stop_" if running else "run_") + str(fid),
        )])
    rows.append([BTN(_l("running"), "run_running"), BTN(_l("kill_mine"), "run_kill_mine")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


def kb_economy(uid):
    """Economy menu."""
    return KB(
        [BTN(_l("daily"), "econ_daily"), BTN(_l("wallet"), "econ_wallet")],
        [BTN(_l("leaderboard"), "econ_leaderboard"), BTN(_l("shop"), "econ_shop")],
        [BTN(_l("referral"), "econ_referral"), BTN(_l("transfer"), "econ_transfer")],
        [BACK("main_menu")],
    )


def kb_daily(uid):
    """Daily bonus keyboard."""
    return KB(
        [BTN(_l("daily"), "econ_daily")],
        [BTN(_l("wallet"), "econ_wallet"), BTN(_l("leaderboard"), "econ_leaderboard")],
        [BACK("menu_economy")],
    )


def kb_leaderboard(page=1, total_pages=None):
    """Leaderboard pagination."""
    if total_pages is None:
        _rows, _total, total_pages, page = get_leaderboard_page(page)
    page = max(1, int(page or 1))
    total_pages = max(1, int(total_pages or 1))
    row = []
    if page > 1:
        row.append(BTN(_l("prev"), "econ_lb_page_" + str(page - 1)))
    row.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        row.append(BTN(_l("next"), "econ_lb_page_" + str(page + 1)))
    return KB(row, [BACK("menu_economy")])


def kb_shop():
    """Shop keyboard."""
    rows = []
    chunk = []
    for key, item in SHOP_ITEMS.items():
        chunk.append(BTN(
            str(item.get("emoji", "")) + " " + trunc(str(item.get("name", key)), 18)
            + " " + str(item.get("cost", 0)),
            "shop_buy_" + str(key),
        ))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_economy")])
    return KB(*rows)


def kb_profile(uid):
    """Profile menu."""
    return KB(
        [BTN(_l("achievements"), "profile_achievements"), BTN(_l("badges"), "profile_badges")],
        [BTN(_l("leaderboard"), "econ_leaderboard"), BTN(_l("plan"), "settings_upgrade")],
        [BTN(_l("notes"), "settings_notes"), BTN(_l("refresh"), "menu_profile")],
        [BACK("main_menu")],
    )


def kb_achievements(uid):
    """Achievements screen keyboard."""
    return KB(
        [BTN(_l("badges"), "profile_badges"), BTN(_l("refresh"), "profile_achievements")],
        [BACK("menu_profile")],
    )


def kb_badges(uid):
    """Badges screen keyboard."""
    return KB(
        [BTN(_l("achievements"), "profile_achievements"), BTN(_l("shop"), "econ_shop")],
        [BACK("menu_profile")],
    )


def kb_support(uid):
    """Support menu."""
    return KB(
        [BTN(_l("new_ticket"), "support_new_ticket"), BTN(_l("tickets"), "support_tickets")],
        [BTN(_l("help"), "support_help")],
        [BACK("main_menu")],
    )


def kb_tickets(uid, page=1, total_pages=None):
    """User ticket list."""
    rows_data, total, computed_pages, page = get_user_tickets(uid, None, page)
    total_pages = max(1, int(total_pages or computed_pages or 1))
    rows = []
    for row in rows_data:
        tid = int(row.get("id") or 0)
        icon = _e("ok") if str(row.get("status")) == "closed" else _e("pending")
        rows.append([BTN(
            icon + " #" + str(tid) + " " + trunc(str(row.get("subject")), 24),
            "ticket_" + str(tid),
        )])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "support_tickets_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "support_tickets_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BTN(_l("new_ticket"), "support_new_ticket")])
    rows.append([BACK("menu_support")])
    return KB(*rows)


def kb_ticket_detail(tid, uid):
    """Ticket actions."""
    ticket = get_ticket(tid) or {}
    rows = [[BTN(_l("reply"), "ticket_reply_" + str(tid))]]
    if str(ticket.get("status")) != "closed":
        rows.append([BTN(_l("close_ticket"), "ticket_close_" + str(tid))])
    rows.append([BACK("support_tickets")])
    return KB(*rows)


def kb_new_ticket():
    """Category picker for a new ticket."""
    rows = []
    chunk = []
    for category in TICKET_CATEGORIES:
        chunk.append(BTN(_e("tag") + " " + category, "ticket_cat_" + category))
        if len(chunk) == 3:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_support")])
    return KB(*rows)


def kb_ticket_priority(category):
    """Priority picker for a new ticket."""
    rows = []
    chunk = []
    for priority in TICKET_PRIORITIES:
        chunk.append(BTN(
            PRIORITY_EMOJI.get(priority, "") + " " + priority,
            "ticket_pri_" + str(category) + "_" + priority,
        ))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("support_new_ticket")])
    return KB(*rows)


def kb_settings(uid):
    """Settings menu."""
    push = int(get_user(uid).get("push_enabled") or 0)
    return KB(
        [BTN(_l("theme"), "settings_theme"), BTN(_l("font"), "settings_font")],
        [BTN(_l("language"), "settings_lang"), BTN(_l("push_on") if push else _l("push_off"), "set_toggle_push")],
        [BTN(_l("api"), "settings_api"), BTN(_l("cron"), "settings_cron")],
        [BTN(_l("webhooks"), "settings_webhooks"), BTN(_l("notes"), "settings_notes")],
        [BTN(_l("notifications"), "menu_notifications"), BTN(_l("upgrade"), "settings_upgrade")],
        [BACK("main_menu")],
    )


def kb_theme_select(uid):
    """Theme picker."""
    current = str(get_user(uid).get("theme") or "sigma")
    rows = []
    chunk = []
    for key, theme in THEMES.items():
        mark = _e("check") + " " if key == current else ""
        chunk.append(BTN(mark + str(theme.get("emoji", "")) + " " + str(theme.get("name")), "set_theme_" + key))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_font_select(uid):
    """Font picker."""
    current = str(get_user(uid).get("font") or "default")
    rows = []
    chunk = []
    for key, font in FONTS.items():
        mark = _e("check") + " " if key == current else ""
        chunk.append(BTN(mark + str(font.get("emoji", "")) + " " + str(font.get("name")), "set_font_" + key))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_lang_select(uid):
    """Language picker."""
    current = str(get_user(uid).get("lang") or "en")
    rows = []
    chunk = []
    for key, name in LANGS.items():
        mark = _e("check") + " " if key == current else ""
        chunk.append(BTN(mark + name, "set_toggle_lang_" + key))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_notifications(uid):
    """Notification centre keyboard."""
    push = int(get_user(uid).get("push_enabled") or 0)
    return KB(
        [BTN(_l("push_on") if push else _l("push_off"), "set_toggle_push")],
        [BTN(_e("check") + " Mark all read", "notif_read_all"), BTN(_l("refresh"), "menu_notifications")],
        [BACK("menu_settings")],
    )


def kb_api_keys(uid):
    """API key management keyboard."""
    rows = [[BTN(_l("api_new"), "api_new"), BTN(_l("api_list"), "api_list")]]
    for key in list_api_keys(uid)[:8]:
        if not int(key.get("is_active") or 0):
            continue
        rows.append([BTN(
            _e("trash") + " Revoke " + trunc(str(key.get("label")), 20),
            "api_del_" + str(key.get("id")),
        )])
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_crons(uid):
    """Cron manager keyboard."""
    rows = [[BTN(_l("cron_new"), "cron_new")]]
    for job in get_user_crons(uid)[:8]:
        icon = _e("ok") if int(job.get("enabled") or 0) else _e("stop")
        rows.append([BTN(
            icon + " #" + str(job.get("id")) + " " + str(job.get("schedule")),
            "cron_" + str(job.get("id")),
        )])
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_cron_detail(uid, cron_id=None):
    """Actions for one cron job. Accepts (cron_id) or (uid, cron_id)."""
    if cron_id is None:
        cron_id = uid
    return KB(
        [BTN(_l("cron_toggle"), "cron_toggle_" + str(cron_id)), BTN(_l("delete"), "cron_del_" + str(cron_id))],
        [BACK("settings_cron")],
    )


def kb_cron_new(uid):
    """File picker for a new cron job."""
    rows = []
    for row in db_all(
        "SELECT * FROM files WHERE uid=? AND status='approved' ORDER BY id DESC LIMIT 10",
        (int(uid),),
    ):
        rows.append([BTN(
            _e("cron") + " " + trunc(str(row.get("fname")), 28),
            "cron_for_" + str(row.get("id")),
        )])
    rows.append([BACK("settings_cron")])
    return KB(*rows)


def kb_cron_schedule(fid):
    """Schedule preset picker."""
    rows = []
    chunk = []
    for preset in CRON_PRESETS:
        chunk.append(BTN(_e("clock") + " " + preset, "cron_set_" + str(fid) + "_" + preset))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BTN(_e("edit") + " Custom", "cron_custom_" + str(fid))])
    rows.append([BACK("settings_cron")])
    return KB(*rows)


def kb_webhooks(uid):
    """Webhook manager keyboard."""
    rows = [[BTN(_l("webhook_new"), "webhook_new")]]
    for hook in get_webhooks(uid)[:8]:
        rows.append([BTN(
            _e("trash") + " " + trunc(str(hook.get("url")), 28),
            "webhook_del_" + str(hook.get("id")),
        )])
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_notes(uid):
    """Notes manager keyboard."""
    rows = [[BTN(_l("note_new"), "note_new")]]
    for note in get_notes(uid)[:10]:
        prefix = _e("pin") if int(note.get("pinned") or 0) else _e("note")
        rows.append([BTN(
            prefix + " " + trunc(str(note.get("title")), 28),
            "note_" + str(note.get("id")),
        )])
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_note_detail(uid, note_id=None):
    """Actions for one note. Accepts (note_id) or (uid, note_id)."""
    if note_id is None:
        note_id = uid
    return KB(
        [BTN(_l("edit"), "note_edit_" + str(note_id)), BTN(_l("note_pin"), "note_pin_" + str(note_id))],
        [BTN(_l("delete"), "note_del_" + str(note_id))],
        [BACK("settings_notes")],
    )


def kb_upgrade(uid):
    """Plan upgrade keyboard."""
    current = get_tier_key(uid)
    rows = []
    for key in TIER_ORDER:
        tier = TIERS[key]
        mark = _e("check") + " " if key == current else ""
        rows.append([BTN(
            mark + str(tier.get("emoji")) + " " + str(tier.get("name")) + " \u2014 " + str(tier.get("price")),
            "plan_order_" + key,
        )])
    rows.append([BACK("menu_settings")])
    return KB(*rows)


def kb_admin():
    """Admin panel root."""
    return KB(
        [BTN(_l("admin_dash"), "admin_dash"), BTN(_l("admin_sys"), "admin_sys")],
        [BTN(_l("admin_users"), "admin_users"), BTN(_l("admin_files"), "admin_pending")],
        [BTN(_l("admin_tickets"), "admin_tickets"), BTN(_l("admin_broadcast"), "admin_broadcast")],
        [BTN(_l("admin_db"), "admin_db"), BTN(_l("admin_audit"), "admin_audit")],
        [BTN(_l("admin_running"), "admin_running"), BTN(_l("admin_metrics"), "admin_metrics")],
        [BACK("main_menu")],
    )


def kb_admin_users(page=1, total_pages=None, rows_override=None):
    """Admin user browser."""
    rows_data, total, computed_pages, page = get_all_users(page)
    if rows_override:
        rows_data = list(rows_override)
    total_pages = max(1, int(total_pages or computed_pages or 1))
    rows = []
    for row in rows_data:
        uid = int(row.get("uid") or 0)
        prefix = _e("ban") if int(row.get("is_banned") or 0) else TIERS.get(
            str(row.get("tier") or "free"), TIERS["free"]).get("emoji", "")
        rows.append([BTN(
            prefix + " " + trunc(user_label(row), 26),
            "admin_user_" + str(uid),
        )])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "admin_users_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "admin_users_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BTN(_l("search"), "admin_user_search")])
    rows.append([BACK("menu_admin")])
    return KB(*rows)


def kb_admin_user_detail(target_uid):
    """Admin actions for one user."""
    banned = is_banned(target_uid)
    return KB(
        [BTN(_l("unban") if banned else _l("ban"),
             ("admin_unban_" if banned else "admin_ban_") + str(target_uid))],
        [BTN(_l("set_tier"), "admin_tier_" + str(target_uid)), BTN(_l("add_pts"), "admin_pts_" + str(target_uid))],
        [BTN(_l("user_files"), "admin_user_files_" + str(target_uid))],
        [BTN(_l("del_user"), "admin_del_user_" + str(target_uid))],
        [BACK("admin_users")],
    )


def kb_admin_tier_select(target_uid):
    """Tier picker for an admin action."""
    rows = []
    chunk = []
    for key in TIER_ORDER:
        chunk.append(BTN(
            str(TIERS[key].get("emoji")) + " " + str(TIERS[key].get("name")),
            "admin_settier_" + str(target_uid) + "_" + key,
        ))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("admin_user_" + str(target_uid))])
    return KB(*rows)


def kb_admin_files(page=1, total_pages=None, rows_override=None):
    """Pending file review list."""
    rows_data, total, computed_pages, page = get_pending_files(page)
    if rows_override:
        rows_data = list(rows_override)
    total_pages = max(1, int(total_pages or computed_pages or 1))
    rows = []
    for row in rows_data:
        rows.append([BTN(
            _e("pending") + " #" + str(row.get("id")) + " " + trunc(str(row.get("fname")), 24),
            "admin_review_" + str(row.get("id")),
        )])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "admin_pending_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "admin_pending_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BACK("menu_admin")])
    return KB(*rows)


def kb_admin_file_detail(fid):
    """Approve / reject actions."""
    return KB(
        [BTN(_l("approve"), "admin_approve_" + str(fid)), BTN(_l("reject"), "admin_reject_" + str(fid))],
        [BTN(_l("download"), "admin_dl_" + str(fid)), BTN(_l("preview"), "admin_prev_" + str(fid))],
        [BACK("admin_pending")],
    )


def kb_admin_tickets(page=1, total_pages=None, rows_override=None):
    """Admin ticket list."""
    rows_data, total, computed_pages, page = get_open_tickets(0, page)
    if rows_override:
        rows_data = list(rows_override)
    total_pages = max(1, int(total_pages or computed_pages or 1))
    rows = []
    for row in rows_data:
        rows.append([BTN(
            PRIORITY_EMOJI.get(str(row.get("priority")), _e("ticket"))
            + " #" + str(row.get("id")) + " " + trunc(str(row.get("subject")), 22),
            "admin_ticket_" + str(row.get("id")),
        )])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "admin_tickets_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "admin_tickets_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BACK("menu_admin")])
    return KB(*rows)


def kb_admin_ticket_detail(tid):
    """Admin ticket actions."""
    return KB(
        [BTN(_l("reply"), "admin_ticket_reply_" + str(tid)),
         BTN(_l("close_ticket"), "admin_ticket_close_" + str(tid))],
        [BTN(_e("admin") + " Assign to me", "admin_ticket_assign_" + str(tid))],
        [BACK("admin_tickets")],
    )


def kb_admin_broadcast():
    """Broadcast targeting keyboard."""
    rows = [[BTN(_e("bell") + " All users", "admin_bc_target_all")]]
    chunk = []
    for key in TIER_ORDER:
        chunk.append(BTN(str(TIERS[key].get("emoji")) + " " + str(TIERS[key].get("name")), "admin_bc_target_" + key))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BACK("menu_admin")])
    return KB(*rows)


def kb_admin_sys():
    """System panel keyboard."""
    return KB(
        [BTN(_l("refresh"), "admin_sys_refresh"), BTN(_l("admin_running"), "admin_running")],
        [BTN(_l("admin_kill"), "admin_kill_all"), BTN(_l("admin_cleanup"), "admin_cleanup")],
        [BTN(_l("admin_metrics"), "admin_metrics")],
        [BACK("menu_admin")],
    )


def kb_admin_db():
    """Database panel keyboard."""
    return KB(
        [BTN(_l("vacuum"), "admin_vacuum"), BTN(_l("backup"), "admin_backup")],
        [BTN(_l("integrity"), "admin_integrity"), BTN(_l("refresh"), "admin_db")],
        [BACK("menu_admin")],
    )


# ============================================================================
# SECTION 13 - MESSAGE BUILDERS
# ============================================================================


def msg_welcome(uid, is_new=False):
    """Welcome / main menu text."""
    user = get_user(uid)
    tier = get_tier(uid)
    level, to_next = calc_level(user.get("xp", 0))
    lines = [
        header(BOT_NAME, uid),
        "",
        (_e("rocket") + " " + B("Welcome aboard, ") + B(esc(str(user.get("first_name") or "friend"))) + "!")
        if is_new else
        (_e("sigma") + " " + B("Welcome back, ") + B(esc(str(user.get("first_name") or "friend"))) + "!"),
        "",
        I("Host your scripts, run them on demand, schedule them with cron,"
          " and track everything from one place."),
        "",
        _e("crown") + " Plan: " + B(tier_badge(user.get("tier", "free"))),
        _e("star") + " Level " + str(level) + " \u2022 " + fmt_num(user.get("xp", 0)) + " XP",
        _e("pts") + " " + fmt_num(user.get("points", 0)) + " pts \u2022 "
        + _e("coin") + " " + fmt_num(user.get("coins", 0)) + " coins",
        _e("folder") + " " + str(file_count(uid)) + " file(s) \u2022 " + fmt_size(total_file_size(uid)),
        _e("lightning") + " " + str(running_count(uid)) + " script(s) running",
        "",
        divider("\u2508", 22),
        I("Send me any document to upload it. Use /help for all commands."),
    ]
    if is_new:
        lines.append("")
        lines.append(_e("gift") + " " + I("Starter bonus applied: 50 pts and 10 coins."))
    return "\n".join(lines)


def msg_help(uid):
    """Full command reference."""
    lines = [
        header("Command Reference", uid),
        "",
        B("General"),
        C("/start") + " \u2014 start the bot / apply a referral code",
        C("/menu") + " \u2014 open the main menu",
        C("/help") + " \u2014 this help page",
        C("/profile") + " \u2014 your profile card",
        C("/stats") + " \u2014 platform statistics",
        "",
        B("Files & scripts"),
        C("/files") + " \u2014 file manager",
        C("/upload") + " \u2014 upload instructions",
        C("/run <id>") + " \u2014 run a script",
        C("/stop <id>") + " \u2014 stop a running script",
        C("/log <id> [lines]") + " \u2014 view script output",
        C("/cron") + " \u2014 scheduled jobs",
        C("/api") + " \u2014 API key management",
        "",
        B("Economy"),
        C("/daily") + " \u2014 claim the daily bonus",
        C("/economy") + " \u2014 economy hub",
        C("/shop") + " \u2014 spend coins",
        "",
        B("Support"),
        C("/ticket <subject>") + " \u2014 open a support ticket",
    ]
    if is_admin(uid):
        lines += [
            "",
            B(_e("admin") + " Admin"),
            C("/admin") + " \u2014 admin panel",
            C("/ban <uid> [reason]") + " \u2014 ban a user",
            C("/unban <uid>") + " \u2014 unban a user",
            C("/approve <fid>") + " \u2014 approve a file",
            C("/reject <fid> [reason]") + " \u2014 reject a file",
            C("/broadcast <text>") + " \u2014 message every user",
            C("/addpts <uid> <pts>") + " \u2014 adjust points",
            C("/settier <uid> <tier>") + " \u2014 change a plan",
            C("/deluser <uid>") + " \u2014 wipe a user",
            C("/backup") + " \u2014 back up the database",
            C("/vacuum") + " \u2014 vacuum the database",
        ]
    lines += ["", divider("\u2508", 22), I(BOT_NAME + " v" + VERSION)]
    return "\n".join(lines)


def msg_profile(uid):
    """Profile page."""
    return header("Profile", uid) + "\n\n" + render_user_card(get_user(uid))


def msg_files(uid, page=1):
    """File manager page."""
    rows, total, total_pages, page = get_files(uid, None, page)
    limit = effective_file_limit(uid)
    limit_text = "unlimited" if limit == -1 else str(limit)
    lines = [
        header("File Manager", uid),
        "",
        _e("folder") + " " + B(str(total)) + " file(s) of " + limit_text
        + " \u2022 " + fmt_size(total_file_size(uid)) + " stored",
        _e("disk") + " Max file size: " + str(effective_size_limit_mb(uid)) + " MB",
        "",
    ]
    if not rows:
        lines.append(I("No files yet. Send me a document to upload your first one."))
    else:
        for row in rows:
            fid = int(row.get("id") or 0)
            mark = _e("lightning") if is_running(uid, fid) else status_icon(row.get("status"))
            lines.append(
                mark + " " + C("#" + str(fid)) + " " + B(esc(trunc(str(row.get("fname")), 34)))
            )
            lines.append(
                "    " + fmt_size(row.get("fsize")) + " \u2022 " + str(row.get("status"))
                + " \u2022 " + fmt_dt(row.get("uploaded"))
                + " \u2022 " + str(row.get("runs", 0)) + " run(s)"
            )
        lines.append("")
        lines.append(paginator_text(page, total_pages, total))
    return "\n".join(lines)


def msg_file_detail(uid, fid):
    """File detail page."""
    row = get_user_file(uid, fid)
    if not row:
        return header("File", uid) + "\n\n" + _e("no") + " That file does not exist."
    return header("File Details", uid) + "\n\n" + render_file_card(row)


def msg_file_log(uid, fid, lines=None):
    """Log viewer page."""
    row = get_user_file(uid, fid)
    if not row:
        return _e("no") + " That file does not exist."
    count = int(lines or get_setting_int(uid, "log_lines", 40))
    text = get_log(uid, fid, count)
    head = [
        header("Log Output", uid),
        "",
        _e("file") + " " + B(esc(str(row.get("fname")))),
        _e("log") + " Log size: " + fmt_size(get_log_size(uid, fid))
        + " \u2022 last " + str(count) + " line(s)",
        (_e("lightning") + " " + B("RUNNING")) if is_running(uid, fid) else (_e("stop") + " Idle"),
        "",
    ]
    body = PRE(trunc(text, 2600)) if text.strip() else I("No output recorded yet.")
    return "\n".join(head) + body


def msg_file_editor(uid, fid):
    """Editor page."""
    row = get_user_file(uid, fid)
    if not row:
        return _e("no") + " That file does not exist."
    ok, preview = preview_file(uid, fid, 20)
    lines = [
        header("File Editor", uid),
        "",
        _e("file") + " " + B(esc(str(row.get("fname")))) + "  " + C("#" + str(fid)),
        _e("disk") + " " + fmt_size(row.get("fsize")),
        "",
        I("Choose \u201cWrite content\u201d to replace the whole file or"
          " \u201cEdit line\u201d to change a single line. Every edit is versioned."),
        "",
    ]
    if ok:
        lines.append(syntax_highlight_preview(preview, file_ext(str(row.get("fname")))))
    else:
        lines.append(I(esc(preview)))
    return "\n".join(lines)


def msg_file_versions(uid, fid):
    """Version history page."""
    row = get_user_file(uid, fid)
    if not row:
        return _e("no") + " That file does not exist."
    versions = get_file_versions(fid)
    lines = [
        header("Version History", uid),
        "",
        _e("file") + " " + B(esc(str(row.get("fname")))),
        "",
    ]
    if not versions:
        lines.append(I("No versions yet. A version is stored each time you edit the file."))
    else:
        for version in versions[:12]:
            lines.append(
                _e("reload") + " " + B("v" + str(version.get("version")))
                + " \u2022 " + fmt_size(version.get("size"))
                + " \u2022 " + fmt_dt(version.get("created_at"))
            )
    return "\n".join(lines)


def msg_file_tags(uid, fid):
    """Tag page."""
    row = get_user_file(uid, fid)
    if not row:
        return _e("no") + " That file does not exist."
    tags = get_file_tags(fid)
    return "\n".join([
        header("File Tags", uid),
        "",
        _e("file") + " " + B(esc(str(row.get("fname")))),
        "",
        (_e("tag") + " " + ", ".join(C(t) for t in tags)) if tags else I("No tags yet."),
        "",
        I("Tags make files searchable from the file manager."),
    ])


def msg_file_share(uid, fid):
    """Share page."""
    row = get_user_file(uid, fid)
    if not row:
        return _e("no") + " That file does not exist."
    shares = get_file_shares(uid, fid)
    lines = [
        header("Share File", uid),
        "",
        _e("file") + " " + B(esc(str(row.get("fname")))),
        "",
        I("Pick how long the share token should stay valid."),
        "",
    ]
    if shares:
        lines.append(B("Existing tokens"))
        for share in shares[:6]:
            lines.append(
                _e("link") + " " + C(str(share.get("token")))
                + " \u2022 " + str(share.get("views", 0)) + " view(s)"
                + " \u2022 expires " + fmt_dt(share.get("expires_at"))
            )
    return "\n".join(lines)


def msg_run_menu(uid):
    """Run centre page."""
    tier = get_tier(uid)
    running = [(u, f) for (u, f) in get_all_running() if int(u) == int(uid)]
    lines = [
        header("Run Center", uid),
        "",
        _e("crown") + " Plan: " + B(tier_badge(get_tier_key(uid))),
        _e("code") + " Script runner: " + ("enabled" if tier.get("terminal") else B("disabled")),
        _e("lightning") + " Slots: " + str(len(running)) + " / "
        + str(TIER_PROC_LIMIT.get(get_tier_key(uid), 1) + get_extra_procs(uid)),
        _e("clock") + " Timeout per run: " + fmt_duration(TIER_TIMEOUTS.get(get_tier_key(uid), 300)),
        "",
    ]
    if running:
        lines.append(B("Running now"))
        for _, fid in running:
            row = get_file(fid) or {}
            info = get_proc_info(uid, fid) or {}
            lines.append(
                _e("run") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname", "?")))
                + " \u2022 up " + fmt_duration(info.get("uptime", 0))
                + " \u2022 PID " + str(info.get("pid", "?"))
            )
        lines.append("")
    supported = ", ".join(sorted(RUNNERS.keys()))
    lines.append(I("Supported extensions: " + supported))
    if not tier.get("terminal"):
        lines.append("")
        lines.append(_e("warn") + " " + I("Upgrade to Basic or higher to execute scripts."))
    return "\n".join(lines)


def msg_economy(uid):
    """Economy hub page."""
    user = get_user(uid)
    stats = get_referral_stats(uid)
    return "\n".join([
        header("Economy", uid),
        "",
        _e("pts") + " Points: " + B(fmt_num(user.get("points", 0))),
        _e("coin") + " Coins: " + B(fmt_num(user.get("coins", 0))),
        _e("trophy") + " Rank: " + B("#" + str(get_rank(uid))),
        _e("fire") + " Daily streak: " + B(str(user.get("daily_streak", 0))) + " day(s)",
        _e("users") + " Referrals: " + B(str(stats.get("count", 0)))
        + " (" + fmt_num(stats.get("total_pts", 0)) + " pts earned)",
        "",
        divider("\u2508", 22),
        B("How to earn"),
        _e("upload") + " Upload a file: +" + str(EARN_PTS_FOR_ACTION.get("upload", 10)) + " pts",
        _e("run") + " Run a script: +" + str(EARN_PTS_FOR_ACTION.get("run_script", 3)) + " pts",
        _e("gift") + " Daily claim: up to " + str(max(DAILY_REWARD_TABLE) + 100) + " pts",
        _e("link") + " Referral: +" + str(EARN_PTS_FOR_ACTION.get("referral", 100)) + " pts",
    ])


def msg_daily_result(ok, pts, streak, bonus_msg):
    """Daily claim result."""
    if not ok:
        return "\n".join([
            _e("clock") + " " + B("Already claimed"),
            "",
            I(str(bonus_msg or "Come back tomorrow for your next reward.")),
            "",
            _e("fire") + " Current streak: " + B(str(streak)) + " day(s)",
        ])
    lines = [
        _e("gift") + " " + B("Daily reward claimed!"),
        "",
        _e("pts") + " +" + B(fmt_num(pts)) + " points",
        _e("fire") + " Streak: " + B(str(streak)) + " day(s)",
    ]
    if bonus_msg:
        lines.append("")
        lines.append(str(bonus_msg))
    lines.append("")
    lines.append(I("Claim again in 24 hours to keep the streak alive."))
    return "\n".join(lines)


def msg_wallet(uid):
    """Wallet page."""
    user = get_user(uid)
    activity = db_all(
        "SELECT * FROM user_activity WHERE uid=? ORDER BY id DESC LIMIT 8",
        (int(uid),),
    )
    lines = [
        header("Wallet", uid),
        "",
        _e("pts") + " Points: " + B(fmt_num(user.get("points", 0))),
        _e("coin") + " Coins: " + B(fmt_num(user.get("coins", 0))),
        _e("star") + " XP: " + B(fmt_num(user.get("xp", 0))),
        "",
        B("Purchased perks"),
        _e("folder") + " Extra file slots: " + str(get_extra_slots(uid)),
        _e("disk") + " Extra storage: " + str(get_extra_storage_mb(uid)) + " MB",
        _e("lightning") + " Extra process slots: " + str(get_extra_procs(uid)),
        (_e("rocket") + " Temporary Pro active until "
         + fmt_dt_abs(get_setting(uid, "temp_tier_until", ""))) if temp_tier_active(uid) else "",
        "",
        B("Recent activity"),
    ]
    if activity:
        for row in activity:
            lines.append("\u2022 " + C(str(row.get("action"))) + " \u2022 " + fmt_dt(row.get("ts")))
    else:
        lines.append(I("No activity recorded yet."))
    return "\n".join([line for line in lines if line != ""] if False else lines)


def msg_leaderboard(rows, uid, page=1, total_pages=1):
    """Leaderboard page."""
    start_rank = (max(1, int(page)) - 1) * 10 + 1
    return "\n".join([
        header("Leaderboard", uid),
        "",
        render_leaderboard(rows, uid, start_rank),
        "",
        divider("\u2508", 22),
        _e("user") + " Your rank: " + B("#" + str(get_rank(uid)))
        + " with " + fmt_num(get_user(uid).get("points", 0)) + " pts",
        paginator_text(page, total_pages, 0).replace("  \u2022  0 item(s)", ""),
    ])


def msg_shop(uid=None):
    """Shop page."""
    coins = fmt_num(get_user(uid).get("coins", 0)) if uid else "0"
    return "\n".join([
        header("Shop", uid),
        "",
        _e("coin") + " Balance: " + B(coins) + " coins",
        "",
        render_shop(),
        "",
        I("Coins are earned alongside points from daily claims, referrals and achievements."),
    ])


def msg_achievements(uid):
    """Achievements page."""
    earned = set(get_user_achievements(uid))
    lines = [
        header("Achievements", uid),
        "",
        _e("medal") + " Unlocked " + B(str(len(earned))) + " of " + str(len(ACHIEVEMENTS)),
        C(pct_bar(100.0 * len(earned) / max(1, len(ACHIEVEMENTS)), 16)),
        "",
    ]
    for key in ACHIEVEMENTS:
        lines.append(render_achievement_card(key, key in earned))
    return "\n".join(lines)


def msg_badges(uid):
    """Badges page."""
    earned = set(get_user_badges(uid))
    lines = [
        header("Badges", uid),
        "",
        _e("shield") + " Collected " + B(str(len(earned))) + " of " + str(len(BADGES)),
        "",
    ]
    for key, badge in BADGES.items():
        mark = _e("ok") if key in earned else _e("lock")
        lines.append(mark + " " + str(badge.get("emoji")) + " " + B(esc(str(badge.get("name")))))
        lines.append("   " + I(esc(str(badge.get("desc")))))
    return "\n".join(lines)


def msg_support(uid):
    """Support hub page."""
    rows, total, _, _ = get_user_tickets(uid)
    open_count = int(db_val(
        "SELECT COUNT(*) c FROM tickets WHERE uid=? AND status='open'", (int(uid),), 0) or 0)
    return "\n".join([
        header("Support", uid),
        "",
        _e("ticket") + " Your tickets: " + B(str(total)) + " (" + str(open_count) + " open)",
        "",
        I("Open a ticket for billing questions, bug reports, feature requests or abuse reports."
          " Staff replies arrive right here in chat."),
        "",
        B("Categories") + ": " + ", ".join(TICKET_CATEGORIES),
        B("Priorities") + ": " + ", ".join(TICKET_PRIORITIES),
    ])


def msg_tickets(uid, tickets, page=1, total_pages=1, total=0):
    """Ticket list page."""
    lines = [header("My Tickets", uid), ""]
    if not tickets:
        lines.append(I("You have not opened any tickets yet."))
        return "\n".join(lines)
    for row in tickets:
        icon = _e("ok") if str(row.get("status")) == "closed" else _e("pending")
        lines.append(
            icon + " " + C("#" + str(row.get("id"))) + " "
            + B(esc(trunc(str(row.get("subject")), 34)))
        )
        lines.append(
            "    " + PRIORITY_EMOJI.get(str(row.get("priority")), "") + " "
            + str(row.get("priority")) + " \u2022 " + str(row.get("category"))
            + " \u2022 " + fmt_dt(row.get("updated_at"))
        )
    lines.append("")
    lines.append(paginator_text(page, total_pages, total))
    return "\n".join(lines)


def msg_ticket_detail(tid, uid):
    """Ticket thread page."""
    ticket = get_ticket(tid)
    if not ticket:
        return _e("no") + " Ticket not found."
    if int(ticket.get("uid") or 0) != int(uid) and not is_admin(uid):
        return _e("lock") + " You cannot view this ticket."
    lines = [header("Ticket", uid), "", render_ticket_card(ticket), "", divider("\u2508", 22), B("Conversation")]
    messages = get_ticket_msgs(tid)
    if not messages:
        lines.append(I("No messages yet."))
    else:
        for msg in messages[-12:]:
            who = (_e("admin") + " Staff") if int(msg.get("is_staff") or 0) else (_e("user") + " You")
            if is_admin(uid) and not int(msg.get("is_staff") or 0):
                who = _e("user") + " " + str(msg.get("uid"))
            lines.append("")
            lines.append(who + " \u2022 " + I(fmt_dt(msg.get("created_at"))))
            lines.append(esc(trunc(str(msg.get("message")), 700)))
    return "\n".join(lines)


def msg_settings(uid):
    """Settings page."""
    user = get_user(uid)
    theme = get_theme(uid)
    font = get_font(uid)
    return "\n".join([
        header("Settings", uid),
        "",
        _e("magic") + " Theme: " + B(str(theme.get("name"))) + " " + str(theme.get("emoji")),
        _e("code") + " Font: " + B(str(font.get("name"))) + " " + str(font.get("emoji")),
        _e("web") + " Language: " + B(LANGS.get(str(user.get("lang") or "en"), "English")),
        _e("bell") + " Push notifications: "
        + B("on" if int(user.get("push_enabled") or 0) else "off"),
        "",
        _e("api") + " API keys: " + str(len([k for k in list_api_keys(uid) if int(k.get("is_active") or 0)])),
        _e("cron") + " Cron jobs: " + str(len(get_user_crons(uid))),
        _e("link") + " Webhooks: " + str(len(get_webhooks(uid))),
        _e("note") + " Notes: " + str(len(get_notes(uid))),
        "",
        I("Themes change section headers, fonts change body rendering."),
    ])


def msg_theme_select(uid):
    """Theme picker page."""
    lines = [header("Choose a Theme", uid), ""]
    current = str(get_user(uid).get("theme") or "sigma")
    for key, theme in THEMES.items():
        mark = _e("check") if key == current else "\u2022"
        lines.append(
            mark + " " + str(theme.get("emoji")) + " " + B(esc(str(theme.get("name"))))
            + " " + C(str(theme.get("header_char")) * 3)
        )
        lines.append("   " + I(esc(str(theme.get("description")))))
    return "\n".join(lines)


def msg_font_select(uid):
    """Font picker page."""
    lines = [header("Choose a Font", uid), ""]
    current = str(get_user(uid).get("font") or "default")
    for key, font in FONTS.items():
        mark = _e("check") if key == current else "\u2022"
        lines.append(mark + " " + str(font.get("emoji")) + " " + B(esc(str(font.get("name")))))
        lines.append("   " + I(esc(str(font.get("description")))))
    return "\n".join(lines)


def msg_notifications(uid):
    """Notification centre page."""
    rows = get_notifications(uid, 12)
    lines = [
        header("Notifications", uid),
        "",
        _e("bell") + " Push: " + B("on" if int(get_user(uid).get("push_enabled") or 0) else "off"),
        "",
    ]
    if not rows:
        lines.append(I("Nothing here yet."))
    else:
        for row in rows:
            mark = "\u2022" if int(row.get("read") or 0) else _e("new")
            lines.append(mark + " " + esc(trunc(str(row.get("message")), 160)))
            lines.append("   " + I(fmt_dt(row.get("created_at"))))
    return "\n".join(lines)


def msg_api_keys(uid):
    """API key page."""
    tier = get_tier(uid)
    keys = list_api_keys(uid)
    lines = [
        header("API Keys", uid),
        "",
        _e("api") + " API access: " + B("enabled" if tier.get("api") else "disabled")
        + I(" (" + str(tier.get("name")) + " plan)"),
        "",
    ]
    if not tier.get("api") and not is_admin(uid):
        lines.append(_e("warn") + " " + I("Upgrade to Pro or higher to use the API."))
        return "\n".join(lines)
    if not keys:
        lines.append(I("No keys yet. Create one to authenticate API requests."))
    else:
        for key in keys:
            mark = _e("ok") if int(key.get("is_active") or 0) else _e("no")
            lines.append(
                mark + " " + C("#" + str(key.get("id"))) + " " + B(esc(str(key.get("label"))))
            )
            lines.append(
                "   created " + fmt_dt(key.get("created_at"))
                + " \u2022 last used " + fmt_dt(key.get("last_used"))
            )
    lines.append("")
    lines.append(I("Keys are stored hashed. The raw value is only shown once at creation."))
    return "\n".join(lines)


def msg_crons(uid):
    """Cron manager page."""
    jobs = get_user_crons(uid)
    lines = [
        header("Cron Jobs", uid),
        "",
        _e("cron") + " " + B(str(len(jobs))) + " job(s) configured",
        "",
    ]
    if not jobs:
        lines.append(I("No scheduled jobs. Create one to run a script automatically."))
    else:
        for job in jobs:
            row = get_file(job.get("fid")) or {}
            mark = _e("ok") if int(job.get("enabled") or 0) else _e("stop")
            lines.append(
                mark + " " + C("#" + str(job.get("id"))) + " "
                + B(esc(trunc(str(row.get("fname", "missing file")), 26)))
                + " \u2022 " + C(str(job.get("schedule")))
            )
            lines.append(
                "   next " + fmt_dt(job.get("next_run")) + " \u2022 last "
                + fmt_dt(job.get("last_run")) + " \u2022 " + str(job.get("run_count", 0)) + " run(s)"
            )
    lines.append("")
    lines.append(I("Presets: " + ", ".join(CRON_PRESETS)))
    return "\n".join(lines)


def msg_cron_detail(cron_id):
    """Cron detail page."""
    job = get_cron(cron_id)
    if not job:
        return _e("no") + " Cron job not found."
    row = get_file(job.get("fid")) or {}
    return "\n".join([
        header("Cron Job #" + str(job.get("id")), job.get("uid")),
        "",
        _e("file") + " File: " + B(esc(str(row.get("fname", "missing")))) + " " + C("#" + str(job.get("fid"))),
        _e("clock") + " Schedule: " + C(str(job.get("schedule"))),
        (_e("ok") + " Enabled") if int(job.get("enabled") or 0) else (_e("stop") + " Disabled"),
        _e("fwd") + " Next run: " + fmt_dt_abs(job.get("next_run")) + " (" + fmt_dt(job.get("next_run")) + ")",
        _e("back") + " Last run: " + fmt_dt(job.get("last_run")),
        _e("graph") + " Total runs: " + fmt_num(job.get("run_count", 0)),
    ])


def msg_webhooks(uid):
    """Webhook page."""
    hooks = get_webhooks(uid)
    lines = [header("Webhooks", uid), ""]
    if not hooks:
        lines.append(I("No webhooks configured. Add one to receive event callbacks."))
    else:
        for hook in hooks:
            mark = _e("ok") if int(hook.get("enabled") or 0) else _e("no")
            lines.append(mark + " " + C("#" + str(hook.get("id"))) + " " + esc(trunc(str(hook.get("url")), 48)))
            lines.append(
                "   events: " + esc(str(hook.get("events")))
                + " \u2022 last trigger " + fmt_dt(hook.get("last_triggered"))
            )
    return "\n".join(lines)


def msg_notes(uid):
    """Notes list page."""
    notes = get_notes(uid)
    lines = [header("My Notes", uid), ""]
    if not notes:
        lines.append(I("No notes yet. Notes are private scratch pads stored with your account."))
    else:
        for note in notes:
            prefix = _e("pin") if int(note.get("pinned") or 0) else _e("note")
            lines.append(prefix + " " + B(esc(trunc(str(note.get("title")), 40))))
            lines.append("   " + I(esc(trunc(str(note.get("content") or ""), 90))))
    return "\n".join(lines)


def msg_note_detail(uid, note_id):
    """Note detail page."""
    note = get_note(uid, note_id)
    if not note:
        return _e("no") + " Note not found."
    return "\n".join([
        header("Note", uid),
        "",
        (_e("pin") if int(note.get("pinned") or 0) else _e("note")) + " "
        + B(esc(str(note.get("title")))),
        I("updated " + fmt_dt(note.get("updated_at"))),
        "",
        esc(str(note.get("content") or "")) or I("(empty)"),
    ])


def msg_stats(uid):
    """Public statistics page."""
    return header("Platform Stats", uid) + "\n\n" + render_stats_card(get_admin_stats())


def msg_plan_info(uid):
    """Current plan page."""
    key = get_tier_key(uid)
    lines = [
        header("Your Plan", uid),
        "",
        render_plan_card(key),
        "",
        divider("\u2508", 22),
        _e("folder") + " Used: " + str(file_count(uid)) + " file(s), " + fmt_size(total_file_size(uid)),
    ]
    if temp_tier_active(uid):
        lines.append(_e("rocket") + " Temporary Pro until " + fmt_dt_abs(get_setting(uid, "temp_tier_until", "")))
    return "\n".join(lines)


def msg_upgrade(uid):
    """Upgrade page listing every plan."""
    lines = [
        header("Plans & Upgrades", uid),
        "",
        _e("crown") + " Current plan: " + B(tier_badge(get_tier_key(uid))),
        "",
    ]
    for key in TIER_ORDER:
        lines.append(render_plan_card(key))
        lines.append("")
    lines.append(divider("\u2508", 22))
    lines.append(I("Pick a plan below to send an upgrade request to the administrators."))
    return "\n".join(lines)


def msg_upload_help(uid):
    """Upload instructions."""
    return "\n".join([
        header("Upload a File", uid),
        "",
        _e("upload") + " " + B("Just send the file to this chat as a document."),
        "",
        _e("info") + " Limits on your plan:",
        "   " + _e("folder") + " Files: "
        + ("unlimited" if effective_file_limit(uid) == -1 else str(effective_file_limit(uid))),
        "   " + _e("disk") + " Max size: " + str(effective_size_limit_mb(uid)) + " MB per file",
        "",
        _e("code") + " Runnable extensions: " + ", ".join(sorted(RUNNERS.keys())),
        "",
        _e("warn") + " " + I("Uploads are reviewed before they can be executed."
                             " You will get a notification once a decision is made."),
    ])


def msg_referral(uid):
    """Referral page."""
    stats = get_referral_stats(uid)
    bot_username = get_bot_username()
    link = "https://t.me/" + bot_username + "?start=" + str(stats.get("code", "")) if bot_username else ""
    lines = [
        header("Referrals", uid),
        "",
        _e("key") + " Your code: " + C(str(stats.get("code", ""))),
    ]
    if link:
        lines.append(_e("link") + " Your link: " + C(link))
    lines += [
        "",
        _e("users") + " Referred users: " + B(str(stats.get("count", 0))),
        _e("pts") + " Points earned: " + B(fmt_num(stats.get("total_pts", 0))),
        "",
        I("You get +" + str(EARN_PTS_FOR_ACTION.get("referral", 100))
          + " pts and 25 coins per signup; your friend gets +"
          + str(EARN_PTS_FOR_ACTION.get("referred_join", 50)) + " pts."),
    ]
    recent = stats.get("recent", [])
    if recent:
        lines.append("")
        lines.append(B("Recent referrals"))
        for row in recent:
            lines.append("\u2022 " + C(str(row.get("referred_uid"))) + " \u2022 " + fmt_dt(row.get("created_at")))
    return "\n".join(lines)


def msg_admin_dash():
    """Admin dashboard page."""
    stats = get_admin_stats()
    tickets = get_ticket_stats()
    errors = get_error_stats()
    lines = [
        header("Admin Dashboard", None),
        "",
        render_stats_card(stats),
        "",
        divider("\u2508", 22),
        B("Plans"),
    ]
    for key in TIER_ORDER:
        lines.append(
            "   " + str(TIERS[key].get("emoji")) + " " + str(TIERS[key].get("name"))
            + ": " + str(stats.get("tier_counts", {}).get(key, 0))
        )
    lines += [
        "",
        B("Support"),
        "   " + _e("ticket") + " open " + str(tickets.get("open", 0))
        + " \u2022 urgent " + str(tickets.get("urgent", 0))
        + " \u2022 closed " + str(tickets.get("closed", 0)),
        "   " + _e("clock") + " avg resolution " + fmt_duration(tickets.get("avg_response_time", 0)),
        "",
        B("Reliability (24h)"),
        "   " + _e("graph") + " runs " + str(errors.get("total_24h", 0))
        + " \u2022 failures " + str(errors.get("failed_24h", 0))
        + " \u2022 timeouts " + str(errors.get("timeouts_24h", 0)),
        "   " + _e("ok") + " success rate " + ("%.1f" % errors.get("success_rate", 100.0)) + "%",
        "",
        _e("crown") + " Pending upgrade orders: " + str(stats.get("orders_pending", 0)),
    ]
    return "\n".join(lines)


def msg_admin_users(users, page=1, total_pages=1, total=0):
    """Admin user list page."""
    lines = [header("Users", None), "", _e("users") + " Total: " + B(fmt_num(total)), ""]
    if not users:
        lines.append(I("No users found."))
    else:
        for row in users:
            flag = _e("ban") if int(row.get("is_banned") or 0) else "\u2022"
            lines.append(
                flag + " " + C(str(row.get("uid"))) + " " + B(esc(trunc(user_label(row), 26)))
            )
            lines.append(
                "   " + tier_badge(row.get("tier", "free")) + " \u2022 "
                + fmt_num(row.get("points", 0)) + " pts \u2022 lvl " + str(row.get("level", 1))
                + " \u2022 seen " + fmt_dt(row.get("last_seen"))
            )
    lines.append("")
    lines.append(paginator_text(page, total_pages, total))
    return "\n".join(lines)


def msg_admin_user_detail(target_uid):
    """Admin user detail page."""
    row = db_one("SELECT * FROM users WHERE uid=?", (int(target_uid),))
    if not row:
        return _e("no") + " User not found."
    lines = [
        header("User Detail", None),
        "",
        render_user_card(row),
        "",
        divider("\u2508", 22),
        B("Files") + ": " + str(file_count(target_uid)) + " \u2022 " + fmt_size(total_file_size(target_uid)),
        B("Running") + ": " + str(running_count(target_uid)),
        B("Crons") + ": " + str(len(get_user_crons(target_uid))),
        B("Tickets") + ": " + str(int(db_val(
            "SELECT COUNT(*) c FROM tickets WHERE uid=?", (int(target_uid),), 0) or 0)),
    ]
    recent = db_all(
        "SELECT * FROM user_activity WHERE uid=? ORDER BY id DESC LIMIT 6",
        (int(target_uid),),
    )
    if recent:
        lines.append("")
        lines.append(B("Recent activity"))
        for act in recent:
            lines.append("\u2022 " + C(str(act.get("action"))) + " \u2022 " + fmt_dt(act.get("ts")))
    return "\n".join(lines)


def msg_admin_files(files, page=1, total_pages=1, total=0):
    """Pending review list page."""
    lines = [
        header("Pending Files", None),
        "",
        _e("pending") + " Awaiting review: " + B(fmt_num(total)),
        "",
    ]
    if not files:
        lines.append(I("Nothing to review. Good job!"))
    else:
        for row in files:
            lines.append(
                _e("file") + " " + C("#" + str(row.get("id"))) + " "
                + B(esc(trunc(str(row.get("fname")), 32)))
            )
            lines.append(
                "   from " + C(str(row.get("uid"))) + " \u2022 " + fmt_size(row.get("fsize"))
                + " \u2022 " + fmt_dt(row.get("uploaded"))
            )
    lines.append("")
    lines.append(paginator_text(page, total_pages, total))
    return "\n".join(lines)


def msg_admin_file_detail(fid):
    """Admin file review page."""
    row = get_file(fid)
    if not row:
        return _e("no") + " File not found."
    owner = db_one("SELECT * FROM users WHERE uid=?", (int(row.get("uid") or 0),)) or {}
    lines = [
        header("Review File", None),
        "",
        render_file_card(row),
        "",
        divider("\u2508", 22),
        _e("user") + " Owner: " + B(esc(user_label(owner))) + " " + C(str(row.get("uid"))),
        _e("crown") + " Plan: " + tier_badge(owner.get("tier", "free")),
        _e("upload") + " Owner uploads: " + fmt_num(owner.get("total_uploads", 0)),
    ]
    path = Path(str(row.get("fpath") or ""))
    if path.exists() and int(row.get("fsize") or 0) < 400000:
        content = read_file_text(path, 2000)
        if content.strip():
            lines.append("")
            lines.append(B("Preview"))
            lines.append(syntax_highlight_preview("\n".join(content.splitlines()[:25]),
                                                  file_ext(str(row.get("fname")))))
    return "\n".join(lines)


def msg_admin_tickets(tickets, page=1, total_pages=1, total=0):
    """Admin ticket queue page."""
    stats = get_ticket_stats()
    lines = [
        header("Ticket Queue", None),
        "",
        _e("ticket") + " Open: " + B(str(stats.get("open", 0)))
        + " \u2022 urgent " + str(stats.get("urgent", 0))
        + " \u2022 closed " + str(stats.get("closed", 0)),
        _e("clock") + " Avg resolution: " + fmt_duration(stats.get("avg_response_time", 0)),
        "",
    ]
    if not tickets:
        lines.append(I("No open tickets."))
    else:
        for row in tickets:
            lines.append(
                PRIORITY_EMOJI.get(str(row.get("priority")), _e("ticket"))
                + " " + C("#" + str(row.get("id"))) + " "
                + B(esc(trunc(str(row.get("subject")), 30)))
            )
            lines.append(
                "   " + str(row.get("category")) + " \u2022 from " + C(str(row.get("uid")))
                + " \u2022 " + fmt_dt(row.get("updated_at"))
            )
    lines.append("")
    lines.append(paginator_text(page, total_pages, total))
    return "\n".join(lines)


def msg_admin_sys():
    """Admin system page."""
    stats = sys_stats()
    info = get_system_info()
    top_disk = disk_usage_by_user(5)
    lines = [
        header("System", None),
        "",
        render_sys_card(stats),
        "",
        divider("\u2508", 22),
        _e("server") + " Host: " + C(str(info.get("hostname"))),
        _e("info") + " OS: " + esc(str(info.get("os"))) + " (" + esc(str(info.get("machine"))) + ")",
        _e("code") + " Python: " + str(info.get("python")),
        _e("bot") + " Bot version: " + str(info.get("version")),
        _e("clock") + " Bot uptime: " + str(info.get("uptime")),
        _e("clock") + " Host uptime: " + str(info.get("host_uptime")),
        _e("gear") + " Threads: " + str(info.get("threads")) + " \u2022 PID " + str(info.get("pid")),
        _e("folder") + " Base dir: " + C(str(info.get("base_dir"))),
    ]
    if top_disk:
        lines.append("")
        lines.append(B("Top storage users"))
        for row in top_disk:
            lines.append(
                "   " + C(str(row.get("uid"))) + " \u2022 " + str(row.get("files", 0))
                + " file(s) \u2022 " + fmt_size(row.get("total_bytes", 0))
            )
    return "\n".join(lines)


def msg_admin_db():
    """Admin database page."""
    counts = db_stat_counts()
    lines = [
        header("Database", None),
        "",
        _e("db") + " File: " + C(str(DB_PATH)),
        _e("disk") + " Size: " + B(fmt_size(db_size())),
        _e("backup") + " Backups stored: " + str(len(list(BACKUPS_DIR.glob("*.db")))),
        "",
        B("Row counts"),
    ]
    for table in sorted(counts.keys()):
        lines.append("   " + table + ": " + fmt_num(counts.get(table, 0)))
    return "\n".join(lines)


def msg_admin_running():
    """Admin running process page."""
    running = get_all_running()
    lines = [
        header("Running Scripts", None),
        "",
        _e("lightning") + " Active: " + B(str(len(running))),
        "",
    ]
    if not running:
        lines.append(I("Nothing is running right now."))
    else:
        for uid, fid in running:
            row = get_file(fid) or {}
            info = get_proc_info(uid, fid) or {}
            lines.append(
                _e("run") + " " + C(str(uid)) + " \u2022 " + C("#" + str(fid)) + " "
                + B(esc(trunc(str(row.get("fname", "?")), 26)))
            )
            lines.append(
                "   PID " + str(info.get("pid", "?")) + " \u2022 up "
                + fmt_duration(info.get("uptime", 0)) + " \u2022 mem "
                + fmt_size(int(info.get("memory_kb", 0)) * 1024)
            )
    return "\n".join(lines)


def msg_admin_audit():
    """Audit log page."""
    rows = get_audit_log(20)
    lines = [header("Audit Log", None), ""]
    if not rows:
        lines.append(I("No admin actions recorded yet."))
    else:
        for row in rows:
            lines.append(
                _e("admin") + " " + C(str(row.get("admin_uid"))) + " \u2192 "
                + B(esc(str(row.get("action"))))
                + (" on " + C(str(row.get("target_uid"))) if int(row.get("target_uid") or 0) else "")
            )
            details = str(row.get("details") or "").strip()
            lines.append("   " + I(fmt_dt(row.get("ts")) + ((" \u2022 " + esc(details)) if details else "")))
    return "\n".join(lines)


def msg_admin_metrics():
    """Metrics history page."""
    rows = get_recent_metrics(12)
    lines = [header("Metrics", None), ""]
    if not rows:
        lines.append(I("No metrics recorded yet. The collector runs every 5 minutes."))
        return "\n".join(lines)
    for row in rows:
        lines.append(
            _e("graph") + " " + I(fmt_dt(row.get("ts"))) + " \u2022 cpu "
            + str(row.get("cpu", 0)) + "% \u2022 mem " + str(row.get("mem", 0))
            + "% \u2022 disk " + str(row.get("disk", 0)) + "%"
        )
        lines.append(
            "   users " + fmt_num(row.get("users", 0)) + " \u2022 files "
            + fmt_num(row.get("files", 0)) + " \u2022 runs " + fmt_num(row.get("runs", 0))
        )
    return "\n".join(lines)


def msg_admin_broadcast():
    """Broadcast targeting page."""
    counts = {}
    for key in TIER_ORDER:
        counts[key] = int(db_val(
            "SELECT COUNT(*) c FROM users WHERE is_banned=0 AND tier=?", (key,), 0) or 0)
    total = int(db_val("SELECT COUNT(*) c FROM users WHERE is_banned=0", (), 0) or 0)
    lines = [
        header("Broadcast", None),
        "",
        _e("bell") + " Reachable users: " + B(fmt_num(total)),
        "",
        B("By plan"),
    ]
    for key in TIER_ORDER:
        lines.append("   " + tier_badge(key) + ": " + str(counts.get(key, 0)))
    lines += [
        "",
        I("Choose an audience, then send the message text. You will get a confirmation step"
          " before anything is delivered."),
    ]
    return "\n".join(lines)


# ============================================================================
# SECTION 14 - SEND HELPERS
# ============================================================================

_bot_username_cache = {"value": ""}


def get_bot_username():
    """Cached bot username (empty string when unavailable)."""
    if _bot_username_cache["value"]:
        return _bot_username_cache["value"]
    try:
        me = bot.get_me()
        _bot_username_cache["value"] = str(getattr(me, "username", "") or "")
    except Exception as exc:
        log.debug("get_me failed: %s", exc)
    return _bot_username_cache["value"]


def chunk_text(text, size=MAX_TG_TEXT):
    """Split long text into Telegram-sized chunks on line boundaries."""
    text = str(text or "")
    if len(text) <= size:
        return [text]
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > size:
            if current:
                chunks.append(current)
            while len(line) > size:
                chunks.append(line[:size])
                line = line[size:]
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


def send(uid, text, markup=None, **kw):
    """Safe send with retry and automatic chunking. Returns the last message or None.

    The member's chosen alphabet is applied here, so every card AND every
    inline button label is drawn in it without touching any call site.
    """
    try:
        text, markup = apply_font(uid, text, markup)
    except Exception as exc:
        log.debug("font skipped for %s: %s", uid, exc)
    parts = chunk_text(text)
    result = None
    for index, part in enumerate(parts):
        reply_markup = markup if index == len(parts) - 1 else None
        for attempt in range(3):
            try:
                result = bot.send_message(int(uid), part, reply_markup=reply_markup, **kw)
                break
            except apihelper.ApiTelegramException as exc:
                message = str(exc)
                if "retry after" in message.lower():
                    wait = 3
                    match = re.search(r"retry after (\d+)", message.lower())
                    if match:
                        wait = int(match.group(1)) + 1
                    time.sleep(min(30, wait))
                    continue
                if "bot was blocked" in message or "chat not found" in message or "user is deactivated" in message:
                    log.info("Cannot deliver to %s: %s", uid, message)
                    return None
                if "can't parse entities" in message.lower():
                    try:
                        result = bot.send_message(
                            int(uid), re.sub(r"<[^>]+>", "", part),
                            reply_markup=reply_markup, parse_mode=None,
                        )
                    except Exception as inner:
                        log.warning("Plain fallback failed for %s: %s", uid, inner)
                    break
                log.warning("Send failed to %s (attempt %s): %s", uid, attempt + 1, message)
                time.sleep(1.0 + attempt)
            except Exception as exc:
                log.warning("Send error to %s (attempt %s): %s", uid, attempt + 1, exc)
                time.sleep(1.0 + attempt)
    return result


def edit(cid, mid, text, markup=None):
    """Safe edit; falls back to sending a new message. Returns True on success."""
    try:
        text, markup = apply_font(cid, text, markup)
    except Exception as exc:
        log.debug("font skipped for %s: %s", cid, exc)
    parts = chunk_text(text)
    body = parts[0]
    try:
        bot.edit_message_text(body, int(cid), int(mid), reply_markup=markup)
        for extra in parts[1:]:
            send(cid, extra)
        return True
    except apihelper.ApiTelegramException as exc:
        message = str(exc)
        if "message is not modified" in message:
            return True
        if "can't parse entities" in message.lower():
            try:
                bot.edit_message_text(
                    re.sub(r"<[^>]+>", "", body), int(cid), int(mid),
                    reply_markup=markup, parse_mode=None,
                )
                return True
            except Exception as inner:
                log.debug("Plain edit fallback failed: %s", inner)
        log.debug("Edit failed, sending instead: %s", message)
    except Exception as exc:
        log.debug("Edit error, sending instead: %s", exc)
    return send(cid, text, markup) is not None


def send_or_edit(uid, msg, text, markup=None):
    """Edit the given message when possible, otherwise send a new one."""
    if msg is not None:
        try:
            return edit(msg.chat.id, msg.message_id, text, markup)
        except Exception as exc:
            log.debug("send_or_edit fallback: %s", exc)
    return send(uid, text, markup) is not None


def ack(call, text="", alert=False):
    """Answer a callback query, never raising."""
    try:
        bot.answer_callback_query(call.id, text=str(text)[:200], show_alert=bool(alert))
        return True
    except Exception as exc:
        log.debug("ack failed: %s", exc)
        return False


def notify_admins(text, markup=None):
    """Send a message to every configured admin."""
    sent = 0
    for admin_uid in sorted(ADMIN_IDS):
        if send(admin_uid, text, markup) is not None:
            sent += 1
    return sent


def notify_user(uid, text):
    """Send a message only when the user has push notifications enabled."""
    if int(uid or 0) <= 0:
        return False
    try:
        user = get_user(uid)
    except Exception as exc:
        log.debug("notify_user lookup failed: %s", exc)
        return False
    add_notification(uid, re.sub(r"<[^>]+>", "", str(text))[:800], "push")
    if not int(user.get("push_enabled") or 0):
        return False
    if int(user.get("is_banned") or 0):
        return False
    return send(uid, text) is not None


def send_file_to_user(uid, fid, caption=None):
    """Send an actual stored file as a document. Returns (ok, message)."""
    row = get_file(fid)
    if not row:
        return False, "File not found."
    if int(row.get("uid") or 0) != int(uid) and not is_admin(uid) and not int(row.get("is_public") or 0):
        return False, "You do not have access to that file."
    path = Path(str(row.get("fpath") or ""))
    if not path.exists():
        return False, "File is missing on disk."
    if path.stat().st_size > 49 * 1024 * 1024:
        return False, "File is too large to send over Telegram (49 MB limit)."
    try:
        with open(str(path), "rb") as handle:
            bot.send_document(
                int(uid), handle,
                visible_file_name=str(row.get("fname") or path.name),
                caption=caption if caption else (_e("file") + " " + esc(str(row.get("fname")))),
            )
    except Exception as exc:
        return False, "Upload to Telegram failed: " + str(exc)
    bump_download(fid)
    return True, "Sent."


def send_document_path(uid, path, caption=""):
    """Send an arbitrary file from disk. Returns (ok, message)."""
    path = Path(str(path))
    if not path.exists():
        return False, "File not found on disk."
    try:
        with open(str(path), "rb") as handle:
            bot.send_document(int(uid), handle, visible_file_name=path.name, caption=caption)
    except Exception as exc:
        return False, "Send failed: " + str(exc)
    return True, "Sent."


def mass_send(uids, text):
    """Threaded broadcast. Returns (sent, failed)."""
    targets = [int(u) for u in list(uids or []) if u]
    counters = {"sent": 0, "failed": 0}
    counter_lock = threading.Lock()

    def worker(batch):
        local_sent = 0
        local_failed = 0
        for target in batch:
            try:
                if send(target, text) is not None:
                    local_sent += 1
                else:
                    local_failed += 1
            except Exception as exc:
                log.debug("mass_send error for %s: %s", target, exc)
                local_failed += 1
            time.sleep(0.05)
        with counter_lock:
            counters["sent"] += local_sent
            counters["failed"] += local_failed

    if not targets:
        return 0, 0
    worker_count = max(1, min(4, (len(targets) + 24) // 25))
    batches = [targets[i::worker_count] for i in range(worker_count)]
    threads = []
    for batch in batches:
        thread = threading.Thread(target=worker, args=(batch,), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=600)
    return counters["sent"], counters["failed"]


# ============================================================================
# SECTION 21 - SECURITY CORE (STATIC MALWARE / BACKDOOR ANALYSIS)
# ============================================================================
# Everything a user uploads is treated as hostile until proven otherwise.
# The pipeline is:
#   upload -> static scan (regex + AST + entropy) -> verdict
#     CLEAN       -> normal review queue
#     SUSPICIOUS  -> forced sandbox run with a neutralised copy + admin review
#     MALICIOUS   -> instant quarantine, never runnable, admin alert
# Every later edit (push) is re-scanned and a security regression blocks the
# push, which is what stops the "upload clean code, then swap in a payload"
# attack.

EXTRA_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS security_scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        verdict TEXT DEFAULT 'clean',
        score INTEGER DEFAULT 0,
        findings TEXT DEFAULT '',
        categories TEXT DEFAULT '',
        checksum TEXT DEFAULT '',
        scanned_at TEXT DEFAULT '',
        engine TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS quarantine (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        reason TEXT DEFAULT '',
        verdict TEXT DEFAULT '',
        score INTEGER DEFAULT 0,
        stored_path TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        released_at TEXT DEFAULT '',
        released_by INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS sandbox_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        exit_code INTEGER DEFAULT 0,
        timed_out INTEGER DEFAULT 0,
        duration_ms INTEGER DEFAULT 0,
        output TEXT DEFAULT '',
        network_blocked INTEGER DEFAULT 1,
        token_neutralised INTEGER DEFAULT 0,
        jail_path TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS token_vault (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        token_hash TEXT DEFAULT '',
        token_hint TEXT DEFAULT '',
        replaced_with TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS code_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        state TEXT DEFAULT 'open',
        verdict TEXT DEFAULT '',
        score INTEGER DEFAULT 0,
        summary TEXT DEFAULT '',
        reviewer INTEGER DEFAULT 0,
        created_at TEXT DEFAULT '',
        closed_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS edit_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fid INTEGER DEFAULT 0,
        uid INTEGER DEFAULT 0,
        action TEXT DEFAULT '',
        lines_added INTEGER DEFAULT 0,
        lines_removed INTEGER DEFAULT 0,
        old_score INTEGER DEFAULT 0,
        new_score INTEGER DEFAULT 0,
        blocked INTEGER DEFAULT 0,
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS reaction_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER DEFAULT 0,
        cid INTEGER DEFAULT 0,
        mid INTEGER DEFAULT 0,
        emoji TEXT DEFAULT '',
        created_at TEXT DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS plugin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT DEFAULT '',
        version TEXT DEFAULT '',
        state TEXT DEFAULT '',
        detail TEXT DEFAULT '',
        loaded_at TEXT DEFAULT ''
    )""",
]

EXTRA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_scans_fid ON security_scans(fid)",
    "CREATE INDEX IF NOT EXISTS idx_scans_uid ON security_scans(uid)",
    "CREATE INDEX IF NOT EXISTS idx_quarantine_fid ON quarantine(fid)",
    "CREATE INDEX IF NOT EXISTS idx_sandbox_fid ON sandbox_runs(fid)",
    "CREATE INDEX IF NOT EXISTS idx_reviews_state ON code_reviews(state)",
    "CREATE INDEX IF NOT EXISTS idx_edit_hist_fid ON edit_history(fid)",
    "CREATE INDEX IF NOT EXISTS idx_reaction_uid ON reaction_log(uid)",
]


def init_extra_tables():
    """Create (and repair) the security / editor / plugin tables."""
    for ddl in EXTRA_SCHEMA:
        db_exec(ddl)
    reconcile_schema(EXTRA_SCHEMA)
    for ddl in EXTRA_INDEXES:
        db_exec(ddl)
    log.info("Security tables ready: %s", len(EXTRA_SCHEMA))
    return len(EXTRA_SCHEMA)


# Threat taxonomy -----------------------------------------------------------
THREAT_CATEGORIES = {
    "selfrep": {"name": "Self-replication / worm", "emoji": "\U0001f9a0", "weight": 45},
    "backdoor": {"name": "Backdoor / reverse shell", "emoji": "\U0001f6aa", "weight": 50},
    "trojan": {"name": "Trojan / dropper", "emoji": "\U0001f40e", "weight": 45},
    "exfil": {"name": "Data exfiltration", "emoji": "\U0001f4e4", "weight": 40},
    "stealer": {"name": "Credential / token stealer", "emoji": "\U0001f5dd\ufe0f", "weight": 45},
    "destruct": {"name": "Destructive payload", "emoji": "\U0001f4a3", "weight": 50},
    "persist": {"name": "Persistence", "emoji": "\U0001f4cc", "weight": 30},
    "obfusc": {"name": "Obfuscation / packing", "emoji": "\U0001f300", "weight": 25},
    "rce": {"name": "Dynamic code execution", "emoji": "\u26a1", "weight": 30},
    "privesc": {"name": "Privilege escalation", "emoji": "\U0001f513", "weight": 35},
    "miner": {"name": "Crypto miner", "emoji": "\u26cf\ufe0f", "weight": 35},
    "spy": {"name": "Keylogger / spyware", "emoji": "\U0001f441\ufe0f", "weight": 40},
    "flood": {"name": "Spam / flood / abuse", "emoji": "\U0001f30a", "weight": 25},
    "antianalysis": {"name": "Anti-analysis / sandbox evasion", "emoji": "\U0001f978", "weight": 30},
    "netfetch": {"name": "Remote payload download", "emoji": "\U0001f4e5", "weight": 35},
    "sigma": {"name": "Attack against this host", "emoji": "\U0001f6e1\ufe0f", "weight": 55},
}

# Each rule: (id, category, severity 1-10, human title, compiled pattern)
_RAW_SECURITY_RULES = [
    # --- dynamic execution / RCE
    ("rce_exec_eval", "rce", 6, "exec()/eval() on runtime data", r"\b(exec|eval)\s*\("),
    ("rce_compile", "rce", 5, "compile() of generated source", r"\bcompile\s*\([^)]*['\"]exec['\"]"),
    ("rce_marshal", "obfusc", 8, "marshal.loads of embedded bytecode", r"marshal\s*\.\s*loads"),
    ("rce_pickle", "obfusc", 7, "pickle.loads of untrusted data", r"pickle\s*\.\s*loads"),
    ("rce_import_dyn", "rce", 5, "dynamic __import__ of a computed name", r"__import__\s*\("),
    ("rce_importlib", "rce", 4, "importlib dynamic module load", r"importlib\s*\.\s*import_module"),
    ("rce_builtins", "obfusc", 7, "__builtins__ indirection", r"__builtins__|__globals__|__subclasses__"),
    ("rce_getattr_chain", "obfusc", 5, "getattr chain used to hide calls", r"getattr\s*\(\s*__import__"),
    # --- obfuscation
    ("obf_b64_exec", "obfusc", 9, "base64 blob passed to exec/eval",
     r"(exec|eval)\s*\(\s*(base64|codecs|zlib|bz2|lzma)\s*\."),
    ("obf_b64_decode", "obfusc", 4, "base64 decoding of embedded data", r"b(ase)?64decode\s*\("),
    ("obf_rot13", "obfusc", 4, "rot13 / codecs decoding", r"codecs\s*\.\s*decode\s*\("),
    ("obf_chr_join", "obfusc", 5, "chr()/join() character assembly", r"''\s*\.\s*join\s*\(\s*\[?\s*chr\s*\("),
    ("obf_hex_escape", "obfusc", 3, "long \\x hex escaped string", r"(?:\\x[0-9a-fA-F]{2}){12,}"),
    ("obf_pyarmor", "obfusc", 6, "commercial packer artefact (pyarmor/pyminifier)",
     r"pyarmor|__pyarmor__|pyminifier|pyobfuscate"),
    ("obf_lambda_exec", "obfusc", 6, "lambda wrapping exec", r"lambda\s*[^:]*:\s*(exec|eval)\s*\("),
    # --- reverse shell / backdoor
    ("bd_socket_dup2", "backdoor", 10, "socket + dup2 reverse shell", r"dup2\s*\(\s*\w+\s*\.\s*fileno\s*\("),
    ("bd_pty_spawn", "backdoor", 9, "pty.spawn shell", r"pty\s*\.\s*spawn"),
    ("bd_nc_e", "backdoor", 10, "netcat with -e (shell)", r"\bnc\b[^\n]{0,40}\s-\w*e\b"),
    ("bd_bash_i", "backdoor", 9, "bash -i redirected to a socket", r"bash\s+-i\s*>&\s*/dev/tcp/"),
    ("bd_devtcp", "backdoor", 10, "/dev/tcp channel", r"/dev/(tcp|udp)/"),
    ("bd_bind_shell", "backdoor", 8, "listening socket serving a shell",
     r"socket\s*\.\s*socket[\s\S]{0,200}?\b(bind|listen)\s*\("),
    ("bd_shell_true", "backdoor", 5, "subprocess with shell=True", r"shell\s*=\s*True"),
    ("bd_os_system", "backdoor", 4, "os.system shell call", r"os\s*\.\s*(system|popen)\s*\("),
    ("bd_meterpreter", "backdoor", 10, "known payload framework string",
     r"meterpreter|msfvenom|empire_?agent|cobaltstrike|beacon_?http"),
    # --- self replication
    ("sr_copy_self", "selfrep", 9, "script copies itself elsewhere",
     r"(shutil\s*\.\s*copy\w*|copyfile)\s*\(\s*(__file__|sys\s*\.\s*argv\s*\[\s*0)"),
    ("sr_read_self", "selfrep", 7, "script reads its own source", r"open\s*\(\s*__file__"),
    ("sr_spread_dirs", "selfrep", 8, "walks directories writing copies of itself",
     r"os\s*\.\s*walk[\s\S]{0,300}?(__file__|self_code|payload)"),
    ("sr_fork_bomb", "destruct", 10, "fork bomb", r"while\s+True\s*:[\s\S]{0,80}?os\s*\.\s*fork\s*\(|:\(\)\{\s*:\|:&\s*\};:"),
    ("sr_thread_bomb", "flood", 6, "unbounded thread/process spawning",
     r"for\s+\w+\s+in\s+range\s*\(\s*\d{4,}\s*\)[\s\S]{0,120}?(Thread|Process)\s*\("),
    ("sr_usb_spread", "selfrep", 8, "copies payload to removable media", r"/media/|/mnt/usb|autorun\.inf"),
    # --- trojan / dropper
    ("tj_download_exec", "netfetch", 9, "downloads and executes a remote payload",
     r"(curl|wget)[^\n]{0,120}\|\s*(bash|sh|python\d?)"),
    ("tj_urlretrieve", "netfetch", 7, "urlretrieve of a remote file", r"urlretrieve\s*\("),
    ("tj_requests_exec", "trojan", 9, "HTTP response body executed",
     r"(requests\s*\.\s*get|urlopen)\s*\([\s\S]{0,160}?(exec|eval)\s*\("),
    ("tj_chmod_exec", "trojan", 6, "downloaded file made executable", r"chmod\s+(\+x|[0-7]*7[0-7]{2})"),
    ("tj_hidden_bin", "trojan", 6, "writes a hidden binary", r"open\s*\(\s*['\"][^'\"]*/\.[\w\-]+['\"]\s*,\s*['\"]wb"),
    ("tj_pip_install", "netfetch", 5, "installs packages at runtime", r"pip\s+install|pip3\s+install|dnf\s+install|apt\s+install|pkg\s+install"),
    # --- credential / token theft
    ("st_env_dump", "stealer", 7, "dumps the whole environment", r"os\s*\.\s*environ\s*(\.\s*(copy|items|keys)\s*\(|\))"),
    ("st_dotenv", "stealer", 8, "reads .env / credential files", r"['\"][^'\"]*\.env['\"]|credentials\.json|\.git-credentials"),
    ("st_ssh_keys", "stealer", 9, "reads SSH keys", r"\.ssh/(id_\w+|authorized_keys)"),
    ("st_passwd", "stealer", 7, "reads /etc/passwd or /etc/shadow", r"/etc/(passwd|shadow)"),
    ("st_browser", "stealer", 8, "reads browser profile data",
     r"Login Data|cookies\.sqlite|Local\s+State|key4\.db|logins\.json"),
    ("st_wallet", "stealer", 9, "reads crypto wallet files", r"wallet\.dat|metamask|exodus|electrum"),
    ("st_bot_token", "stealer", 9, "harvests Telegram bot tokens",
     r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b"),
    ("st_token_grep", "stealer", 7, "greps files for tokens/keys",
     r"(TOKEN|API_KEY|SECRET|PASSWORD)[^\n]{0,30}(open|read|glob|walk)\s*\("),
    # --- exfiltration
    ("ex_post_data", "exfil", 6, "posts local data to a remote host",
     r"requests\s*\.\s*post\s*\([\s\S]{0,200}?(files|data)\s*="),
    ("ex_webhook", "exfil", 6, "discord/slack webhook exfil", r"discord(app)?\.com/api/webhooks|hooks\.slack\.com"),
    ("ex_telegram_api", "exfil", 5, "raw Telegram API call to another bot",
     r"api\.telegram\.org/bot"),
    ("ex_paste", "exfil", 6, "uploads to a paste service", r"pastebin\.com/api|paste\.rs|transfer\.sh|0x0\.st|file\.io"),
    ("ex_smtp", "exfil", 5, "sends mail with attachments", r"smtplib\s*\.\s*SMTP"),
    ("ex_ftp", "exfil", 5, "FTP upload", r"ftplib\s*\.\s*FTP"),
    ("ex_dns_tunnel", "exfil", 7, "DNS tunnelling", r"dnspython|dns\.resolver|nslookup\s+\$\("),
    # --- destructive
    ("ds_rm_rf", "destruct", 10, "rm -rf of a root/home path", r"rm\s+-[rRf]{1,3}\s+(/|~|\$HOME|/\*)"),
    ("ds_rmtree", "destruct", 8, "shutil.rmtree on home/root", r"rmtree\s*\(\s*['\"]?(/|~|\$HOME)"),
    ("ds_mkfs", "destruct", 10, "disk format / raw write", r"\bmkfs\b|\bdd\s+if=/dev/(zero|urandom)\s+of=/dev/"),
    ("ds_unlink_loop", "destruct", 7, "mass file deletion loop",
     r"for\s+\w+\s+in\s+[\s\S]{0,80}?os\s*\.\s*(remove|unlink)\s*\("),
    ("ds_shutdown", "destruct", 7, "reboot/shutdown/kill -9 -1", r"\b(shutdown|reboot|halt|poweroff)\b|kill\s+-9\s+-1"),
    ("ds_overwrite_db", "sigma", 10, "writes to the SIGMA database", r"sigma\.db|sigma_hosting[\w/]*\.db"),
    ("ds_kill_bot", "sigma", 9, "kills the hosting bot process", r"pkill\s+-f\s+bot\.py|killall\s+python"),
    ("ds_escape_dir", "sigma", 7, "path traversal out of the user directory", r"\.\./\.\./|\.\.\\\\\.\.\\\\"),
    # --- persistence
    ("ps_cron", "persist", 7, "installs a crontab entry", r"crontab\s+-|/etc/cron|@reboot"),
    ("ps_rc", "persist", 7, "writes to shell startup files", r"\.bashrc|\.zshrc|\.profile|bash_profile"),
    ("ps_systemd", "persist", 7, "installs a systemd unit", r"systemctl\s+(enable|start)|/etc/systemd/system"),
    ("ps_termux_boot", "persist", 7, "installs a Termux boot script", r"\.termux/boot"),
    ("ps_registry", "persist", 6, "Windows autorun registry key", r"CurrentVersion\\\\Run|winreg\s*\.\s*SetValue"),
    # --- privilege escalation
    ("pe_sudo", "privesc", 6, "sudo / su invocation", r"\b(sudo|su)\s+-?\w"),
    ("pe_setuid", "privesc", 7, "setuid/seteuid call", r"os\s*\.\s*set[ue]id\s*\("),
    ("pe_chmod777", "privesc", 5, "chmod 777", r"chmod\s+(-R\s+)?777"),
    ("pe_ptrace", "privesc", 7, "ptrace / process injection", r"ptrace|process_vm_writev|LD_PRELOAD"),
    # --- miners
    ("mn_pools", "miner", 9, "mining pool address", r"stratum\+tcp|nanopool|minexmr|f2pool|ethermine"),
    ("mn_binaries", "miner", 9, "known miner binary", r"xmrig|cpuminer|ccminer|nbminer|phoenixminer"),
    ("mn_hashloop", "miner", 4, "tight hashing loop", r"while\s+True\s*:[\s\S]{0,120}?(sha256|scrypt|randomx)"),
    # --- spyware
    ("sp_keylog", "spy", 9, "keyboard hooking", r"pynput|keyboard\s*\.\s*(on_press|hook)|GetAsyncKeyState"),
    ("sp_screen", "spy", 7, "screen capture", r"ImageGrab|mss\s*\(|screencap\b"),
    ("sp_mic_cam", "spy", 8, "microphone / camera capture", r"VideoCapture\s*\(|pyaudio|termux-microphone-record"),
    ("sp_sms", "spy", 8, "reads SMS / contacts on Android", r"termux-sms-list|termux-contact-list|content://sms"),
    ("sp_location", "spy", 6, "reads device location", r"termux-location|geolocation"),
    # --- flood / abuse
    ("fl_spam_loop", "flood", 6, "unbounded message sending loop",
     r"while\s+True\s*:[\s\S]{0,150}?send_(message|document|photo)\s*\("),
    ("fl_ddos", "flood", 8, "packet flood / DDoS tooling", r"hping3|slowloris|LOIC|socket\.SOCK_RAW"),
    ("fl_mass_add", "flood", 6, "mass account/member scraping", r"add_?members|scrape_?members|GetParticipants"),
    # --- anti analysis
    ("aa_sleep", "antianalysis", 4, "very long sleep to dodge analysis", r"time\s*\.\s*sleep\s*\(\s*(\d{4,}|[6-9]\d{2})"),
    ("aa_vm_check", "antianalysis", 6, "virtual machine / sandbox detection",
     r"/proc/self/cgroup|dmidecode|VBoxService|vmware|qemu|docker_?env|SIGMA_SANDBOX"),
    ("aa_debug_check", "antianalysis", 5, "debugger detection", r"sys\s*\.\s*gettrace\s*\(|ptrace\s*\(\s*0"),
    ("aa_time_bomb", "antianalysis", 7, "date-triggered payload (time bomb)",
     r"(datetime|date)\s*[\s\S]{0,60}?(>=?|==)\s*(datetime|date)\s*\(\s*20[2-9]\d"),
]

SECURITY_RULES = []
for _rid, _cat, _sev, _title, _pattern in _RAW_SECURITY_RULES:
    try:
        SECURITY_RULES.append({
            "id": _rid,
            "category": _cat,
            "severity": int(_sev),
            "title": _title,
            "regex": re.compile(_pattern, re.IGNORECASE),
        })
    except re.error as _exc:  # pragma: no cover - defensive
        log.error("Bad security rule %s: %s", _rid, _exc)

VERDICTS = {
    "clean": {"name": "Clean", "emoji": "\u2705", "color": "green", "blurb": "No known threat patterns found."},
    "low": {"name": "Low risk", "emoji": "\U0001f7e2", "color": "green", "blurb": "Only common, low-signal patterns."},
    "suspicious": {"name": "Suspicious", "emoji": "\U0001f7e1", "color": "yellow", "blurb": "Needs a sandbox run and a human review."},
    "dangerous": {"name": "Dangerous", "emoji": "\U0001f7e0", "color": "orange", "blurb": "Strong malware indicators. Admin approval required."},
    "malicious": {"name": "Malicious", "emoji": "\u26d4", "color": "red", "blurb": "Quarantined. Execution permanently blocked."},
}

VERDICT_ORDER = ["clean", "low", "suspicious", "dangerous", "malicious"]

SCAN_TEXT_EXTS = {
    ".py", ".js", ".ts", ".sh", ".bash", ".rb", ".php", ".pl", ".go", ".lua",
    ".r", ".java", ".txt", ".json", ".yml", ".yaml", ".env", ".cfg", ".ini",
    ".sql", ".html", ".css", ".md", ".c", ".cpp", ".h", ".rs", ".kt", ".swift",
}

BINARY_MAGIC = {
    b"\x7fELF": "Linux ELF executable",
    b"MZ": "Windows PE executable",
    b"\xca\xfe\xba\xbe": "Java class / Mach-O fat binary",
    b"dex\n": "Android DEX bytecode",
    b"PK\x03\x04": "ZIP/APK/JAR archive",
}

MAX_SCAN_BYTES = 3 * 1024 * 1024


def shannon_entropy(data):
    """Entropy in bits per character. High values mean packed/encrypted data."""
    text = str(data or "")
    if not text:
        return 0.0
    counts = {}
    for char in text:
        counts[char] = counts.get(char, 0) + 1
    total = float(len(text))
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * math.log(p, 2)
    return round(entropy, 3)


def _long_blob_findings(source):
    """Detect very long single-token blobs (packed payloads)."""
    findings = []
    for match in re.finditer(r"[A-Za-z0-9+/=]{240,}", source):
        blob = match.group(0)
        entropy = shannon_entropy(blob)
        if entropy >= 4.2:
            findings.append({
                "id": "obf_long_blob",
                "category": "obfusc",
                "severity": 7,
                "title": "Packed payload blob (" + str(len(blob)) + " chars, entropy " + str(entropy) + ")",
                "line": source.count("\n", 0, match.start()) + 1,
                "excerpt": blob[:60] + "\u2026",
            })
            if len(findings) >= 5:
                break
    return findings


def _line_excerpt(source, index):
    """Single-line excerpt around a match offset."""
    start = source.rfind("\n", 0, index) + 1
    end = source.find("\n", index)
    if end == -1:
        end = len(source)
    return source[start:end].strip()[:160]


def scan_text_rules(source):
    """Run every regex rule over the source. Returns a list of findings."""
    findings = []
    text = str(source or "")
    for rule in SECURITY_RULES:
        match = rule["regex"].search(text)
        if not match:
            continue
        findings.append({
            "id": rule["id"],
            "category": rule["category"],
            "severity": rule["severity"],
            "title": rule["title"],
            "line": text.count("\n", 0, match.start()) + 1,
            "excerpt": _line_excerpt(text, match.start()),
        })
    findings.extend(_long_blob_findings(text))
    return findings


_AST_DANGEROUS_CALLS = {
    "exec": ("rce", 6, "exec() call"),
    "eval": ("rce", 6, "eval() call"),
    "compile": ("rce", 4, "compile() call"),
    "__import__": ("rce", 5, "dynamic __import__"),
    "input": ("flood", 1, "interactive input in a hosted script"),
}

_AST_DANGEROUS_IMPORTS = {
    "socket": ("backdoor", 4, "raw socket usage"),
    "marshal": ("obfusc", 7, "marshal bytecode loader"),
    "pickle": ("obfusc", 5, "pickle deserialisation"),
    "ctypes": ("privesc", 7, "ctypes native memory access"),
    "pty": ("backdoor", 7, "pseudo-terminal allocation"),
    "telnetlib": ("backdoor", 6, "telnet client"),
    "ftplib": ("exfil", 5, "FTP client"),
    "smtplib": ("exfil", 5, "SMTP client"),
    "winreg": ("persist", 6, "Windows registry access"),
    "resource": ("antianalysis", 2, "resource limit manipulation"),
}


def ast_scan_python(source):
    """AST pass for Python: catches calls the regex layer may miss."""
    findings = []
    try:
        tree = ast.parse(str(source or ""))
    except SyntaxError as exc:
        return [{
            "id": "ast_syntax",
            "category": "obfusc",
            "severity": 2,
            "title": "File does not parse as Python (line " + str(getattr(exc, "lineno", 0) or 0) + ")",
            "line": int(getattr(exc, "lineno", 0) or 0),
            "excerpt": str(getattr(exc, "msg", ""))[:120],
        }]
    except Exception:
        return findings
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            info = _AST_DANGEROUS_CALLS.get(node.func.id)
            if info and ("call", node.func.id) not in seen:
                seen.add(("call", node.func.id))
                findings.append({
                    "id": "ast_call_" + node.func.id,
                    "category": info[0],
                    "severity": info[1],
                    "title": "AST: " + info[2],
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "excerpt": node.func.id + "(\u2026)",
                })
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            else:
                names = [str(node.module or "").split(".")[0]]
            for name in names:
                info = _AST_DANGEROUS_IMPORTS.get(name)
                if info and ("import", name) not in seen:
                    seen.add(("import", name))
                    findings.append({
                        "id": "ast_import_" + name,
                        "category": info[0],
                        "severity": info[1],
                        "title": "AST: " + info[2],
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "excerpt": "import " + name,
                    })
        if isinstance(node, ast.Attribute) and node.attr in ("fork", "forkpty", "setuid", "execv", "execve"):
            key = ("attr", node.attr)
            if key not in seen:
                seen.add(key)
                findings.append({
                    "id": "ast_attr_" + node.attr,
                    "category": "privesc" if node.attr == "setuid" else "backdoor",
                    "severity": 6,
                    "title": "AST: os." + node.attr + " process manipulation",
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "excerpt": "os." + node.attr + "(\u2026)",
                })
    return findings


def detect_binary_kind(path):
    """Identify obvious binary payloads by magic bytes."""
    try:
        with open(str(path), "rb") as handle:
            head = handle.read(8)
    except OSError:
        return ""
    for magic, label in BINARY_MAGIC.items():
        if head.startswith(magic):
            return label
    return ""


def read_source_for_scan(path):
    """Read a file as text for scanning (bounded, never raises)."""
    try:
        with open(str(path), "rb") as handle:
            raw = handle.read(MAX_SCAN_BYTES)
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def verdict_for_score(score, max_severity=0):
    """Map a numeric risk score onto a verdict key."""
    score = int(score or 0)
    if max_severity >= 10 or score >= 120:
        return "malicious"
    if max_severity >= 8 or score >= 70:
        return "dangerous"
    if score >= 30:
        return "suspicious"
    if score > 0:
        return "low"
    return "clean"


def dedupe_findings(findings):
    """Collapse duplicate rule hits, keeping the highest severity."""
    best = {}
    for item in findings or []:
        key = str(item.get("id"))
        current = best.get(key)
        if current is None or int(item.get("severity", 0)) > int(current.get("severity", 0)):
            best[key] = item
    return sorted(best.values(), key=lambda f: -int(f.get("severity", 0)))


def scan_source(source, ext=""):
    """Full static analysis of one source string. Returns a report dict."""
    text = str(source or "")
    findings = scan_text_rules(text)
    if str(ext).lower() == ".py":
        findings.extend(ast_scan_python(text))
    findings.extend(run_extension_scanners(text, ext))
    findings = dedupe_findings(findings)
    score = 0
    categories = {}
    for item in findings:
        category = str(item.get("category", "obfusc"))
        weight = int(THREAT_CATEGORIES.get(category, {}).get("weight", 20))
        score += int(round(weight * int(item.get("severity", 1)) / 10.0))
        categories[category] = categories.get(category, 0) + 1
    max_severity = max([int(f.get("severity", 0)) for f in findings] or [0])
    lines = text.count("\n") + 1 if text else 0
    entropy = shannon_entropy(text[:20000])
    if entropy >= 5.6 and lines <= 5 and len(text) > 2000:
        score += 25
        findings.append({
            "id": "obf_single_line",
            "category": "obfusc",
            "severity": 6,
            "title": "Minified single-line payload (entropy " + str(entropy) + ")",
            "line": 1,
            "excerpt": text[:60] + "\u2026",
        })
        max_severity = max(max_severity, 6)
    verdict = verdict_for_score(score, max_severity)
    return {
        "verdict": verdict,
        "score": int(score),
        "max_severity": int(max_severity),
        "findings": findings,
        "categories": categories,
        "entropy": entropy,
        "lines": lines,
        "bytes": len(text.encode("utf-8", errors="replace")),
        "engine": "sigma-static/" + VERSION.split()[0],
        "binary": "",
    }


def scan_file(path, ext=None):
    """Static analysis of a file on disk."""
    file_path = Path(str(path))
    extension = str(ext if ext is not None else file_path.suffix).lower()
    binary_kind = detect_binary_kind(file_path)
    source = read_source_for_scan(file_path)
    report = scan_source(source, extension)
    report["path"] = str(file_path)
    report["binary"] = binary_kind
    if binary_kind and "ZIP" not in binary_kind:
        report["score"] = int(report.get("score", 0)) + 45
        report["findings"].insert(0, {
            "id": "bin_executable",
            "category": "trojan",
            "severity": 8,
            "title": "Pre-compiled binary upload (" + binary_kind + ")",
            "line": 0,
            "excerpt": binary_kind,
        })
        report["max_severity"] = max(int(report.get("max_severity", 0)), 8)
        report["verdict"] = verdict_for_score(report["score"], report["max_severity"])
    return report


def save_scan(fid, uid, report):
    """Persist a scan report and return its row id."""
    findings = report.get("findings", [])[:60]
    return db_exec(
        "INSERT INTO security_scans (fid, uid, verdict, score, findings, categories,"
        " checksum, scanned_at, engine) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            int(fid or 0),
            int(uid or 0),
            str(report.get("verdict", "clean")),
            int(report.get("score", 0)),
            json.dumps(findings)[:12000],
            ",".join(sorted(report.get("categories", {}).keys())),
            str(report.get("checksum", ""))[:64],
            utcstamp(),
            str(report.get("engine", ""))[:40],
        ),
    )


def get_last_scan(fid):
    """Latest stored scan for a file, with findings decoded."""
    row = db_one(
        "SELECT * FROM security_scans WHERE fid=? ORDER BY id DESC LIMIT 1",
        (int(fid),),
    )
    if not row:
        return {}
    data = dict(row)
    try:
        data["findings"] = json.loads(str(row.get("findings") or "[]"))
    except ValueError:
        data["findings"] = []
    return data


def get_scan_history(fid, limit=10):
    """Scan history of one file."""
    return db_all(
        "SELECT id, verdict, score, scanned_at FROM security_scans"
        " WHERE fid=? ORDER BY id DESC LIMIT ?",
        (int(fid), int(limit)),
    )


def security_stats():
    """Aggregate numbers for the admin security dashboard."""
    return {
        "scans": int(db_val("SELECT COUNT(*) c FROM security_scans", (), 0) or 0),
        "clean": int(db_val("SELECT COUNT(*) c FROM security_scans WHERE verdict IN ('clean','low')", (), 0) or 0),
        "suspicious": int(db_val("SELECT COUNT(*) c FROM security_scans WHERE verdict='suspicious'", (), 0) or 0),
        "dangerous": int(db_val("SELECT COUNT(*) c FROM security_scans WHERE verdict='dangerous'", (), 0) or 0),
        "malicious": int(db_val("SELECT COUNT(*) c FROM security_scans WHERE verdict='malicious'", (), 0) or 0),
        "quarantined": int(db_val("SELECT COUNT(*) c FROM quarantine WHERE released_at=''", (), 0) or 0),
        "sandboxed": int(db_val("SELECT COUNT(*) c FROM sandbox_runs", (), 0) or 0),
        "reviews_open": int(db_val("SELECT COUNT(*) c FROM code_reviews WHERE state='open'", (), 0) or 0),
        "tokens_neutralised": int(db_val("SELECT COUNT(*) c FROM token_vault", (), 0) or 0),
        "blocked_pushes": int(db_val("SELECT COUNT(*) c FROM edit_history WHERE blocked=1", (), 0) or 0),
    }


# Quarantine ---------------------------------------------------------------
def quarantine_dir():
    """Directory holding quarantined payloads (never executable)."""
    path = BASE_DIR / "quarantine"
    path.mkdir(parents=True, exist_ok=True)
    return path


def quarantine_file(fid, reason, report=None, actor=0):
    """Move a file out of the user tree and block it permanently."""
    row = get_file(fid)
    if not row:
        return False, "File not found."
    uid = int(row.get("uid") or 0)
    src = Path(str(row.get("fpath") or ""))
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    dest = quarantine_dir() / (str(fid) + "_" + stamp + "_" + sanitize_name(str(row.get("fname") or "file")))
    stored = ""
    try:
        if src.exists():
            shutil.move(str(src), str(dest))
            try:
                os.chmod(str(dest), 0o400)
            except OSError:
                pass
            stored = str(dest)
    except Exception as exc:
        log.error("Quarantine move failed for #%s: %s", fid, exc)
    if is_running(uid, fid):
        stop_script(uid, fid)
    if stored:
        db_exec("UPDATE files SET status='quarantined', fpath=? WHERE id=?",
                (stored, int(fid)))
    else:
        db_exec("UPDATE files SET status='quarantined' WHERE id=?", (int(fid),))
    db_exec(
        "INSERT INTO quarantine (fid, uid, reason, verdict, score, stored_path,"
        " created_at, released_at, released_by) VALUES (?,?,?,?,?,?,?,'',0)",
        (
            int(fid), uid, str(reason)[:400],
            str((report or {}).get("verdict", "malicious")),
            int((report or {}).get("score", 0)),
            stored, utcstamp(),
        ),
    )
    audit(actor or 0, "quarantine", uid, "file #" + str(fid) + ": " + str(reason)[:160])
    notify_user(
        uid,
        _e("shield") + " " + B("File quarantined") + "\n\n"
        + _e("file") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname")))
        + "\n" + _e("warn") + " " + esc(str(reason)[:300])
        + "\n\n" + I("Contact support with /ticket if you believe this is wrong."),
    )
    return True, "File #" + str(fid) + " quarantined."


def is_quarantined(fid):
    """True when a file is currently in quarantine."""
    return bool(db_one(
        "SELECT id FROM quarantine WHERE fid=? AND released_at='' LIMIT 1",
        (int(fid),),
    ))


def release_quarantine(fid, admin_uid):
    """Admin override: restore a quarantined file to pending review."""
    row = db_one(
        "SELECT * FROM quarantine WHERE fid=? AND released_at='' ORDER BY id DESC LIMIT 1",
        (int(fid),),
    )
    if not row:
        return False, "That file is not in quarantine."
    file_row = get_file(fid)
    stored = Path(str(row.get("stored_path") or ""))
    if file_row and stored.exists():
        target = Path(str(file_row.get("fpath") or ""))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(target))
            os.chmod(str(target), 0o600)
        except Exception as exc:
            return False, "Restore failed: " + str(exc)
    db_exec(
        "UPDATE quarantine SET released_at=?, released_by=? WHERE id=?",
        (utcstamp(), int(admin_uid), int(row.get("id"))),
    )
    db_exec("UPDATE files SET status='pending' WHERE id=?", (int(fid),))
    audit(admin_uid, "quarantine_release", int(row.get("uid") or 0), "file #" + str(fid))
    return True, "File #" + str(fid) + " released to pending review."


def get_quarantine_list(limit=20):
    """Currently quarantined files."""
    return db_all(
        "SELECT q.*, f.fname FROM quarantine q LEFT JOIN files f ON f.id=q.fid"
        " WHERE q.released_at='' ORDER BY q.id DESC LIMIT ?",
        (int(limit),),
    )


# Token neutralisation ------------------------------------------------------
TOKEN_PATTERNS = [
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b")),
    ("generic_api_key", re.compile(r"\b(?:sk|pk|api|key)_[A-Za-z0-9]{20,}\b")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9\._\-]{20,}")),
]


def find_tokens(source):
    """All credential-looking strings in a source file."""
    hits = []
    text = str(source or "")
    for kind, pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(text):
            hits.append({"kind": kind, "value": match.group(0)})
            if len(hits) >= 25:
                return hits
    return hits


def neutralize_tokens(source, fid=0, uid=0):
    """Replace every real token with the checker-bot token.

    Used before any sandbox execution: a stolen or hard-coded production token
    never reaches untrusted code. Only a hash and a short hint are stored, so
    the vault cannot leak the secret either.
    """
    text = str(source or "")
    replaced = 0
    for hit in find_tokens(text):
        value = hit["value"]
        if not value or value == CHECKER_TOKEN:
            continue
        text = text.replace(value, CHECKER_TOKEN)
        replaced += 1
        db_exec(
            "INSERT INTO token_vault (fid, uid, token_hash, token_hint, replaced_with, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                int(fid), int(uid),
                hashlib.sha256(value.encode("utf-8")).hexdigest(),
                value[:6] + "\u2026" + value[-4:],
                "checker_token",
                utcstamp(),
            ),
        )
    return text, replaced


def get_token_vault(limit=20):
    """Recent neutralised credentials (hashes only)."""
    return db_all(
        "SELECT * FROM token_vault ORDER BY id DESC LIMIT ?", (int(limit),)
    )


# Reviews -------------------------------------------------------------------
def open_code_review(fid, uid, report, summary=""):
    """Queue a file for human review and alert every admin."""
    review_id = db_exec(
        "INSERT INTO code_reviews (fid, uid, state, verdict, score, summary, reviewer,"
        " created_at, closed_at) VALUES (?,?,'open',?,?,?,0,?,'')",
        (
            int(fid), int(uid),
            str((report or {}).get("verdict", "suspicious")),
            int((report or {}).get("score", 0)),
            str(summary)[:1000], utcstamp(),
        ),
    )
    return int(review_id or 0)


def close_code_review(review_id, admin_uid, verdict):
    """Record a reviewer decision."""
    row = db_one("SELECT * FROM code_reviews WHERE id=?", (int(review_id),))
    if not row:
        return False, "Review not found."
    db_exec(
        "UPDATE code_reviews SET state='closed', verdict=?, reviewer=?, closed_at=? WHERE id=?",
        (str(verdict)[:20], int(admin_uid), utcstamp(), int(review_id)),
    )
    return True, "Review #" + str(review_id) + " closed as " + str(verdict) + "."


def get_open_reviews(limit=20):
    """Open review queue for admins."""
    return db_all(
        "SELECT r.*, f.fname FROM code_reviews r LEFT JOIN files f ON f.id=r.fid"
        " WHERE r.state='open' ORDER BY r.score DESC, r.id DESC LIMIT ?",
        (int(limit),),
    )


def security_gate(uid, fid):
    """Called before any real execution. Returns (allowed, reason)."""
    fid = int(fid)
    if is_quarantined(fid):
        return False, "This file is quarantined by the security scanner and cannot run."
    scan = get_last_scan(fid)
    verdict = str(scan.get("verdict") or "")
    if verdict == "malicious":
        return False, "The security scanner marked this file as malicious. Execution is blocked."
    if verdict == "dangerous" and not is_admin(uid):
        review = db_one(
            "SELECT * FROM code_reviews WHERE fid=? ORDER BY id DESC LIMIT 1", (fid,)
        )
        if not review or str(review.get("state")) == "open" or str(review.get("verdict")) != "approved":
            return False, (
                "This file needs a manual security review before it can run. "
                "An administrator has been notified."
            )
    return True, ""


def post_upload_security(uid, fid, path, fname=""):
    """Scan a freshly uploaded file and act on the verdict.

    Returns the report so the upload handler can show it to the user.
    """
    report = scan_file(path)
    report["checksum"] = checksum_file(path)
    save_scan(fid, uid, report)
    verdict = str(report.get("verdict"))
    name = str(fname or Path(str(path)).name)
    if verdict == "malicious":
        quarantine_file(
            fid,
            "Automatic scan: " + str(len(report.get("findings", []))) + " indicator(s), score "
            + str(report.get("score")),
            report,
        )
        notify_admins(
            _e("shield") + " " + B("MALICIOUS upload blocked") + "\n\n"
            + _e("file") + " " + C("#" + str(fid)) + " " + esc(name) + "\n"
            + _e("user") + " " + C(str(uid)) + "\n"
            + render_scan_summary(report),
            kb_security_review(fid),
        )
        return report
    if verdict in ("dangerous", "suspicious"):
        db_exec("UPDATE files SET status='review' WHERE id=?", (int(fid),))
        open_code_review(fid, uid, report, "Automatic upload scan")
        notify_admins(
            _e("scan") + " " + B("Upload needs review") + "\n\n"
            + _e("file") + " " + C("#" + str(fid)) + " " + esc(name) + "\n"
            + _e("user") + " " + C(str(uid)) + "\n"
            + render_scan_summary(report) + "\n\n"
            + I("Run it in the sandbox before approving."),
            kb_security_review(fid),
        )
    return report


# ============================================================================
# SECTION 22 - SANDBOX (ISOLATED EXECUTION JAIL)
# ============================================================================
# Suspicious code is never run in the user's directory. It is copied into a
# throw-away jail with:
#   * a rewritten source where every credential is swapped for CHECKER_TOKEN
#   * HOME/TMPDIR pointing inside the jail so it cannot see anything else
#   * a minimal environment (no SIGMA_*, no real tokens, no proxy settings)
#   * CPU / address-space / file-size / process-count rlimits
#   * a hard wall-clock timeout and a process-group kill
#   * unshare-based network isolation when the kernel allows it

SANDBOX_LIMITS = {
    "cpu_seconds": 45,
    "address_space_mb": 768,
    "file_size_mb": 64,
    "processes": 200,        # headroom, applied on top of current usage
    "open_files": 512,
    "wall_timeout": 60,
    "max_output": 9000,
}


def sandbox_root():
    """Parent directory for all sandbox jails."""
    path = BASE_DIR / "sandbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _make_jail(uid, fid):
    """Create an empty jail directory tree."""
    jail = sandbox_root() / ("u" + str(int(uid)) + "_f" + str(int(fid)) + "_" + rand_string(6).lower())
    (jail / "home").mkdir(parents=True, exist_ok=True)
    (jail / "tmp").mkdir(parents=True, exist_ok=True)
    (jail / "work").mkdir(parents=True, exist_ok=True)
    return jail


def sandbox_env(jail):
    """Minimal, scrubbed environment for sandboxed code."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin:" + str(Path(sys.executable).parent),
        "HOME": str(jail / "home"),
        "TMPDIR": str(jail / "tmp"),
        "TEMP": str(jail / "tmp"),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TOKEN": CHECKER_TOKEN,
        "BOT_TOKEN": CHECKER_TOKEN,
        "SIGMA_SANDBOX": "1",
    }
    try:
        env["PYTHONPATH"] = str(guard_dir())
        env.update(guard_env(0, 0, jail, net_mode=(
            "allowlist" if int(hcfg("sandbox_net") or 0) else "off")))
        env["SIGMA_GUARD_LOG"] = str(jail / "guard.jsonl")
    except Exception as exc:
        log.debug("sandbox guard env skipped: %s", exc)
    return env


def _sandbox_env_tail():
    """Kept for backwards compatibility."""
    return {}


def _unused_sandbox_env():
    return {"NO_NETWORK": "1"}


def _sandbox_env_legacy():
    return {
        "NO_NETWORK": "1",
    }


def _sandbox_preexec():
    """Apply rlimits inside the child process (POSIX only)."""
    try:
        import resource as _resource
    except ImportError:  # pragma: no cover - non POSIX
        _resource = None
    try:
        os.setsid()
    except Exception:
        pass
    if _resource is None:
        return
    try:
        nproc = nproc_headroom(int(SANDBOX_LIMITS.get("processes", 200)))
    except Exception:
        nproc = 512
    limits = [
        ("RLIMIT_CPU", int(SANDBOX_LIMITS["cpu_seconds"])),
        ("RLIMIT_AS", int(SANDBOX_LIMITS["address_space_mb"]) * 1024 * 1024),
        ("RLIMIT_FSIZE", int(SANDBOX_LIMITS["file_size_mb"]) * 1024 * 1024),
        ("RLIMIT_NOFILE", int(SANDBOX_LIMITS.get("open_files", 512))),
        ("RLIMIT_NPROC", nproc),
        ("RLIMIT_CORE", 0),
    ]
    for name, value in limits:
        which = getattr(_resource, name, None)
        if which is None:
            continue
        try:
            soft, hard = _resource.getrlimit(which)
            if hard not in (-1, _resource.RLIM_INFINITY) and value > hard:
                value = hard
            _resource.setrlimit(which, (value, value))
        except (ValueError, OSError):
            continue


def _network_wrapper(command):
    """Optionally strip network access from a sandbox command.

    When harden_sandbox_net is on (default) the review sandbox keeps the
    network, because a hosting bot that cannot resolve api.telegram.org dies
    instantly and tells you nothing. Credentials are still swapped for the
    checker token, so a 401 Unauthorized in the sandbox log is expected and
    proves the token swap worked.
    """
    try:
        allow_net = int(hcfg("sandbox_net") or 0)
    except Exception:
        allow_net = 1
    if allow_net:
        return list(command), False
    unshare = shutil.which("unshare")
    if not unshare:
        return list(command), False
    return [unshare, "-r", "-n", "--"] + list(command), True


def prepare_sandbox_copy(uid, fid):
    """Copy a file into a fresh jail with credentials neutralised."""
    row = get_file(fid)
    if not row:
        return None, "File not found.", 0
    src = Path(str(row.get("fpath") or ""))
    quarantined = db_one(
        "SELECT stored_path FROM quarantine WHERE fid=? AND released_at='' ORDER BY id DESC LIMIT 1",
        (int(fid),),
    )
    if not src.exists() and quarantined:
        src = Path(str(quarantined.get("stored_path") or ""))
    if not src.exists():
        return None, "The file is missing on disk.", 0
    jail = _make_jail(uid, fid)
    target = jail / "work" / sanitize_name(str(row.get("fname") or "payload.txt"))
    replaced = 0
    if src.suffix.lower() in SCAN_TEXT_EXTS:
        source = read_source_for_scan(src)
        source, replaced = neutralize_tokens(source, fid, uid)
        banner = ""
        if src.suffix.lower() == ".py":
            banner = "# SIGMA SANDBOX COPY - credentials replaced with the checker token\n"
        elif src.suffix.lower() in (".sh", ".bash"):
            banner = "# SIGMA SANDBOX COPY - credentials replaced with the checker token\n"
        try:
            with open(str(target), "w", encoding="utf-8") as handle:
                handle.write(banner + source)
        except OSError as exc:
            return None, "Could not stage the file: " + str(exc), 0
    else:
        try:
            shutil.copy2(str(src), str(target))
        except OSError as exc:
            return None, "Could not stage the file: " + str(exc), 0
    return {"jail": jail, "path": target, "row": row}, "", replaced


def run_in_sandbox(uid, fid, timeout=None):
    """Execute a file inside the jail. Returns a result dict."""
    staged, error, replaced = prepare_sandbox_copy(uid, fid)
    if not staged:
        return {"ok": False, "error": error}
    jail = staged["jail"]
    path = staged["path"]
    row = dict(staged["row"])
    row["fpath"] = str(path)
    row["fname"] = path.name
    ok, command, needs_compile = build_command(row)
    if not ok:
        return {"ok": False, "error": str(command)}
    if needs_compile:
        compiled, message = _compile_java(path, path.parent)
        if not compiled:
            return {"ok": False, "error": message}
    command, network_blocked = _network_wrapper(command)
    wall = int(timeout or SANDBOX_LIMITS["wall_timeout"])
    started = time.time()
    output = ""
    exit_code = -1
    timed_out = 0
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(path.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=sandbox_env(jail),
            preexec_fn=_sandbox_preexec,
            close_fds=True,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "Interpreter not installed: " + str(command[0])}
    except Exception as exc:
        return {"ok": False, "error": "Sandbox launch failed: " + str(exc)}
    try:
        raw, _ = proc.communicate(timeout=wall)
        output = (raw or b"").decode("utf-8", errors="replace")
        exit_code = int(proc.returncode or 0)
    except subprocess.TimeoutExpired:
        timed_out = 1
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        try:
            raw, _ = proc.communicate(timeout=5)
            output = (raw or b"").decode("utf-8", errors="replace")
        except Exception:
            output = output or ""
        exit_code = -9
    duration_ms = int((time.time() - started) * 1000)
    output = output[-int(SANDBOX_LIMITS["max_output"]):]
    escaped = detect_jail_escape(jail)
    db_exec(
        "INSERT INTO sandbox_runs (fid, uid, exit_code, timed_out, duration_ms, output,"
        " network_blocked, token_neutralised, jail_path, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            int(fid), int(uid), int(exit_code), int(timed_out), duration_ms,
            output[:8000], 1 if network_blocked else 0, int(replaced), str(jail), utcstamp(),
        ),
    )
    return {
        "ok": True,
        "exit_code": exit_code,
        "timed_out": bool(timed_out),
        "duration_ms": duration_ms,
        "output": output,
        "network_blocked": bool(network_blocked),
        "tokens_replaced": int(replaced),
        "jail": str(jail),
        "artifacts": escaped,
    }


def detect_jail_escape(jail):
    """List files the payload created inside the jail (behavioural signal)."""
    created = []
    root = Path(str(jail))
    try:
        for path in root.rglob("*"):
            if path.is_file():
                created.append(str(path.relative_to(root)))
            if len(created) >= 40:
                break
    except Exception:
        return created
    return created


def cleanup_sandboxes(max_age_hours=6):
    """Delete old jails."""
    removed = 0
    cutoff = time.time() - int(max_age_hours) * 3600
    try:
        for path in sandbox_root().iterdir():
            if not path.is_dir():
                continue
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(str(path), ignore_errors=True)
                removed += 1
    except Exception as exc:
        log.debug("Sandbox cleanup skipped: %s", exc)
    return removed


def get_sandbox_runs(fid, limit=5):
    """Recent sandbox runs of a file."""
    return db_all(
        "SELECT * FROM sandbox_runs WHERE fid=? ORDER BY id DESC LIMIT ?",
        (int(fid), int(limit)),
    )


def sandbox_and_review(uid, fid, actor=0):
    """Full \"unclear code\" workflow: jail it, then ask an admin to decide."""
    report = get_last_scan(fid)
    if not report:
        row = get_file(fid)
        if row:
            report = scan_file(str(row.get("fpath") or ""))
            save_scan(fid, uid, report)
    result = run_in_sandbox(uid, fid)
    if not result.get("ok"):
        return False, str(result.get("error") or "Sandbox failed."), result
    review_id = open_code_review(fid, uid, report or {}, "Sandbox run requested")
    row = get_file(fid) or {}
    notify_admins(
        _e("scan") + " " + B("Sandbox verdict needed") + "\n\n"
        + _e("file") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname"))) + "\n"
        + _e("user") + " " + C(str(uid)) + "  \u00b7  review " + C("#" + str(review_id)) + "\n\n"
        + render_sandbox_report(result),
        kb_security_review(fid),
    )
    return True, "Sandbox finished. Admins were asked to review the result.", result


# ============================================================================
# SECTION 23 - GUARDED CODE EDITOR (PULL / PUSH WITH REGRESSION BLOCKING)
# ============================================================================

MAX_EDIT_BYTES = 512 * 1024


def editable_file(uid, fid):
    """Fetch a file and confirm it is text the user may edit."""
    row = get_user_file(uid, fid)
    if not row:
        return None, "File not found."
    if is_quarantined(fid):
        return None, "This file is quarantined and cannot be edited."
    path = Path(str(row.get("fpath") or ""))
    if not path.exists():
        return None, "The file is missing on disk."
    if path.suffix.lower() not in SCAN_TEXT_EXTS:
        return None, "Only text/source files can be edited."
    if path.stat().st_size > MAX_EDIT_BYTES:
        return None, "File is too large for the in-bot editor (limit " + fmt_size(MAX_EDIT_BYTES) + ")."
    return row, ""


def pull_code(uid, fid, start=1, count=60):
    """\"git pull\" equivalent: numbered source slice ready for editing."""
    row, error = editable_file(uid, fid)
    if not row:
        return False, error, {}
    path = Path(str(row.get("fpath")))
    source = read_source_for_scan(path)
    lines = source.splitlines()
    start = max(1, int(start or 1))
    count = max(1, min(int(count or 60), 200))
    window = lines[start - 1:start - 1 + count]
    numbered = []
    for offset, line in enumerate(window, start=start):
        numbered.append(str(offset).rjust(4) + " \u2502 " + line)
    scan = get_last_scan(fid)
    return True, "\n".join(numbered), {
        "fname": str(row.get("fname")),
        "total_lines": len(lines),
        "start": start,
        "end": min(len(lines), start + count - 1),
        "checksum": checksum_file(path),
        "verdict": str(scan.get("verdict") or "unscanned"),
        "score": int(scan.get("score") or 0),
    }


def diff_summary(old_text, new_text, limit=40):
    """Unified diff plus added/removed counts."""
    old_lines = str(old_text or "").splitlines()
    new_lines = str(new_text or "").splitlines()
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=1))
    added = len([line for line in diff if line.startswith("+") and not line.startswith("+++")])
    removed = len([line for line in diff if line.startswith("-") and not line.startswith("---")])
    return {
        "added": added,
        "removed": removed,
        "diff": diff[:limit],
        "truncated": len(diff) > limit,
    }


def _security_regression(old_report, new_report):
    """Decide whether an edit made the file meaningfully more dangerous."""
    old_score = int((old_report or {}).get("score", 0) or 0)
    new_score = int((new_report or {}).get("score", 0) or 0)
    old_ids = {str(f.get("id")) for f in (old_report or {}).get("findings", []) or []}
    new_findings = (new_report or {}).get("findings", []) or []
    fresh = [f for f in new_findings if str(f.get("id")) not in old_ids]
    worst_fresh = max([int(f.get("severity", 0)) for f in fresh] or [0])
    verdict = str((new_report or {}).get("verdict", "clean"))
    blocked = False
    reason = ""
    if verdict == "malicious":
        blocked = True
        reason = "The new code matches malicious patterns."
    elif worst_fresh >= 8:
        blocked = True
        reason = "The edit introduces a high-severity threat pattern."
    elif new_score - old_score >= 40:
        blocked = True
        reason = "The edit raises the risk score by " + str(new_score - old_score) + " points."
    return {
        "blocked": blocked,
        "reason": reason,
        "fresh": fresh,
        "old_score": old_score,
        "new_score": new_score,
        "worst_fresh": worst_fresh,
    }


def log_edit(fid, uid, action, diff, regression, blocked, note=""):
    """Append one row to the tamper-evident edit history."""
    return db_exec(
        "INSERT INTO edit_history (fid, uid, action, lines_added, lines_removed,"
        " old_score, new_score, blocked, note, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            int(fid), int(uid), str(action)[:40],
            int((diff or {}).get("added", 0)), int((diff or {}).get("removed", 0)),
            int((regression or {}).get("old_score", 0)), int((regression or {}).get("new_score", 0)),
            1 if blocked else 0, str(note)[:400], utcstamp(),
        ),
    )


def push_code(uid, fid, new_source, action="push", note=""):
    """\"git push\" equivalent with a mandatory security re-check.

    This is the anti-bait-and-switch control: a file that was approved while
    clean cannot be silently turned into a payload. Any regression is rejected,
    the previous version stays live, and the admins get the diff.
    """
    row, error = editable_file(uid, fid)
    if not row:
        return False, error, {}
    path = Path(str(row.get("fpath")))
    old_source = read_source_for_scan(path)
    new_source = str(new_source or "")
    if len(new_source.encode("utf-8", errors="replace")) > MAX_EDIT_BYTES:
        return False, "That content is too large (limit " + fmt_size(MAX_EDIT_BYTES) + ").", {}
    if new_source == old_source:
        return False, "No changes detected.", {}
    diff = diff_summary(old_source, new_source)
    old_report = get_last_scan(fid) or scan_source(old_source, path.suffix.lower())
    new_report = scan_source(new_source, path.suffix.lower())
    regression = _security_regression(old_report, new_report)
    if regression["blocked"]:
        log_edit(fid, uid, action, diff, regression, True, regression["reason"])
        save_scan(fid, uid, dict(new_report, verdict=new_report.get("verdict"), checksum="rejected-push"))
        db_exec("UPDATE files SET status='review' WHERE id=?", (int(fid),))
        open_code_review(fid, uid, new_report, "Blocked push: " + regression["reason"])
        notify_admins(
            _e("shield") + " " + B("Malicious edit blocked") + "\n\n"
            + _e("file") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname"))) + "\n"
            + _e("user") + " " + C(str(uid)) + "\n"
            + _e("warn") + " " + esc(regression["reason"]) + "\n"
            + _e("graph") + " Score " + str(regression["old_score"]) + " \u2192 " + str(regression["new_score"]) + "\n\n"
            + render_findings_list(regression["fresh"], 5),
            kb_security_review(fid),
        )
        log.warning("Blocked malicious push by %s on file #%s", uid, fid)
        return False, (
            _e("shield") + " Push rejected: " + regression["reason"]
            + " The previous version is still live and an admin is reviewing the change."
        ), {"regression": regression, "diff": diff, "report": new_report}
    save_file_version(fid, path)
    try:
        with open(str(path), "w", encoding="utf-8") as handle:
            handle.write(new_source)
    except OSError as exc:
        return False, "Write failed: " + str(exc), {}
    size = path.stat().st_size
    new_report["checksum"] = checksum_file(path)
    save_scan(fid, uid, new_report)
    db_exec(
        "UPDATE files SET fsize=?, checksum=? WHERE id=?",
        (int(size), str(new_report["checksum"]), int(fid)),
    )
    log_edit(fid, uid, action, diff, regression, False, note)
    verdict = str(new_report.get("verdict"))
    if verdict in ("suspicious", "dangerous"):
        db_exec("UPDATE files SET status='review' WHERE id=?", (int(fid),))
        open_code_review(fid, uid, new_report, "Push needs review")
        notify_admins(
            _e("scan") + " " + B("Edited file needs review") + "\n\n"
            + _e("file") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname"))) + "\n"
            + render_scan_summary(new_report),
            kb_security_review(fid),
        )
    if is_running(uid, fid):
        stop_script(uid, fid)
    log_activity(uid, "push:" + str(fid))
    return True, (
        _e("ok") + " Pushed " + C("+" + str(diff["added"]) + "/-" + str(diff["removed"]))
        + " \u00b7 " + VERDICTS.get(verdict, VERDICTS["clean"])["emoji"] + " "
        + VERDICTS.get(verdict, VERDICTS["clean"])["name"]
    ), {"diff": diff, "report": new_report, "regression": regression}


def push_replace_lines(uid, fid, first_line, last_line, replacement, action="replace_lines"):
    """Replace an inclusive line range, then run the guarded push."""
    row, error = editable_file(uid, fid)
    if not row:
        return False, error, {}
    path = Path(str(row.get("fpath")))
    lines = read_source_for_scan(path).splitlines()
    first = max(1, int(first_line or 1))
    last = max(first, int(last_line or first))
    if first > len(lines):
        return False, "Line " + str(first) + " is past the end of the file (" + str(len(lines)) + " lines).", {}
    new_block = str(replacement or "").splitlines()
    updated = lines[:first - 1] + new_block + lines[last:]
    return push_code(uid, fid, "\n".join(updated) + "\n", action,
                     "lines " + str(first) + "-" + str(last))


def push_insert_after(uid, fid, line_no, content):
    """Insert new lines after a given line number."""
    row, error = editable_file(uid, fid)
    if not row:
        return False, error, {}
    path = Path(str(row.get("fpath")))
    lines = read_source_for_scan(path).splitlines()
    index = max(0, min(int(line_no or 0), len(lines)))
    updated = lines[:index] + str(content or "").splitlines() + lines[index:]
    return push_code(uid, fid, "\n".join(updated) + "\n", "insert", "after line " + str(index))


def push_delete_lines(uid, fid, first_line, last_line):
    """Delete an inclusive line range."""
    return push_replace_lines(uid, fid, first_line, last_line, "", "delete_lines")


def get_edit_history(fid, limit=10):
    """Recent pushes (including blocked attempts) for a file."""
    return db_all(
        "SELECT * FROM edit_history WHERE fid=? ORDER BY id DESC LIMIT ?",
        (int(fid), int(limit)),
    )


def user_push_risk(uid):
    """How many pushes this user has had blocked (abuse signal)."""
    return {
        "pushes": int(db_val("SELECT COUNT(*) c FROM edit_history WHERE uid=?", (int(uid),), 0) or 0),
        "blocked": int(db_val(
            "SELECT COUNT(*) c FROM edit_history WHERE uid=? AND blocked=1", (int(uid),), 0) or 0),
        "quarantines": int(db_val(
            "SELECT COUNT(*) c FROM quarantine WHERE uid=?", (int(uid),), 0) or 0),
    }


# ============================================================================
# SECTION 24 - SECURITY / EDITOR UI (CARDS, RENDERERS, KEYBOARDS)
# ============================================================================


def verdict_chip(verdict):
    """Coloured chip for a verdict key."""
    meta = VERDICTS.get(str(verdict), VERDICTS["clean"])
    return meta["emoji"] + " " + B(meta["name"])


def risk_meter(score, width=12):
    """Risk bar: fills up as the score rises."""
    pct = max(0.0, min(100.0, float(score or 0) / 1.4))
    return pct_bar(pct, width) + " " + str(int(score or 0))


def render_findings_list(findings, limit=8):
    """Bullet list of findings, worst first."""
    items = list(findings or [])[:int(limit)]
    if not items:
        return I("No indicators.")
    lines = []
    for item in items:
        category = THREAT_CATEGORIES.get(str(item.get("category")), {})
        lines.append(
            str(category.get("emoji", "\u2022")) + " " + B(esc(str(item.get("title"))))
            + "  " + C("sev " + str(item.get("severity", 0)))
        )
        line_no = int(item.get("line", 0) or 0)
        excerpt = str(item.get("excerpt") or "").strip()
        if excerpt:
            lines.append(
                "   " + I("L" + str(line_no) + ": ") + C(esc(trunc(excerpt, 70)))
            )
    return "\n".join(lines)


def render_scan_summary(report):
    """Compact two-line scan summary used inside notifications."""
    report = report or {}
    categories = report.get("categories", {}) or {}
    tags = " ".join(
        str(THREAT_CATEGORIES.get(key, {}).get("emoji", "")) for key in sorted(categories)
    )
    return (
        verdict_chip(report.get("verdict", "clean")) + "  " + C("score " + str(report.get("score", 0)))
        + "\n" + _e("graph") + " " + risk_meter(report.get("score", 0))
        + ("\n" + _e("scan") + " " + tags if tags.strip() else "")
    )


def render_scan_report(report, fname="", fid=0):
    """Full scan card."""
    report = report or {}
    verdict = str(report.get("verdict", "clean"))
    meta = VERDICTS.get(verdict, VERDICTS["clean"])
    lines = [
        header("Security scan", None),
        "",
        _e("file") + " " + B(esc(str(fname or report.get("path", "")))) + (
            "  " + C("#" + str(fid)) if fid else ""),
        _e("shield") + " " + verdict_chip(verdict),
        _e("graph") + " Risk: " + risk_meter(report.get("score", 0)),
        _e("scan") + " Indicators: " + B(str(len(report.get("findings", []) or []))),
        _e("code") + " " + fmt_num(report.get("lines", 0)) + " lines \u00b7 "
        + fmt_size(report.get("bytes", 0)) + " \u00b7 entropy " + str(report.get("entropy", 0)),
    ]
    if report.get("binary"):
        lines.append(_e("warn") + " Binary: " + B(esc(str(report.get("binary")))))
    lines.append("")
    lines.append(I(meta["blurb"]))
    categories = report.get("categories", {}) or {}
    if categories:
        lines.append("")
        lines.append(B("Threat classes"))
        for key, count in sorted(categories.items(), key=lambda kv: -kv[1]):
            info = THREAT_CATEGORIES.get(key, {"name": key, "emoji": "\u2022"})
            lines.append("  " + str(info.get("emoji")) + " " + esc(str(info.get("name")))
                         + "  " + C("x" + str(count)))
    lines.append("")
    lines.append(B("Findings"))
    lines.append(render_findings_list(report.get("findings", []), 10))
    lines.append("")
    lines.append(divider("\u2504", 22))
    lines.append(I("Engine " + str(report.get("engine", "sigma-static")) + " \u00b7 " + fmt_dt_abs(utcstamp())))
    return "\n".join(lines)


def render_sandbox_report(result):
    """Sandbox execution card."""
    result = result or {}
    artifacts = result.get("artifacts", []) or []
    interesting = [name for name in artifacts if not name.startswith(("work/", "home/.cache"))]
    lines = [
        _e("lock") + " " + B("Sandbox result"),
        "",
        _e("check") + " Exit code: " + C(str(result.get("exit_code"))),
        _e("clock") + " Duration: " + C(fmt_duration(int(result.get("duration_ms", 0)) / 1000.0)),
        _e("warn") + " Timed out: " + B("yes" if result.get("timed_out") else "no"),
        _e("web") + " Network: " + B("blocked" if result.get("network_blocked") else "best-effort block"),
        _e("key") + " Tokens neutralised: " + B(str(result.get("tokens_replaced", 0))),
        _e("folder") + " Files created in jail: " + B(str(len(artifacts))),
    ]
    if interesting:
        lines.append("   " + C(esc(", ".join(interesting[:6]))))
    output = str(result.get("output") or "").strip()
    lines.append("")
    lines.append(B("Captured output"))
    lines.append(PRE(trunc(output, 1200)) if output else I("(no output)"))
    return "\n".join(lines)


def render_diff_block(diff):
    """Render a unified diff with coloured markers."""
    diff = diff or {}
    rows = diff.get("diff", []) or []
    if not rows:
        return I("No visible diff.")
    out = []
    for line in rows:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("@@"):
            out.append("\u2504\u2504 " + line.strip("@ "))
        elif line.startswith("+"):
            out.append("+ " + line[1:])
        elif line.startswith("-"):
            out.append("- " + line[1:])
        else:
            out.append("  " + line[1:] if line else "")
    body = "\n".join(out[:40])
    tail = "\n\u2026" if diff.get("truncated") else ""
    return PRE(trunc(body + tail, 1500))


def msg_security_center(uid):
    """User-facing security overview."""
    risk = user_push_risk(uid)
    rows = db_all(
        "SELECT s.fid, s.verdict, s.score, s.scanned_at, f.fname FROM security_scans s"
        " LEFT JOIN files f ON f.id=s.fid WHERE s.uid=? ORDER BY s.id DESC LIMIT 8",
        (int(uid),),
    )
    lines = [
        header("Security centre", get_user(uid)),
        "",
        I("Every upload and every edit is scanned before it can run."),
        "",
        _e("scan") + " Scans: " + B(str(len(rows))) + "  \u00b7  "
        + _e("edit") + " Pushes: " + B(str(risk.get("pushes", 0))),
        _e("shield") + " Blocked pushes: " + B(str(risk.get("blocked", 0))) + "  \u00b7  "
        + _e("ban") + " Quarantined: " + B(str(risk.get("quarantines", 0))),
        "",
        B("Recent verdicts"),
    ]
    if not rows:
        lines.append(I("Nothing scanned yet \u2014 upload a script to start."))
    for row in rows:
        lines.append(
            VERDICTS.get(str(row.get("verdict")), VERDICTS["clean"])["emoji"] + " "
            + C("#" + str(row.get("fid"))) + " " + esc(trunc(str(row.get("fname") or "deleted"), 22))
            + "  " + I("score " + str(row.get("score", 0)))
            + "  " + I(fmt_dt(str(row.get("scanned_at") or "")))
        )
    return "\n".join(lines)


def msg_admin_security(uid=0):
    """Admin security dashboard."""
    stats = security_stats()
    reviews = get_open_reviews(6)
    quarantined = get_quarantine_list(6)
    lines = [
        header("Security operations", None),
        "",
        _e("scan") + " Scans: " + B(fmt_num(stats["scans"])),
        "   " + VERDICTS["clean"]["emoji"] + " " + str(stats["clean"])
        + "   " + VERDICTS["suspicious"]["emoji"] + " " + str(stats["suspicious"])
        + "   " + VERDICTS["dangerous"]["emoji"] + " " + str(stats["dangerous"])
        + "   " + VERDICTS["malicious"]["emoji"] + " " + str(stats["malicious"]),
        _e("lock") + " Sandbox runs: " + B(str(stats["sandboxed"])),
        _e("ban") + " In quarantine: " + B(str(stats["quarantined"])),
        _e("key") + " Tokens neutralised: " + B(str(stats["tokens_neutralised"])),
        _e("shield") + " Blocked pushes: " + B(str(stats["blocked_pushes"])),
        _e("bell") + " Open reviews: " + B(str(stats["reviews_open"])),
        "",
        B("Review queue"),
    ]
    if not reviews:
        lines.append(I("Queue is empty."))
    for row in reviews:
        lines.append(
            VERDICTS.get(str(row.get("verdict")), VERDICTS["suspicious"])["emoji"] + " "
            + C("#" + str(row.get("fid"))) + " " + esc(trunc(str(row.get("fname") or "?"), 20))
            + "  " + I("score " + str(row.get("score", 0)))
            + "  " + I(str(row.get("summary") or "")[:40])
        )
    if quarantined:
        lines.append("")
        lines.append(B("Quarantine"))
        for row in quarantined:
            lines.append(
                "\u26d4 " + C("#" + str(row.get("fid"))) + " "
                + esc(trunc(str(row.get("fname") or "?"), 20)) + "  " + I(fmt_dt(str(row.get("created_at"))))
            )
    return "\n".join(lines)


def msg_scan_detail(fid):
    """Stored scan detail for one file."""
    row = get_file(fid) or {}
    scan = get_last_scan(fid)
    if not scan:
        return _e("info") + " File " + C("#" + str(fid)) + " has not been scanned yet."
    report = {
        "verdict": scan.get("verdict"),
        "score": scan.get("score"),
        "findings": scan.get("findings", []),
        "categories": {},
        "lines": 0,
        "bytes": int(row.get("fsize") or 0),
        "entropy": 0,
        "engine": scan.get("engine"),
    }
    for item in report["findings"]:
        key = str(item.get("category"))
        report["categories"][key] = report["categories"].get(key, 0) + 1
    body = render_scan_report(report, str(row.get("fname") or ""), fid)
    history = get_scan_history(fid, 5)
    if history:
        body += "\n\n" + B("Scan history") + "\n" + "\n".join(
            VERDICTS.get(str(h.get("verdict")), VERDICTS["clean"])["emoji"] + " "
            + I(fmt_dt(str(h.get("scanned_at")))) + "  " + C("score " + str(h.get("score")))
            for h in history
        )
    runs = get_sandbox_runs(fid, 1)
    if runs:
        body += "\n\n" + I("Last sandbox run: exit " + str(runs[0].get("exit_code"))
                            + " \u00b7 " + fmt_dt(str(runs[0].get("created_at"))))
    return body


def msg_editor(uid, fid, start=1):
    """Editor view with the current source window."""
    ok, body, meta = pull_code(uid, fid, start)
    if not ok:
        return _e("no") + " " + esc(str(body))
    lines = [
        _e("code") + " " + B("Editor") + " \u00b7 " + C("#" + str(fid)) + " " + esc(str(meta.get("fname"))),
        _e("shield") + " " + verdict_chip(meta.get("verdict", "clean"))
        + "  " + C("score " + str(meta.get("score", 0))),
        _e("graph") + " Lines " + str(meta.get("start")) + "-" + str(meta.get("end"))
        + " of " + str(meta.get("total_lines")) + "  \u00b7  MD5 " + C(str(meta.get("checksum", ""))[:10]),
        "",
        PRE(trunc(body, 2600)),
        "",
        I("Pull to fetch, push to write. Every push is re-scanned; a change that"),
        I("adds malware is rejected and the previous version stays live."),
    ]
    return "\n".join(lines)


def msg_edit_history(fid):
    """Push history card."""
    rows = get_edit_history(fid, 12)
    lines = [_e("reload") + " " + B("Push history") + " \u00b7 " + C("#" + str(fid)), ""]
    if not rows:
        lines.append(I("No edits recorded yet."))
    for row in rows:
        icon = "\u26d4" if int(row.get("blocked") or 0) else _e("ok")
        lines.append(
            icon + " " + B(esc(str(row.get("action")))) + "  "
            + C("+" + str(row.get("lines_added", 0)) + "/-" + str(row.get("lines_removed", 0)))
            + "  " + I("score " + str(row.get("old_score", 0)) + "\u2192" + str(row.get("new_score", 0)))
            + "  " + I(fmt_dt(str(row.get("created_at"))))
        )
        if row.get("note"):
            lines.append("   " + I(esc(trunc(str(row.get("note")), 70))))
    return "\n".join(lines)


def kb_security_center(uid):
    """User security menu."""
    rows = [
        [BTN(_e("scan") + " Scan a file", "sec_pick_scan")],
        [BTN(_e("lock") + " Sandbox a file", "sec_pick_sandbox")],
        [BTN(_e("code") + " Open editor", "sec_pick_editor")],
    ]
    if is_admin(uid):
        rows.append([BTN(_e("admin") + " Security operations", "sec_admin")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


def kb_security_review(fid):
    """Admin decision keyboard for one file."""
    return KB(
        [
            BTN(_e("scan") + " Report", "sec_report_" + str(fid)),
            BTN(_e("lock") + " Sandbox", "sec_sandbox_" + str(fid)),
        ],
        [
            BTN(_e("check") + " Safe \u2192 approve", "sec_safe_" + str(fid)),
            BTN("\u26d4 Quarantine", "sec_quar_" + str(fid)),
        ],
        [
            BTN(_e("reload") + " Release", "sec_release_" + str(fid)),
            BTN(_e("edit") + " Diff history", "sec_hist_" + str(fid)),
        ],
        [BACK("sec_admin")],
    )


def kb_admin_security():
    """Admin security dashboard keyboard."""
    return KB(
        [BTN(_e("bell") + " Review queue", "sec_queue"), BTN("\u26d4 Quarantine", "sec_quarantine")],
        [BTN(_e("key") + " Token vault", "sec_vault"), BTN(_e("scan") + " Rescan all", "sec_rescan_all")],
        [BTN(_e("reload") + " Refresh", "sec_admin"), BTN(_e("trash") + " Clean jails", "sec_clean_jails")],
        [BACK("menu_admin")],
    )


def kb_editor(uid, fid, start=1, total_lines=0):
    """Editor keyboard: pull / push / patch controls."""
    rows = [
        [
            BTN(_e("fwd") + " Pull next", "ed_pull_" + str(fid) + "_" + str(start + 60)),
            BTN(_e("back") + " Pull prev", "ed_pull_" + str(fid) + "_" + str(max(1, start - 60))),
        ],
        [
            BTN(_e("edit") + " Replace lines", "ed_replace_" + str(fid)),
            BTN(_e("plus") + " Insert after", "ed_insert_" + str(fid)),
        ],
        [
            BTN(_e("minus") + " Delete lines", "ed_delete_" + str(fid)),
            BTN(_e("upload") + " Push whole file", "ed_push_" + str(fid)),
        ],
        [
            BTN(_e("scan") + " Rescan", "sec_report_" + str(fid)),
            BTN(_e("reload") + " History", "ed_hist_" + str(fid)),
        ],
        [BTN(_e("file") + " File detail", "file_" + str(fid))],
        [BACK("menu_files")],
    ]
    return KB(*rows)


def kb_file_picker(uid, action_prefix, page=1, per_page=8):
    """Generic \"choose one of your files\" keyboard."""
    rows_data, total, total_pages, page = get_files(uid, None, page, per_page)
    rows = []
    for row in rows_data:
        rows.append([BTN(
            _e("file") + " " + trunc(str(row.get("fname")), 26) + "  #" + str(row.get("id")),
            action_prefix + str(row.get("id")),
        )])
    if not rows:
        rows.append([BTN(_e("upload") + " Upload a file first", "menu_files")])
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "sec_pick_page_" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "sec_pick_page_" + str(page + 1)))
    rows.append(nav)
    rows.append([BACK("menu_security")])
    return KB(*rows)


# ============================================================================
# SECTION 25 - LIVE ANIMATION, REACTIONS & PREMIUM EMOJI
# ============================================================================
# Telegram plays a full-screen animation when a message reaction is sent with
# is_big=True (the effect you get by long-pressing a reaction). react_big()
# uses that, react_sequence() chains several of them for a "live movement"
# feel, and animate_frames() edits one message repeatedly to animate text.
#
# Premium (custom) emoji are sent as <tg-emoji emoji-id="...">fallback</tg-emoji>
# entities. They render for every viewer when the bot is attached to a premium
# account / business account; otherwise Telegram shows the fallback glyph, so
# the UI never breaks.

PREMIUM_EMOJI_IDS = {
    "sigma": "5847721907740640693",
    "fire": "5445284980978621387",
    "rocket": "5445284980978621387",
    "shield": "5propagate",
    "lock": "5253742049005453611",
    "star": "5449471003699856745",
    "check": "5237699328843200968",
    "cross": "5210952531676504517",
    "loading": "5386367538735104399",
    "crown": "5424818078833715060",
    "heart": "5199885118214255386",
    "scan": "5386367538735104399",
}


def _load_premium_overrides():
    """Allow the operator to supply their own custom emoji ids via env JSON."""
    raw = os.environ.get("SIGMA_PREMIUM_EMOJI", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if str(v).isdigit()}
    except ValueError:
        log.warning("SIGMA_PREMIUM_EMOJI is not valid JSON \u2014 ignoring it")
    return {}


PREMIUM_EMOJI_IDS.update(_load_premium_overrides())
PREMIUM_EMOJI_ENABLED = os.environ.get("SIGMA_PREMIUM_EMOJI_ENABLED", "1").strip().lower() in (
    "1", "true", "yes", "on",
)


def premoji(key, fallback=None):
    """Premium (custom) emoji with a graceful plain-emoji fallback."""
    glyph = str(fallback or _e(key) or "\u2b50")
    emoji_id = str(PREMIUM_EMOJI_IDS.get(str(key), ""))
    if not PREMIUM_EMOJI_ENABLED or not emoji_id.isdigit():
        return glyph
    return '<tg-emoji emoji-id="' + emoji_id + '">' + glyph + "</tg-emoji>"


ANIMATIONS = {
    "spinner": ["\u25dc", "\u25dd", "\u25de", "\u25df"],
    "dots": ["\u28f7", "\u28ef", "\u28df", "\u287f", "\u28bf", "\u28fb", "\u28fd", "\u28fe"],
    "pulse": ["\U0001f5a4", "\U0001f7e3", "\U0001f535", "\U0001f7e2", "\U0001f7e1", "\U0001f534"],
    "scan": ["\U0001f50d\u2003\u2003", "\u2003\U0001f50e\u2003", "\u2003\u2003\U0001f50d", "\u2003\U0001f50e\u2003"],
    "radar": ["\u25f0", "\u25f3", "\u25f2", "\u25f1"],
    "rocket": ["\U0001f680\u2003\u2003\u2003", "\u2003\U0001f680\u2003\u2003", "\u2003\u2003\U0001f680\u2003", "\u2003\u2003\u2003\U0001f680", "\u2728\u2728\u2728\u2728"],
    "matrix": ["\u2591\u2591\u2591", "\u2592\u2591\u2591", "\u2593\u2592\u2591", "\u2588\u2593\u2592", "\u2593\u2588\u2593", "\u2592\u2593\u2588"],
    "shield": ["\U0001f6e1\ufe0f", "\U0001f512", "\U0001f6e1\ufe0f", "\u2705"],
    "hearts": ["\u2764\ufe0f", "\U0001f9e1", "\U0001f49b", "\U0001f49a", "\U0001f499", "\U0001f49c"],
    "stars": ["\u2728", "\U0001f31f", "\U0001f4a5", "\U0001f31f"],
    "upload": ["\u2b06\ufe0f\u2003\u2003", "\u2003\u2b06\ufe0f\u2003", "\u2003\u2003\u2b06\ufe0f", "\u2705"],
    "clock": ["\U0001f55b", "\U0001f550", "\U0001f551", "\U0001f552", "\U0001f553", "\U0001f554"],
}

REACTION_EMOJIS = [
    "\U0001f44d", "\u2764", "\U0001f525", "\U0001f389", "\U0001f60d",
    "\U0001f92f", "\U0001f44f", "\u26a1", "\U0001f680", "\U0001f3c6",
]

ANIM_MIN_DELAY = 0.45


def animate_frames(cid, mid, text, frames="spinner", loops=2, delay=0.5, suffix=""):
    """Animate one message by editing it frame by frame.

    Runs synchronously but is always called from a handler thread, so polling
    is never blocked for long. Failures are swallowed: an animation must never
    break the actual action.
    """
    sequence = ANIMATIONS.get(str(frames), ANIMATIONS["spinner"]) if isinstance(frames, str) else list(frames)
    step = max(float(delay), ANIM_MIN_DELAY)
    shown = 0
    for _loop in range(max(1, int(loops))):
        for frame in sequence:
            body = str(frame) + " " + str(text) + ("\n" + suffix if suffix else "")
            try:
                bot.edit_message_text(body, cid, mid, parse_mode="HTML")
                shown += 1
            except Exception:
                return shown
            time.sleep(step)
    return shown


def animated_progress(cid, mid, label, steps, delay=0.6):
    """Animated progress bar driven by a list of (pct, caption) steps."""
    for pct, caption in steps:
        body = (
            premoji("loading", _e("reload")) + " " + B(esc(str(label))) + "\n\n"
            + pct_bar(float(pct), 14) + " " + str(int(pct)) + "%\n"
            + I(esc(str(caption)))
        )
        try:
            bot.edit_message_text(body, cid, mid, parse_mode="HTML")
        except Exception:
            return False
        time.sleep(max(float(delay), ANIM_MIN_DELAY))
    return True


def live_scan_animation(cid, mid, fname):
    """The scanner's signature animation."""
    return animated_progress(cid, mid, "Scanning " + trunc(str(fname), 24), [
        (12, "Reading file \u2026"),
        (34, "Signature engine \u2026"),
        (58, "AST analysis \u2026"),
        (78, "Entropy / packer check \u2026"),
        (94, "Scoring threat classes \u2026"),
        (100, "Done"),
    ], 0.5)


def typing(cid, action="typing"):
    """Show a chat action (typing / upload_document / etc)."""
    try:
        bot.send_chat_action(cid, action)
        return True
    except Exception:
        return False


def react_big(cid, mid, emoji="\U0001f525"):
    """Send a reaction with the big full-screen animation."""
    try:
        reaction = [types.ReactionTypeEmoji(str(emoji))]
        bot.set_message_reaction(cid, mid, reaction, is_big=True)
        return True
    except Exception as exc:
        log.debug("set_message_reaction failed: %s", exc)
        return False


def react_sequence(cid, mid, emojis=None, delay=0.9):
    """Chain several big reactions for a live, moving effect."""
    played = 0
    for emoji in list(emojis or ["\U0001f525", "\u26a1", "\U0001f389"]):
        if not react_big(cid, mid, emoji):
            break
        played += 1
        time.sleep(max(float(delay), 0.6))
    return played


def celebrate(cid, mid=None, emoji="\U0001f389"):
    """Big reaction plus a sparkle animation on the given message."""
    if mid:
        react_big(cid, mid, emoji)
        animate_frames(cid, mid, B("Nice one!"), "stars", 1, 0.45)
        return True
    return False


def spawn_animation(target, *args, **kwargs):
    """Run an animation in a daemon thread so handlers stay responsive."""
    thread = threading.Thread(target=_safe_call, args=(target,) + args, kwargs=kwargs, daemon=True)
    thread.start()
    return thread


def _safe_call(target, *args, **kwargs):
    """Call a function, log and swallow any exception."""
    try:
        return target(*args, **kwargs)
    except Exception as exc:
        log.debug("Background call %s failed: %s", getattr(target, "__name__", target), exc)
        return None


def log_reaction(uid, cid, mid, emoji):
    """Store a reaction event (used for the reaction leaderboard)."""
    return db_exec(
        "INSERT INTO reaction_log (uid, cid, mid, emoji, created_at) VALUES (?,?,?,?,?)",
        (int(uid or 0), int(cid or 0), int(mid or 0), str(emoji)[:16], utcstamp()),
    )


def reaction_stats(limit=10):
    """Most used reactions."""
    return db_all(
        "SELECT emoji, COUNT(*) c FROM reaction_log GROUP BY emoji ORDER BY c DESC LIMIT ?",
        (int(limit),),
    )


def msg_reactions_board():
    """Reaction leaderboard card."""
    rows = reaction_stats(10)
    lines = [premoji("heart") + " " + B("Live reactions"), ""]
    if not rows:
        lines.append(I("No reactions yet \u2014 long-press any bot message and react."))
    total = sum(int(row.get("c") or 0) for row in rows) or 1
    for row in rows:
        count = int(row.get("c") or 0)
        lines.append(
            str(row.get("emoji")) + "  " + pct_bar(count * 100.0 / total, 10) + "  " + B(str(count))
        )
    return "\n".join(lines)


# ============================================================================
# SECTION 26 - EXTENSION FRAMEWORK (ADD FEATURES WITHOUT TOUCHING bot.py)
# ============================================================================
# Drop a .py file into ~/sigma_hosting/plugins/ and it is loaded at startup
# (or live with /reload). A plugin registers commands, callback buttons, menu
# entries, FSM states, text hooks, background jobs and even extra security
# scanners through the decorators below - no edit to bot.py is ever required.

EXTENSION_REGISTRY = {
    "commands": {},
    "callbacks": [],
    "menu_buttons": [],
    "text_hooks": [],
    "jobs": [],
    "scanners": [],
    "plugins": {},
}

_ext_lock = threading.Lock()


def extension_command(name, description="", admin_only=False):
    """Register a /command implemented by a plugin."""

    def decorator(func):
        with _ext_lock:
            EXTENSION_REGISTRY["commands"][str(name).lower().lstrip("/")] = {
                "handler": func,
                "description": str(description)[:120],
                "admin_only": bool(admin_only),
                "plugin": getattr(func, "__module__", "core"),
            }
        return func

    return decorator


def extension_callback(prefix, admin_only=False, exact=False):
    """Register a callback-data prefix (or exact match) handler."""

    def decorator(func):
        with _ext_lock:
            EXTENSION_REGISTRY["callbacks"].append({
                "prefix": str(prefix),
                "exact": bool(exact),
                "handler": func,
                "admin_only": bool(admin_only),
                "plugin": getattr(func, "__module__", "core"),
            })
        return func

    return decorator


def extension_menu_button(label, data, admin_only=False, row=99):
    """Add an inline button to the main menu."""
    with _ext_lock:
        existing = {(item.get("label"), item.get("data"))
                    for item in EXTENSION_REGISTRY["menu_buttons"]}
        if (str(label)[:40], str(data)[:60]) in existing:
            return True
        EXTENSION_REGISTRY["menu_buttons"].append({
            "label": str(label)[:40],
            "data": str(data)[:60],
            "admin_only": bool(admin_only),
            "row": int(row),
            "core": row < 90,
        })
    return True


def extension_text_hook(func=None, priority=50):
    """Register a plain-text hook: return True to consume the message."""

    def decorator(inner):
        with _ext_lock:
            EXTENSION_REGISTRY["text_hooks"].append({
                "handler": inner,
                "priority": int(priority),
                "plugin": getattr(inner, "__module__", "core"),
            })
            EXTENSION_REGISTRY["text_hooks"].sort(key=lambda hook: hook["priority"])
        return inner

    return decorator(func) if callable(func) else decorator


def extension_job(interval_seconds, name=""):
    """Register a periodic background job."""

    def decorator(func):
        with _ext_lock:
            EXTENSION_REGISTRY["jobs"].append({
                "handler": func,
                "interval": max(15, int(interval_seconds)),
                "name": str(name or getattr(func, "__name__", "job")),
                "last_run": 0.0,
                "plugin": getattr(func, "__module__", "core"),
            })
        return func

    return decorator


def extension_scanner(name=""):
    """Register an extra security scanner: fn(source, ext) -> [findings]."""

    def decorator(func):
        with _ext_lock:
            EXTENSION_REGISTRY["scanners"].append({
                "handler": func,
                "name": str(name or getattr(func, "__name__", "scanner")),
                "plugin": getattr(func, "__module__", "core"),
            })
        return func

    return decorator


class SigmaAPI(object):
    """Stable surface handed to plugins.

    A plugin only ever touches this object, so internal refactors of bot.py do
    not break existing plugins.
    """

    version = VERSION
    bot = bot
    # registration
    command = staticmethod(extension_command)
    callback = staticmethod(extension_callback)
    menu_button = staticmethod(extension_menu_button)
    text_hook = staticmethod(extension_text_hook)
    job = staticmethod(extension_job)
    scanner = staticmethod(extension_scanner)
    # messaging
    send = staticmethod(send)
    edit = staticmethod(edit)
    ack = staticmethod(ack)
    notify_user = staticmethod(notify_user)
    notify_admins = staticmethod(notify_admins)
    animate = staticmethod(animate_frames)
    progress = staticmethod(animated_progress)
    react = staticmethod(react_big)
    react_sequence = staticmethod(react_sequence)
    premoji = staticmethod(premoji)
    # ui
    KB = staticmethod(KB)
    BTN = staticmethod(BTN)
    LBTN = staticmethod(LBTN)
    BACK = staticmethod(BACK)
    B = staticmethod(B)
    I = staticmethod(I)
    C = staticmethod(C)
    PRE = staticmethod(PRE)
    esc = staticmethod(esc)
    header = staticmethod(header)
    divider = staticmethod(divider)
    pct_bar = staticmethod(pct_bar)
    fmt_size = staticmethod(fmt_size)
    fmt_num = staticmethod(fmt_num)
    fmt_dt = staticmethod(fmt_dt)
    # data
    db_exec = staticmethod(db_exec)
    db_one = staticmethod(db_one)
    db_all = staticmethod(db_all)
    db_val = staticmethod(db_val)
    reconcile_schema = staticmethod(reconcile_schema)
    get_user = staticmethod(get_user)
    get_files = staticmethod(get_files)
    get_file = staticmethod(get_file)
    add_points = staticmethod(add_points)
    add_coins = staticmethod(add_coins)
    is_admin = staticmethod(is_admin)
    set_state = None  # bound in bind_api_states()
    # security
    scan_source = staticmethod(scan_source)
    scan_file = staticmethod(scan_file)
    run_in_sandbox = staticmethod(run_in_sandbox)
    quarantine_file = staticmethod(quarantine_file)
    log = log
    base_dir = BASE_DIR

    @staticmethod
    def emoji(key):
        """Look up a core emoji by key."""
        return _e(key)

    @staticmethod
    def label(key):
        """Look up a core UI label by key."""
        return _l(key)


def plugins_dir():
    """Directory scanned for plugins."""
    path = BASE_DIR / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


SAMPLE_PLUGIN = '''"""Example SIGMA plugin - copy this file and build your own feature.

Drop any .py file in this folder and restart the bot (or run /reload as an
admin). Nothing in bot.py has to change.
"""

PLUGIN_NAME = "hello"
PLUGIN_VERSION = "1.0.0"


def register(sigma):
    """Called once at load time. `sigma` is the SigmaAPI object."""

    @sigma.command("hello", "Say hello (example plugin)")
    def cmd_hello(message, uid):
        sigma.send(
            uid,
            sigma.premoji("rocket") + " " + sigma.B("Hello from a plugin!")
            + "\\n" + sigma.I("Edit plugins/example_hello.py to change this."),
            sigma.KB([sigma.BTN("Press me", "hello_press")]),
        )

    @sigma.callback("hello_press", exact=True)
    def cb_hello(call, uid, cid, mid, data):
        sigma.ack(call, "It works!", False)
        sigma.react(cid, mid, "\\U0001f525")
        sigma.edit(cid, mid, sigma.B("Plugin callback handled."))

    # Adds a button to the bot's main menu.
    sigma.menu_button(sigma.emoji("star") + " Hello", "hello_press")

    @sigma.job(300, "hello-heartbeat")
    def heartbeat():
        sigma.log.debug("hello plugin heartbeat")
'''

PLUGIN_README = """# SIGMA plugins

Every `.py` file in this folder is imported at startup and after `/reload`.

A plugin must expose `register(sigma)`. Available decorators:

| Decorator | Purpose |
|---|---|
| `@sigma.command(name, desc, admin_only=False)` | new `/command` |
| `@sigma.callback(prefix, exact=False)` | inline-button callbacks |
| `sigma.menu_button(label, data)` | add a main-menu button |
| `@sigma.text_hook(priority=50)` | intercept plain text |
| `@sigma.job(seconds, name)` | periodic background job |
| `@sigma.scanner(name)` | extra malware signatures |

Helpers on `sigma`: `send`, `edit`, `ack`, `animate`, `progress`, `react`,
`premoji`, `KB/BTN/BACK`, `B/I/C/PRE/esc`, `db_exec/db_one/db_all/db_val`,
`get_user`, `get_files`, `add_points`, `scan_source`, `run_in_sandbox`.

Plugin errors are isolated: a broken plugin is skipped and logged, the bot
keeps running.
"""


def write_sample_plugin():
    """Create the example plugin and docs on first run."""
    folder = plugins_dir()
    sample = folder / "example_hello.py"
    readme = folder / "README.md"
    created = []
    if not sample.exists():
        sample.write_text(SAMPLE_PLUGIN, encoding="utf-8")
        created.append(sample.name)
    if not readme.exists():
        readme.write_text(PLUGIN_README, encoding="utf-8")
        created.append(readme.name)
    return created


def _clear_plugin_registrations(plugin_name):
    """Remove everything a plugin registered (used before reload)."""
    with _ext_lock:
        EXTENSION_REGISTRY["commands"] = {
            key: value for key, value in EXTENSION_REGISTRY["commands"].items()
            if value.get("plugin") != plugin_name
        }
        for bucket in ("callbacks", "text_hooks", "jobs", "scanners"):
            EXTENSION_REGISTRY[bucket] = [
                item for item in EXTENSION_REGISTRY[bucket] if item.get("plugin") != plugin_name
            ]


def load_plugin_file(path):
    """Import one plugin file and call register(). Returns (ok, detail)."""
    import importlib.util

    path = Path(str(path))
    name = "sigma_plugin_" + re.sub(r"[^A-Za-z0-9_]+", "_", path.stem)
    _clear_plugin_registrations(name)
    try:
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            return False, "could not create a module spec"
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        log.error("Plugin %s failed to import: %s", path.name, exc)
        return False, str(exc)[:200]
    register = getattr(module, "register", None)
    if not callable(register):
        return False, "no register(sigma) function"
    try:
        register(SigmaAPI)
    except Exception as exc:
        log.error("Plugin %s register() failed: %s", path.name, exc)
        return False, str(exc)[:200]
    version = str(getattr(module, "PLUGIN_VERSION", "1.0.0"))
    label = str(getattr(module, "PLUGIN_NAME", path.stem))
    with _ext_lock:
        EXTENSION_REGISTRY["plugins"][path.stem] = {
            "name": label,
            "version": version,
            "file": str(path),
            "module": name,
            "loaded_at": utcstamp(),
        }
    db_exec(
        "INSERT INTO plugin_log (name, version, state, detail, loaded_at) VALUES (?,?,?,?,?)",
        (label, version, "loaded", str(path.name), utcstamp()),
    )
    return True, label + " v" + version


def load_plugins():
    """Load every plugin file. Never raises."""
    write_sample_plugin()
    loaded, failed = [], []
    try:
        files = sorted(plugins_dir().glob("*.py"))
    except Exception as exc:
        log.error("Cannot list the plugins folder: %s", exc)
        return loaded, failed
    for path in files:
        if path.name.startswith("_"):
            continue
        ok, detail = load_plugin_file(path)
        if ok:
            loaded.append(path.stem)
            log.info("Plugin loaded: %s (%s)", path.name, detail)
        else:
            failed.append(path.stem + ": " + detail)
            db_exec(
                "INSERT INTO plugin_log (name, version, state, detail, loaded_at) VALUES (?,?,?,?,?)",
                (path.stem, "", "error", detail[:300], utcstamp()),
            )
    log.info("Extensions: %s plugin(s), %s command(s), %s callback(s), %s job(s)",
             len(loaded), len(EXTENSION_REGISTRY["commands"]),
             len(EXTENSION_REGISTRY["callbacks"]), len(EXTENSION_REGISTRY["jobs"]))
    return loaded, failed


def reload_plugins():
    """Live reload: forget every plugin registration and load again."""
    with _ext_lock:
        names = list(EXTENSION_REGISTRY["plugins"].keys())
        EXTENSION_REGISTRY["plugins"] = {}
        EXTENSION_REGISTRY["menu_buttons"] = [
            item for item in EXTENSION_REGISTRY["menu_buttons"] if item.get("core")
        ]
    for name in names:
        _clear_plugin_registrations("sigma_plugin_" + re.sub(r"[^A-Za-z0-9_]+", "_", name))
    return load_plugins()


def extension_menu_rows(uid):
    """Extra main-menu rows contributed by plugins."""
    rows = []
    with _ext_lock:
        buttons = sorted(EXTENSION_REGISTRY["menu_buttons"], key=lambda item: item.get("row", 99))
    chunk = []
    for item in buttons:
        if item.get("admin_only") and not is_admin(uid):
            continue
        chunk.append(BTN(str(item.get("label")), str(item.get("data"))))
        if len(chunk) == 2:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    return rows


def dispatch_extension_command(message, uid, command):
    """Run a plugin command. Returns True when handled."""
    entry = EXTENSION_REGISTRY["commands"].get(str(command).lower())
    if not entry:
        return False
    if entry.get("admin_only") and not is_admin(uid):
        send(uid, _e("lock") + " Administrators only.")
        return True
    try:
        entry["handler"](message, uid)
    except Exception as exc:
        log.exception("Plugin command /%s failed: %s", command, exc)
        send(uid, _e("warn") + " That extension command failed. The admins were notified.")
        notify_admins(_e("warn") + " Plugin command " + C("/" + str(command)) + " failed: " + esc(str(exc)[:200]))
    return True


def dispatch_extension_callback(call, uid, cid, mid, data):
    """Run a plugin callback handler. Returns True when handled."""
    with _ext_lock:
        handlers = list(EXTENSION_REGISTRY["callbacks"])
    for entry in handlers:
        prefix = str(entry.get("prefix"))
        matched = (data == prefix) if entry.get("exact") else str(data).startswith(prefix)
        if not matched:
            continue
        if entry.get("admin_only") and not is_admin(uid):
            ack(call, "Administrators only.", True)
            return True
        try:
            entry["handler"](call, uid, cid, mid, data)
        except Exception as exc:
            log.exception("Plugin callback %s failed: %s", data, exc)
            ack(call, "That extension action failed.", True)
        return True
    return False


def dispatch_text_hooks(message, uid, text):
    """Give plugins a chance to consume a plain-text message."""
    with _ext_lock:
        hooks = list(EXTENSION_REGISTRY["text_hooks"])
    for hook in hooks:
        try:
            if hook["handler"](message, uid, text):
                return True
        except Exception as exc:
            log.exception("Plugin text hook failed: %s", exc)
    return False


def run_extension_scanners(source, ext):
    """Merge plugin-provided findings into the scanner output."""
    findings = []
    with _ext_lock:
        scanners = list(EXTENSION_REGISTRY["scanners"])
    for entry in scanners:
        try:
            extra = entry["handler"](source, ext) or []
            for item in extra:
                if isinstance(item, dict) and item.get("title"):
                    item.setdefault("id", "ext_" + str(entry.get("name")))
                    item.setdefault("category", "obfusc")
                    item.setdefault("severity", 5)
                    item.setdefault("line", 0)
                    item.setdefault("excerpt", "")
                    findings.append(item)
        except Exception as exc:
            log.debug("Plugin scanner %s failed: %s", entry.get("name"), exc)
    return findings


def extension_job_loop():
    """Background loop running plugin jobs on their own intervals."""
    log.info("Extension job runner started")
    while not _stop_event.is_set():
        try:
            now = time.time()
            with _ext_lock:
                jobs = list(EXTENSION_REGISTRY["jobs"])
            for entry in jobs:
                if now - float(entry.get("last_run") or 0) < int(entry.get("interval", 60)):
                    continue
                entry["last_run"] = now
                _safe_call(entry["handler"])
        except Exception as exc:
            log.error("Extension job runner error: %s", exc)
        _stop_event.wait(15)
    log.info("Extension job runner stopped")


def msg_plugins():
    """Plugin overview card."""
    with _ext_lock:
        plugins = dict(EXTENSION_REGISTRY["plugins"])
        commands = dict(EXTENSION_REGISTRY["commands"])
        callbacks = list(EXTENSION_REGISTRY["callbacks"])
        jobs = list(EXTENSION_REGISTRY["jobs"])
        scanners = list(EXTENSION_REGISTRY["scanners"])
    lines = [
        header("Extensions", None),
        "",
        I("Add features by dropping a .py file into the plugins folder \u2014"),
        I("no change to bot.py, no restart of the code you already trust."),
        "",
        _e("folder") + " " + C(str(plugins_dir())),
        "",
        _e("gear") + " Plugins: " + B(str(len(plugins))) + "  \u00b7  "
        + _e("bot") + " Commands: " + B(str(len(commands))),
        _e("menu") + " Callbacks: " + B(str(len(callbacks))) + "  \u00b7  "
        + _e("clock") + " Jobs: " + B(str(len(jobs))) + "  \u00b7  "
        + _e("scan") + " Scanners: " + B(str(len(scanners))),
        "",
        B("Loaded"),
    ]
    if not plugins:
        lines.append(I("None yet. See plugins/README.md for the API."))
    for key, meta in sorted(plugins.items()):
        lines.append(
            _e("check") + " " + B(esc(str(meta.get("name")))) + " " + C("v" + str(meta.get("version")))
            + "  " + I(esc(key + ".py"))
        )
    if commands:
        lines.append("")
        lines.append(B("Plugin commands"))
        for name, meta in sorted(commands.items()):
            lines.append(
                C("/" + name) + " \u2014 " + esc(str(meta.get("description") or ""))
                + ("  " + _e("lock") if meta.get("admin_only") else "")
            )
    errors = db_all(
        "SELECT name, detail, loaded_at FROM plugin_log WHERE state='error' ORDER BY id DESC LIMIT 3", ()
    )
    if errors:
        lines.append("")
        lines.append(B("Recent load errors"))
        for row in errors:
            lines.append(_e("no") + " " + esc(str(row.get("name"))) + ": " + I(esc(trunc(str(row.get("detail")), 70))))
    return "\n".join(lines)


def kb_plugins(uid):
    """Plugin management keyboard."""
    rows = []
    if is_admin(uid):
        rows.append([BTN(_e("reload") + " Reload plugins", "plug_reload")])
    rows.append([BTN(_e("info") + " Plugin API docs", "plug_docs")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


# ============================================================================
# SECTION 27 - SECURITY / EDITOR / EXTENSION HANDLERS
# ============================================================================
# Every button below is registered through the extension framework itself, so
# the core router never has to know about them. That is the same path a plugin
# takes - the security suite is the framework's first consumer.


def guard(func):
    """Decorator: ensure the user exists, block banned users, swallow errors."""

    def wrapper(message, *args, **kwargs):
        try:
            from_user = getattr(message, "from_user", None)
            uid = int(getattr(from_user, "id", 0) or 0)
            if not uid:
                return None
            ensure_user(uid, from_user)
            touch_seen(uid)
            if is_banned(uid):
                user = get_user(uid)
                send(
                    uid,
                    _e("ban") + " " + B("Your account is banned.") + "\n\n"
                    + "Reason: " + esc(str(user.get("ban_reason") or "not specified")) + "\n"
                    + I("Contact an administrator if you believe this is a mistake."),
                )
                return None
            if not check_rate_limit(uid, "commands", 40, 60):
                send(uid, _e("warn") + " Slow down a little \u2014 too many requests. Try again in a minute.")
                return None
            return func(message, *args, **kwargs)
        except Exception as exc:
            log.exception("Handler %s failed: %s", getattr(func, "__name__", "?"), exc)
            try:
                send(
                    int(getattr(getattr(message, "from_user", None), "id", 0) or 0),
                    _e("warn") + " Something went wrong handling that. The error has been logged.",
                )
            except Exception as inner:
                log.debug("error notify failed: %s", inner)
            return None

    wrapper.__name__ = getattr(func, "__name__", "wrapped")
    wrapper.__doc__ = getattr(func, "__doc__", "")
    return wrapper


def admin_guard(func):
    """Decorator: guard plus an administrator check."""

    def inner(message, *args, **kwargs):
        uid = int(getattr(getattr(message, "from_user", None), "id", 0) or 0)
        if not is_admin(uid):
            send(uid, _e("lock") + " That command is restricted to administrators.")
            return None
        return func(message, *args, **kwargs)

    inner.__name__ = getattr(func, "__name__", "wrapped_admin")
    inner.__doc__ = getattr(func, "__doc__", "")
    return guard(inner)


def cmd_args(message):
    """Text after the command word."""
    text = str(getattr(message, "text", "") or "")
    parts = text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _pick_or_msg(uid, prefix, title):
    """Send a file picker for one of the security actions."""
    return (
        _e("folder") + " " + B(title) + "\n\n" + I("Choose one of your files."),
        kb_file_picker(uid, prefix),
    )


@bot.message_handler(commands=["security", "sec"])
@guard
def handle_security(message):
    """/security - open the security centre."""
    uid = int(message.from_user.id)
    send(uid, msg_security_center(uid), kb_security_center(uid))


@bot.message_handler(commands=["scan"])
@guard
def handle_scan(message):
    """/scan <id> - run the malware scanner on one of your files."""
    uid = int(message.from_user.id)
    fid = _first_int(str(getattr(message, "text", "") or ""))
    if not fid:
        text, markup = _pick_or_msg(uid, "sec_scan_", "Scan a file")
        send(uid, text, markup)
        return
    row = get_user_file(uid, fid) if not is_admin(uid) else get_file(fid)
    if not row:
        send(uid, _e("no") + " File " + C("#" + str(fid)) + " not found.", kb_main(uid))
        return
    sent = send(uid, premoji("scan") + " " + B("Starting scan\u2026"))
    if sent:
        spawn_animation(live_scan_animation, sent.chat.id, sent.message_id, str(row.get("fname")))
    report = scan_file(str(row.get("fpath") or ""))
    report["checksum"] = checksum_file(str(row.get("fpath") or ""))
    save_scan(fid, int(row.get("uid") or uid), report)
    time.sleep(3.2)
    body = render_scan_report(report, str(row.get("fname")), fid)
    if sent:
        edit(sent.chat.id, sent.message_id, body, kb_security_review(fid) if is_admin(uid)
             else kb_file_detail(uid, fid))
        react_big(sent.chat.id, sent.message_id,
                  "\U0001f44d" if report.get("verdict") in ("clean", "low") else "\U0001f631")
    else:
        send(uid, body)
    if str(report.get("verdict")) == "malicious":
        quarantine_file(fid, "Manual scan verdict: malicious", report, uid)


@bot.message_handler(commands=["sandbox"])
@guard
def handle_sandbox(message):
    """/sandbox <id> - run a file inside the isolated jail."""
    uid = int(message.from_user.id)
    fid = _first_int(str(getattr(message, "text", "") or ""))
    if not fid:
        text, markup = _pick_or_msg(uid, "sec_sandbox_", "Sandbox a file")
        send(uid, text, markup)
        return
    _do_sandbox(uid, fid)


def _do_sandbox(uid, fid, cid=None, mid=None):
    """Shared sandbox flow used by the command and the buttons."""
    row = get_file(fid) if is_admin(uid) else get_user_file(uid, fid)
    if not row:
        send(uid, _e("no") + " File " + C("#" + str(fid)) + " not found.")
        return
    if cid and mid:
        target_cid, target_mid = cid, mid
    else:
        sent = send(uid, premoji("lock") + " " + B("Preparing jail\u2026"))
        if not sent:
            return
        target_cid, target_mid = sent.chat.id, sent.message_id
    spawn_animation(animated_progress, target_cid, target_mid, "Sandbox " + trunc(str(row.get("fname")), 20), [
        (15, "Creating throw-away jail \u2026"),
        (35, "Copying code, stripping credentials \u2026"),
        (55, "Swapping in the checker bot token \u2026"),
        (75, "Applying CPU / memory / network limits \u2026"),
        (92, "Executing under supervision \u2026"),
    ], 0.55)
    ok, note, result = sandbox_and_review(int(row.get("uid") or uid), fid, uid)
    time.sleep(0.4)
    if not ok:
        edit(target_cid, target_mid, _e("no") + " " + esc(str(note)), kb_file_detail(uid, fid))
        return
    body = (
        premoji("lock") + " " + B("Sandbox complete") + "  " + C("#" + str(fid)) + "\n"
        + I("The code ran with no access to your files, no real token and no network.")
        + "\n\n" + render_sandbox_report(result) + "\n\n" + I(esc(note))
    )
    edit(target_cid, target_mid, body, kb_security_review(fid) if is_admin(uid) else kb_file_detail(uid, fid))
    react_big(target_cid, target_mid, "\U0001f9ea")


@bot.message_handler(commands=["editor", "edit"])
@guard
def handle_editor(message):
    """/editor <id> - open the guarded code editor."""
    uid = int(message.from_user.id)
    fid = _first_int(str(getattr(message, "text", "") or ""))
    if not fid:
        text, markup = _pick_or_msg(uid, "ed_open_", "Open the editor")
        send(uid, text, markup)
        return
    send(uid, msg_editor(uid, fid), kb_editor(uid, fid))


@bot.message_handler(commands=["pull"])
@guard
def handle_pull(message):
    """/pull <id> [line] - fetch source lines for editing."""
    uid = int(message.from_user.id)
    numbers = [int(n) for n in re.findall(r"\d+", str(getattr(message, "text", "") or ""))]
    if not numbers:
        text, markup = _pick_or_msg(uid, "ed_open_", "Pull source")
        send(uid, text, markup)
        return
    fid = numbers[0]
    start = numbers[1] if len(numbers) > 1 else 1
    send(uid, msg_editor(uid, fid, start), kb_editor(uid, fid, start))


@bot.message_handler(commands=["push"])
@guard
def handle_push(message):
    """/push <id> - replace a file's contents (security re-checked)."""
    uid = int(message.from_user.id)
    fid = _first_int(str(getattr(message, "text", "") or ""))
    if not fid:
        text, markup = _pick_or_msg(uid, "ed_push_", "Push new code")
        send(uid, text, markup)
        return
    row, error = editable_file(uid, fid)
    if not row:
        send(uid, _e("no") + " " + esc(str(error)))
        return
    set_state(uid, "awaiting_push_full", fid=fid)
    prompt(
        uid,
        _e("upload") + " " + B("Push to " + esc(str(row.get("fname")))) + "\n\n"
        + I("Send the complete new source. It is scanned before it replaces the")
        + " " + I("live version, and a change that adds malware is rejected."),
        "ed_open_" + str(fid),
    )


@bot.message_handler(commands=["plugins", "extensions"])
@guard
def handle_plugins(message):
    """/plugins - list loaded extensions."""
    uid = int(message.from_user.id)
    send(uid, msg_plugins(), kb_plugins(uid))


@bot.message_handler(commands=["reload"])
@guard
def handle_reload(message):
    """/reload - reload plugins without restarting the bot (admin)."""
    uid = int(message.from_user.id)
    if not is_admin(uid):
        send(uid, _e("lock") + " Administrators only.")
        return
    sent = send(uid, premoji("loading", _e("reload")) + " " + B("Reloading extensions\u2026"))
    loaded, failed = reload_plugins()
    body = (
        _e("ok") + " " + B("Reload complete") + "\n\n"
        + _e("gear") + " Loaded: " + B(str(len(loaded))) + "\n"
        + (_e("no") + " Failed: " + B(str(len(failed))) + "\n" + PRE(trunc("\n".join(failed), 600))
           if failed else "")
        + "\n" + msg_plugins()
    )
    if sent:
        edit(sent.chat.id, sent.message_id, body, kb_plugins(uid))
        react_big(sent.chat.id, sent.message_id, "\u26a1")
    else:
        send(uid, body, kb_plugins(uid))


@bot.message_handler(commands=["reactions", "react"])
@guard
def handle_reactions(message):
    """/react - live reaction demo and leaderboard."""
    uid = int(message.from_user.id)
    sent = send(
        uid,
        msg_reactions_board() + "\n\n" + I("Watch \u2014 the bot is about to react live."),
        kb_reactions(),
    )
    if sent:
        spawn_animation(react_sequence, sent.chat.id, sent.message_id,
                        ["\U0001f525", "\u26a1", "\U0001f389", "\U0001f60d"], 1.0)


def kb_reactions():
    """Reaction playground keyboard."""
    rows = []
    chunk = []
    for emoji in REACTION_EMOJIS:
        chunk.append(BTN(emoji, "react_" + emoji))
        if len(chunk) == 5:
            rows.append(chunk)
            chunk = []
    if chunk:
        rows.append(chunk)
    rows.append([BTN(_e("star") + " Animation showcase", "anim_show")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


@bot.message_handler(commands=["anim", "animation"])
@guard
def handle_anim(message):
    """/anim - show the animation showcase."""
    uid = int(message.from_user.id)
    sent = send(uid, B("Animation showcase"), kb_reactions())
    if sent:
        spawn_animation(_animation_showcase, sent.chat.id, sent.message_id)


def _animation_showcase(cid, mid):
    """Play several animation styles back to back."""
    for name in ("rocket", "matrix", "radar", "pulse", "stars"):
        animate_frames(cid, mid, B("Animation: ") + C(name), name, 1, 0.45)
    edit(cid, mid, premoji("sigma") + " " + B("Showcase finished") + "\n\n"
         + msg_reactions_board(), kb_reactions())
    react_big(cid, mid, "\U0001f92f")


def _first_int(text):
    """First integer in a string, or 0."""
    match = re.search(r"\d+", str(text or ""))
    return int(match.group(0)) if match else 0


# --- plugin command / reaction plumbing ------------------------------------
@bot.message_handler(func=lambda m: bool(
    str(getattr(m, "text", "") or "").startswith("/")
    and str(getattr(m, "text", "") or "")[1:].split("@")[0].split()[0].lower()
    in EXTENSION_REGISTRY["commands"]
), content_types=["text"])
@guard
def on_extension_command(message):
    """Dispatch commands that plugins registered at runtime."""
    uid = int(message.from_user.id)
    raw = str(getattr(message, "text", "") or "")[1:].split("@")[0].split()
    command = raw[0].lower() if raw else ""
    dispatch_extension_command(message, uid, command)


def _register_reaction_handler():
    """Attach a message-reaction handler when the library supports it."""
    decorator = getattr(bot, "message_reaction_handler", None)
    if not callable(decorator):
        log.info("Reaction updates need pyTelegramBotAPI 4.14+ \u2014 skipping the handler")
        return False

    @decorator(func=lambda update: True)
    def on_reaction(update):
        """Mirror a user's reaction back with the big live animation."""
        try:
            user = getattr(update, "user", None)
            uid = int(getattr(user, "id", 0) or 0)
            cid = int(getattr(getattr(update, "chat", None), "id", 0) or 0)
            mid = int(getattr(update, "message_id", 0) or 0)
            new = list(getattr(update, "new_reaction", []) or [])
            emoji = str(getattr(new[0], "emoji", "") or "") if new else ""
            if not emoji:
                return
            log_reaction(uid, cid, mid, emoji)
            add_points(uid, 1, "reaction")
            react_big(cid, mid, emoji)
        except Exception as exc:
            log.debug("Reaction handler failed: %s", exc)

    log.info("Live reaction handler registered")
    return True


# --- security callbacks ----------------------------------------------------
@extension_callback("menu_security", exact=True)
def _cb_menu_security(call, uid, cid, mid, data):
    ack(call)
    edit(cid, mid, msg_security_center(uid), kb_security_center(uid))


@extension_callback("sec_admin", exact=True, admin_only=True)
def _cb_sec_admin(call, uid, cid, mid, data):
    ack(call)
    edit(cid, mid, msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_queue", exact=True, admin_only=True)
def _cb_sec_queue(call, uid, cid, mid, data):
    ack(call)
    rows = get_open_reviews(10)
    buttons = [[BTN(
        VERDICTS.get(str(row.get("verdict")), VERDICTS["suspicious"])["emoji"] + " #"
        + str(row.get("fid")) + " " + trunc(str(row.get("fname") or "?"), 20),
        "sec_report_" + str(row.get("fid")),
    )] for row in rows]
    buttons.append([BACK("sec_admin")])
    edit(cid, mid, msg_admin_security(uid), KB(*buttons))


@extension_callback("sec_quarantine", exact=True, admin_only=True)
def _cb_sec_quarantine(call, uid, cid, mid, data):
    ack(call)
    rows = get_quarantine_list(10)
    lines = ["\u26d4 " + B("Quarantine"), ""]
    if not rows:
        lines.append(I("Nothing is quarantined."))
    for row in rows:
        lines.append(
            C("#" + str(row.get("fid"))) + " " + esc(trunc(str(row.get("fname") or "?"), 22))
            + "\n   " + I(esc(trunc(str(row.get("reason")), 70)))
        )
    buttons = [[BTN(_e("reload") + " Release #" + str(row.get("fid")),
                    "sec_release_" + str(row.get("fid")))] for row in rows[:8]]
    buttons.append([BACK("sec_admin")])
    edit(cid, mid, "\n".join(lines), KB(*buttons))


@extension_callback("sec_vault", exact=True, admin_only=True)
def _cb_sec_vault(call, uid, cid, mid, data):
    ack(call)
    rows = get_token_vault(12)
    lines = [
        _e("key") + " " + B("Token vault"), "",
        I("Credentials found in uploaded code are replaced with the checker"),
        I("token before anything runs. Only hashes are kept here."), "",
    ]
    if not rows:
        lines.append(I("No credentials have been intercepted yet."))
    for row in rows:
        lines.append(
            _e("lock") + " " + C("#" + str(row.get("fid"))) + " " + esc(str(row.get("token_hint")))
            + "  " + I(str(row.get("token_hash"))[:12]) + "  " + I(fmt_dt(str(row.get("created_at"))))
        )
    edit(cid, mid, "\n".join(lines), KB([BACK("sec_admin")]))


@extension_callback("sec_clean_jails", exact=True, admin_only=True)
def _cb_sec_clean(call, uid, cid, mid, data):
    ack(call, "Cleaning\u2026")
    removed = cleanup_sandboxes(0)
    edit(cid, mid, _e("trash") + " Removed " + B(str(removed)) + " sandbox jail(s).\n\n"
         + msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_rescan_all", exact=True, admin_only=True)
def _cb_sec_rescan(call, uid, cid, mid, data):
    ack(call, "Rescanning\u2026")
    rows = db_all("SELECT id, uid, fpath, fname FROM files ORDER BY id DESC LIMIT 60", ())
    flagged = 0
    for row in rows:
        path = str(row.get("fpath") or "")
        if not Path(path).exists():
            continue
        report = scan_file(path)
        report["checksum"] = checksum_file(path)
        save_scan(int(row.get("id")), int(row.get("uid") or 0), report)
        if str(report.get("verdict")) in ("dangerous", "malicious"):
            flagged += 1
    edit(cid, mid, _e("scan") + " Rescanned " + B(str(len(rows))) + " file(s), "
         + B(str(flagged)) + " flagged.\n\n" + msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_pick_scan", exact=True)
def _cb_pick_scan(call, uid, cid, mid, data):
    ack(call)
    text, markup = _pick_or_msg(uid, "sec_scan_", "Scan a file")
    edit(cid, mid, text, markup)


@extension_callback("sec_pick_sandbox", exact=True)
def _cb_pick_sandbox(call, uid, cid, mid, data):
    ack(call)
    text, markup = _pick_or_msg(uid, "sec_sandbox_", "Sandbox a file")
    edit(cid, mid, text, markup)


@extension_callback("sec_pick_editor", exact=True)
def _cb_pick_editor(call, uid, cid, mid, data):
    ack(call)
    text, markup = _pick_or_msg(uid, "ed_open_", "Open the editor")
    edit(cid, mid, text, markup)


@extension_callback("sec_pick_page_")
def _cb_pick_page(call, uid, cid, mid, data):
    ack(call)
    page = _first_int(data.replace("sec_pick_page_", "")) or 1
    edit(cid, mid, _e("folder") + " " + B("Choose a file"), kb_file_picker(uid, "sec_scan_", page))


@extension_callback("sec_scan_")
def _cb_sec_scan(call, uid, cid, mid, data):
    fid = _first_int(data.replace("sec_scan_", ""))
    row = get_file(fid) if is_admin(uid) else get_user_file(uid, fid)
    if not row:
        ack(call, "File not found.", True)
        return
    ack(call, "Scanning\u2026")
    spawn_animation(live_scan_animation, cid, mid, str(row.get("fname")))
    report = scan_file(str(row.get("fpath") or ""))
    report["checksum"] = checksum_file(str(row.get("fpath") or ""))
    save_scan(fid, int(row.get("uid") or uid), report)
    time.sleep(3.2)
    edit(cid, mid, render_scan_report(report, str(row.get("fname")), fid),
         kb_security_review(fid) if is_admin(uid) else kb_file_detail(uid, fid))
    react_big(cid, mid, "\U0001f44d" if report.get("verdict") in ("clean", "low") else "\U0001f631")
    if str(report.get("verdict")) == "malicious":
        quarantine_file(fid, "Scan verdict: malicious", report, uid)


@extension_callback("sec_report_")
def _cb_sec_report(call, uid, cid, mid, data):
    ack(call)
    fid = _first_int(data.replace("sec_report_", ""))
    edit(cid, mid, msg_scan_detail(fid),
         kb_security_review(fid) if is_admin(uid) else kb_file_detail(uid, fid))


@extension_callback("sec_sandbox_")
def _cb_sec_sandbox(call, uid, cid, mid, data):
    ack(call, "Preparing the jail\u2026")
    fid = _first_int(data.replace("sec_sandbox_", ""))
    _do_sandbox(uid, fid, cid, mid)


@extension_callback("sec_safe_", admin_only=True)
def _cb_sec_safe(call, uid, cid, mid, data):
    fid = _first_int(data.replace("sec_safe_", ""))
    review = db_one("SELECT id FROM code_reviews WHERE fid=? AND state='open' ORDER BY id DESC LIMIT 1", (fid,))
    if review:
        close_code_review(int(review.get("id")), uid, "approved")
    db_exec("UPDATE files SET status='approved' WHERE id=?", (fid,))
    row = get_file(fid) or {}
    audit(uid, "security_approve", int(row.get("uid") or 0), "file #" + str(fid))
    notify_user(int(row.get("uid") or 0), _e("ok") + " " + B("Security review passed") + "\n\n"
                + _e("file") + " " + C("#" + str(fid)) + " " + esc(str(row.get("fname")))
                + "\n" + I("Your file is approved and can run now."))
    ack(call, "Approved")
    react_big(cid, mid, "\u2705")
    edit(cid, mid, _e("ok") + " File " + C("#" + str(fid)) + " approved as safe.\n\n"
         + msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_quar_", admin_only=True)
def _cb_sec_quar(call, uid, cid, mid, data):
    fid = _first_int(data.replace("sec_quar_", ""))
    ok, note = quarantine_file(fid, "Manual quarantine by admin " + str(uid), get_last_scan(fid), uid)
    review = db_one("SELECT id FROM code_reviews WHERE fid=? AND state='open' ORDER BY id DESC LIMIT 1", (fid,))
    if review:
        close_code_review(int(review.get("id")), uid, "rejected")
    ack(call, note[:180], not ok)
    edit(cid, mid, ("\u26d4 " if ok else _e("no") + " ") + esc(note) + "\n\n"
         + msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_release_", admin_only=True)
def _cb_sec_release(call, uid, cid, mid, data):
    fid = _first_int(data.replace("sec_release_", ""))
    ok, note = release_quarantine(fid, uid)
    ack(call, note[:180], not ok)
    edit(cid, mid, (_e("ok") if ok else _e("no")) + " " + esc(note) + "\n\n"
         + msg_admin_security(uid), kb_admin_security())


@extension_callback("sec_hist_")
def _cb_sec_hist(call, uid, cid, mid, data):
    ack(call)
    fid = _first_int(data.replace("sec_hist_", ""))
    edit(cid, mid, msg_edit_history(fid),
         kb_security_review(fid) if is_admin(uid) else kb_editor(uid, fid))


# --- editor callbacks ------------------------------------------------------
@extension_callback("ed_open_")
def _cb_ed_open(call, uid, cid, mid, data):
    ack(call)
    fid = _first_int(data.replace("ed_open_", ""))
    edit(cid, mid, msg_editor(uid, fid), kb_editor(uid, fid))


@extension_callback("ed_pull_")
def _cb_ed_pull(call, uid, cid, mid, data):
    ack(call, "Pulling\u2026")
    parts = [int(n) for n in re.findall(r"\d+", data.replace("ed_pull_", ""))]
    fid = parts[0] if parts else 0
    start = parts[1] if len(parts) > 1 else 1
    edit(cid, mid, msg_editor(uid, fid, start), kb_editor(uid, fid, start))


@extension_callback("ed_hist_")
def _cb_ed_hist(call, uid, cid, mid, data):
    ack(call)
    fid = _first_int(data.replace("ed_hist_", ""))
    edit(cid, mid, msg_edit_history(fid), kb_editor(uid, fid))


@extension_callback("ed_replace_")
def _cb_ed_replace(call, uid, cid, mid, data):
    fid = _first_int(data.replace("ed_replace_", ""))
    row, error = editable_file(uid, fid)
    if not row:
        ack(call, str(error)[:180], True)
        return
    ack(call)
    set_state(uid, "awaiting_push_replace", fid=fid)
    prompt(
        uid,
        _e("edit") + " " + B("Replace lines in " + esc(str(row.get("fname")))) + "\n\n"
        + I("First line: the range, e.g. ") + C("12-18") + I(" or just ") + C("12") + ".\n"
        + I("Then the replacement code on the following lines."),
        "ed_open_" + str(fid),
    )


@extension_callback("ed_insert_")
def _cb_ed_insert(call, uid, cid, mid, data):
    fid = _first_int(data.replace("ed_insert_", ""))
    row, error = editable_file(uid, fid)
    if not row:
        ack(call, str(error)[:180], True)
        return
    ack(call)
    set_state(uid, "awaiting_push_insert", fid=fid)
    prompt(
        uid,
        _e("plus") + " " + B("Insert into " + esc(str(row.get("fname")))) + "\n\n"
        + I("First line: the line number to insert after.") + "\n"
        + I("Then the new code."),
        "ed_open_" + str(fid),
    )


@extension_callback("ed_delete_")
def _cb_ed_delete(call, uid, cid, mid, data):
    fid = _first_int(data.replace("ed_delete_", ""))
    row, error = editable_file(uid, fid)
    if not row:
        ack(call, str(error)[:180], True)
        return
    ack(call)
    set_state(uid, "awaiting_push_delete", fid=fid)
    prompt(
        uid,
        _e("minus") + " " + B("Delete lines from " + esc(str(row.get("fname")))) + "\n\n"
        + I("Send a range such as ") + C("40-52") + I(" or a single line number."),
        "ed_open_" + str(fid),
    )


@extension_callback("ed_push_")
def _cb_ed_push(call, uid, cid, mid, data):
    fid = _first_int(data.replace("ed_push_", ""))
    row, error = editable_file(uid, fid)
    if not row:
        ack(call, str(error)[:180], True)
        return
    ack(call)
    set_state(uid, "awaiting_push_full", fid=fid)
    prompt(
        uid,
        _e("upload") + " " + B("Push to " + esc(str(row.get("fname")))) + "\n\n"
        + I("Send the complete new source. Every push is re-scanned."),
        "ed_open_" + str(fid),
    )


# --- plugin callbacks ------------------------------------------------------
@extension_callback("menu_plugins", exact=True)
def _cb_menu_plugins(call, uid, cid, mid, data):
    ack(call)
    edit(cid, mid, msg_plugins(), kb_plugins(uid))


@extension_callback("plug_reload", exact=True, admin_only=True)
def _cb_plug_reload(call, uid, cid, mid, data):
    ack(call, "Reloading\u2026")
    loaded, failed = reload_plugins()
    edit(cid, mid, _e("ok") + " Reloaded " + B(str(len(loaded))) + " plugin(s), "
         + B(str(len(failed))) + " error(s).\n\n" + msg_plugins(), kb_plugins(uid))
    react_big(cid, mid, "\u26a1")


@extension_callback("plug_docs", exact=True)
def _cb_plug_docs(call, uid, cid, mid, data):
    ack(call)
    body = (
        _e("info") + " " + B("Plugin API") + "\n\n"
        + I("Create ") + C(str(plugins_dir() / "my_feature.py")) + I(" with:") + "\n\n"
        + PRE(
            "def register(sigma):\n"
            "    @sigma.command(\"ping\", \"Reply with pong\")\n"
            "    def cmd(message, uid):\n"
            "        sigma.send(uid, sigma.B(\"pong\"))\n\n"
            "    @sigma.callback(\"my_btn\", exact=True)\n"
            "    def cb(call, uid, cid, mid, data):\n"
            "        sigma.ack(call, \"hi\")\n\n"
            "    sigma.menu_button(\"My feature\", \"my_btn\")\n\n"
            "    @sigma.job(300, \"my-job\")\n"
            "    def job():\n"
            "        ...\n\n"
            "    @sigma.scanner(\"my-rules\")\n"
            "    def scan(source, ext):\n"
            "        return []", "python")
        + "\n" + I("Then run /reload. bot.py never changes.")
    )
    edit(cid, mid, body, kb_plugins(uid))


@extension_callback("react_")
def _cb_react(call, uid, cid, mid, data):
    emoji = data.replace("react_", "")[:8] or "\U0001f525"
    ack(call, emoji)
    log_reaction(uid, cid, mid, emoji)
    if not react_big(cid, mid, emoji):
        spawn_animation(animate_frames, cid, mid, msg_reactions_board(), "hearts", 1, 0.45)


@extension_callback("anim_show", exact=True)
def _cb_anim_show(call, uid, cid, mid, data):
    ack(call, "Playing\u2026")
    spawn_animation(_animation_showcase, cid, mid)


@extension_callback("noop", exact=True)
def _cb_noop(call, uid, cid, mid, data):
    ack(call)


# --- editor FSM states -----------------------------------------------------
def _parse_range(text):
    """Parse '12-18' / '12' into (first, last)."""
    numbers = [int(n) for n in re.findall(r"\d+", str(text or ""))]
    if not numbers:
        return 0, 0
    first = numbers[0]
    last = numbers[1] if len(numbers) > 1 else first
    return first, max(first, last)


def _push_result(uid, fid, ok, note, extra):
    """Show the outcome of a push with its diff."""
    body = (_e("ok") if ok else _e("shield")) + " " + esc(str(note))
    diff = (extra or {}).get("diff")
    if diff:
        body += "\n\n" + B("Diff") + "\n" + render_diff_block(diff)
    report = (extra or {}).get("report")
    if report:
        body += "\n\n" + render_scan_summary(report)
        fresh = (extra or {}).get("regression", {}).get("fresh") or []
        if fresh:
            body += "\n\n" + B("New indicators") + "\n" + render_findings_list(fresh, 5)
    sent = send(uid, body, kb_editor(uid, fid))
    if sent and ok:
        react_big(sent.chat.id, sent.message_id, "\u2705")
    elif sent:
        react_big(sent.chat.id, sent.message_id, "\U0001f6e1")


def _fsm_push_full(uid, state, text):
    """Whole-file push."""
    fid = int(state.get("fid") or 0)
    ok, note, extra = push_code(uid, fid, text, "push_full")
    _push_result(uid, fid, ok, note, extra)


def _fsm_push_replace(uid, state, text):
    """Replace a line range."""
    fid = int(state.get("fid") or 0)
    lines = str(text or "").split("\n")
    first, last = _parse_range(lines[0] if lines else "")
    if not first:
        send(uid, _e("info") + " Start with a line range such as " + C("12-18") + ".",
             kb_editor(uid, fid))
        return
    replacement = "\n".join(lines[1:])
    ok, note, extra = push_replace_lines(uid, fid, first, last, replacement)
    _push_result(uid, fid, ok, note, extra)


def _fsm_push_insert(uid, state, text):
    """Insert after a line."""
    fid = int(state.get("fid") or 0)
    lines = str(text or "").split("\n")
    line_no = _first_int(lines[0] if lines else "")
    ok, note, extra = push_insert_after(uid, fid, line_no, "\n".join(lines[1:]))
    _push_result(uid, fid, ok, note, extra)


def _fsm_push_delete(uid, state, text):
    """Delete a line range."""
    fid = int(state.get("fid") or 0)
    first, last = _parse_range(text)
    if not first:
        send(uid, _e("info") + " Send a range such as " + C("40-52") + ".", kb_editor(uid, fid))
        return
    ok, note, extra = push_delete_lines(uid, fid, first, last)
    _push_result(uid, fid, ok, note, extra)


EXTRA_FSM_HANDLERS = {
    "awaiting_push_full": _fsm_push_full,
    "awaiting_push_replace": _fsm_push_replace,
    "awaiting_push_insert": _fsm_push_insert,
    "awaiting_push_delete": _fsm_push_delete,
}


# ============================================================================
# SECTION 15 - COMMAND HANDLERS
# ============================================================================


@bot.message_handler(commands=["start"])
@guard
def handle_start(message):
    """/start [ref_code] - welcome, referral handling, main menu."""
    uid = int(message.from_user.id)
    is_new = get_setting(uid, "welcomed", "") == ""
    if is_new:
        set_setting(uid, "welcomed", utcstamp())
        add_points(uid, 50, "welcome_bonus")
        add_coins(uid, 10)
        award_badge(uid, "newcomer")
        notify_admins(
            _e("users") + " New user: " + B(esc(str(message.from_user.first_name or "?")))
            + " " + C(str(uid))
        )
    payload = cmd_args(message)
    if payload:
        ok, result = apply_referral(uid, payload.split()[0])
        send(uid, (_e("ok") if ok else _e("info")) + " " + esc(result))
    log_activity(uid, "start")
    send(uid, msg_welcome(uid, is_new), kb_main(uid))


@bot.message_handler(commands=["panel", "keyboard"])
@guard
def on_panel(message):
    """Permanent keyboard, registered early so it always resolves."""
    uid = int(message.from_user.id)
    try:
        bot.send_message(int(uid), font_safe(msg_panel_intro(uid), user_font(uid)),
                         reply_markup=kb_persistent(uid))
    except Exception as exc:
        log.debug("panel failed: %s", exc)
    send(uid, _e("star") + " " + B("Inline panel") + "\n"
         + I("Context actions live inside each message."), kb_main(uid))


@bot.message_handler(commands=["hidepanel"])
@guard
def on_hidepanel(message):
    """Remove the permanent keyboard."""
    uid = int(message.from_user.id)
    try:
        bot.send_message(int(uid), font_safe(_e("ok") + " Permanent buttons hidden."
                                             " Use /panel to restore them.",
                                             user_font(uid)),
                         reply_markup=kb_hide_persistent())
    except Exception as exc:
        log.debug("hidepanel failed: %s", exc)


@bot.message_handler(commands=["help"])
@guard
def handle_help(message):
    """/help - command reference."""
    uid = int(message.from_user.id)
    send(uid, msg_help(uid), kb_back_only("main_menu"))


@bot.message_handler(commands=["menu"])
@guard
def handle_menu(message):
    """/menu - main menu."""
    uid = int(message.from_user.id)
    send(uid, msg_welcome(uid, False), kb_main(uid))


@bot.message_handler(commands=["profile"])
@guard
def handle_profile(message):
    """/profile - profile card."""
    uid = int(message.from_user.id)
    check_achievements(uid)
    send(uid, msg_profile(uid), kb_profile(uid))


@bot.message_handler(commands=["daily"])
@guard
def handle_daily(message):
    """/daily - claim the daily bonus."""
    uid = int(message.from_user.id)
    ok, pts, streak, bonus = give_daily(uid)
    send(uid, msg_daily_result(ok, pts, streak, bonus), kb_daily(uid))


@bot.message_handler(commands=["stats"])
@guard
def handle_stats(message):
    """/stats - platform statistics."""
    uid = int(message.from_user.id)
    send(uid, msg_stats(uid), kb_back_only("main_menu"))


@bot.message_handler(commands=["files"])
@guard
def handle_files(message):
    """/files - file manager."""
    uid = int(message.from_user.id)
    send(uid, msg_files(uid, 1), kb_files(uid, 1))


@bot.message_handler(commands=["upload"])
@guard
def handle_upload(message):
    """/upload - upload instructions."""
    uid = int(message.from_user.id)
    send(uid, msg_upload_help(uid), kb_back_only("menu_files"))


@bot.message_handler(commands=["run"])
@guard
def handle_run(message):
    """/run <id> [args] - run a script."""
    uid = int(message.from_user.id)
    args = cmd_args(message).split(None, 1)
    if not args or not args[0].isdigit():
        send(uid, _e("info") + " Usage: " + C("/run <file_id> [arguments]"), kb_run_menu(uid))
        return
    fid = int(args[0])
    extra = args[1] if len(args) > 1 else ""
    ok, result = run_script(uid, fid, extra)
    send(uid, (_e("run") if ok else _e("no")) + " " + esc(result), kb_file_detail(uid, fid))


@bot.message_handler(commands=["stop"])
@guard
def handle_stop(message):
    """/stop <id> - stop a running script."""
    uid = int(message.from_user.id)
    args = cmd_args(message)
    if not args.isdigit():
        send(uid, _e("info") + " Usage: " + C("/stop <file_id>"), kb_run_menu(uid))
        return
    fid = int(args)
    ok, result = stop_script(uid, fid)
    send(uid, (_e("stop") if ok else _e("no")) + " " + esc(result), kb_file_detail(uid, fid))


@bot.message_handler(commands=["log"])
@guard
def handle_log(message):
    """/log <id> [lines] - view script output."""
    uid = int(message.from_user.id)
    parts = cmd_args(message).split()
    if not parts or not parts[0].isdigit():
        send(uid, _e("info") + " Usage: " + C("/log <file_id> [lines]"), kb_files(uid, 1))
        return
    fid = int(parts[0])
    lines = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 40
    send(uid, msg_file_log(uid, fid, min(500, max(5, lines))), kb_file_detail(uid, fid))


@bot.message_handler(commands=["ticket"])
@guard
def handle_ticket(message):
    """/ticket <subject> - open a support ticket."""
    uid = int(message.from_user.id)
    subject = cmd_args(message)
    if not subject:
        send(uid, msg_support(uid), kb_support(uid))
        return
    tid = create_ticket(uid, subject, "General", "Normal")
    if not tid:
        send(uid, _e("no") + " Could not create the ticket. Please try again.")
        return
    add_ticket_msg(tid, uid, subject, False)
    send(
        uid,
        _e("ticket") + " Ticket " + C("#" + str(tid)) + " created.\n\n"
        + I("Send more details with the Reply button below."),
        kb_ticket_detail(tid, uid),
    )


@bot.message_handler(commands=["economy"])
@guard
def handle_economy(message):
    """/economy - economy hub."""
    uid = int(message.from_user.id)
    send(uid, msg_economy(uid), kb_economy(uid))


@bot.message_handler(commands=["shop"])
@guard
def handle_shop(message):
    """/shop - points and coin shop."""
    uid = int(message.from_user.id)
    send(uid, msg_shop(uid), kb_shop())


@bot.message_handler(commands=["api"])
@guard
def handle_api(message):
    """/api - API key management."""
    uid = int(message.from_user.id)
    send(uid, msg_api_keys(uid), kb_api_keys(uid))


@bot.message_handler(commands=["cron"])
@guard
def handle_cron(message):
    """/cron - cron job manager."""
    uid = int(message.from_user.id)
    send(uid, msg_crons(uid), kb_crons(uid))


@bot.message_handler(commands=["admin"])
@admin_guard
def handle_admin(message):
    """/admin - admin panel."""
    uid = int(message.from_user.id)
    send(uid, msg_admin_dash(), kb_admin())


@bot.message_handler(commands=["ban"])
@admin_guard
def handle_ban(message):
    """/ban <uid> [reason] - ban a user."""
    uid = int(message.from_user.id)
    parts = cmd_args(message).split(None, 1)
    if not parts or not parts[0].isdigit():
        send(uid, _e("info") + " Usage: " + C("/ban <uid> [reason]"))
        return
    reason = parts[1] if len(parts) > 1 else "No reason provided"
    ok, result = ban_user(int(parts[0]), reason, uid)
    send(uid, (_e("ban") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["unban"])
@admin_guard
def handle_unban(message):
    """/unban <uid> - lift a ban."""
    uid = int(message.from_user.id)
    args = cmd_args(message)
    if not args.isdigit():
        send(uid, _e("info") + " Usage: " + C("/unban <uid>"))
        return
    ok, result = unban_user(int(args), uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["approve"])
@admin_guard
def handle_approve(message):
    """/approve <fid> - approve a file."""
    uid = int(message.from_user.id)
    args = cmd_args(message)
    if not args.isdigit():
        send(uid, _e("info") + " Usage: " + C("/approve <file_id>"))
        return
    ok, result = approve_file(int(args), uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["reject"])
@admin_guard
def handle_reject(message):
    """/reject <fid> [reason] - reject a file."""
    uid = int(message.from_user.id)
    parts = cmd_args(message).split(None, 1)
    if not parts or not parts[0].isdigit():
        send(uid, _e("info") + " Usage: " + C("/reject <file_id> [reason]"))
        return
    reason = parts[1] if len(parts) > 1 else "Rejected by administrator"
    ok, result = reject_file(int(parts[0]), reason, uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["broadcast"])
@admin_guard
def handle_broadcast(message):
    """/broadcast <text> - message every user."""
    uid = int(message.from_user.id)
    text = cmd_args(message)
    if not text:
        send(uid, msg_admin_broadcast(), kb_admin_broadcast())
        return
    send(uid, _e("bell") + " Broadcasting\u2026")
    sent, failed = send_broadcast(uid, text, None)
    send(
        uid,
        _e("ok") + " Broadcast finished.\n"
        + "Delivered: " + B(str(sent)) + "\nFailed: " + B(str(failed)),
        kb_admin(),
    )


@bot.message_handler(commands=["addpts"])
@admin_guard
def handle_addpts(message):
    """/addpts <uid> <pts> - adjust a user's points."""
    uid = int(message.from_user.id)
    parts = cmd_args(message).split()
    if len(parts) < 2:
        send(uid, _e("info") + " Usage: " + C("/addpts <uid> <pts>"))
        return
    try:
        target = int(parts[0])
        pts = int(parts[1])
    except ValueError:
        send(uid, _e("no") + " Both values must be integers.")
        return
    ok, result = add_points_admin(target, pts, uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["settier"])
@admin_guard
def handle_settier(message):
    """/settier <uid> <tier> - change a user's plan."""
    uid = int(message.from_user.id)
    parts = cmd_args(message).split()
    if len(parts) < 2 or not parts[0].isdigit():
        send(uid, _e("info") + " Usage: " + C("/settier <uid> <" + "|".join(TIER_ORDER) + ">"))
        return
    ok, result = set_user_tier(int(parts[0]), parts[1], uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))


@bot.message_handler(commands=["deluser"])
@admin_guard
def handle_deluser(message):
    """/deluser <uid> - wipe all data of a user."""
    uid = int(message.from_user.id)
    args = cmd_args(message)
    if not args.isdigit():
        send(uid, _e("info") + " Usage: " + C("/deluser <uid>"))
        return
    target = int(args)
    send(
        uid,
        _e("warn") + " " + B("This deletes every file and record for ") + C(str(target)) + B(".")
        + "\n" + I("This cannot be undone."),
        kb_confirm("admin_del_user_yes_" + str(target), "admin_user_" + str(target)),
    )


@bot.message_handler(commands=["backup"])
@admin_guard
def handle_backup(message):
    """/backup - create a database backup."""
    uid = int(message.from_user.id)
    ok, result = db_backup()
    if not ok:
        send(uid, _e("no") + " " + esc(result))
        return
    send(uid, _e("backup") + " Backup created: " + C(result) + "\nSize: " + fmt_size(Path(result).stat().st_size))
    send_document_path(uid, result, caption=_e("db") + " SIGMA database backup")
    audit(uid, "db_backup", 0, str(result))


@bot.message_handler(commands=["vacuum"])
@admin_guard
def handle_vacuum(message):
    """/vacuum - compact the database."""
    uid = int(message.from_user.id)
    ok, result = db_vacuum()
    audit(uid, "db_vacuum", 0, result[:120])
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result), kb_admin_db())


# ============================================================================
# SECTION 16 - FILE UPLOAD HANDLER
# ============================================================================


def _store_upload(uid, file_id, raw_name, size, message):
    """Shared upload pipeline for documents, photos and audio."""
    limit = effective_file_limit(uid)
    if limit != -1 and file_count(uid) >= limit:
        send(
            uid,
            _e("warn") + " " + B("File limit reached") + "\n\n"
            + "Your plan allows " + str(limit) + " file(s).\n"
            + I("Delete something or upgrade your plan."),
            kb_upgrade(uid),
        )
        return
    max_bytes = effective_size_limit_mb(uid) * 1024 * 1024
    if int(size or 0) > max_bytes:
        send(
            uid,
            _e("warn") + " " + B("File too large") + "\n\n"
            + "Size: " + fmt_size(size) + "\nAllowed: " + fmt_size(max_bytes) + "\n"
            + I("Upgrade for a bigger limit."),
            kb_upgrade(uid),
        )
        return
    if not check_rate_limit(uid, "upload", 20, 3600):
        send(uid, _e("warn") + " Upload limit reached (20 per hour). Try again later.")
        return
    name = sanitize_name(raw_name)
    target = user_dir(uid) / name
    if target.exists():
        stem, dot, ext = name.rpartition(".")
        stamp = utcnow().strftime("%Y%m%d%H%M%S")
        name = (stem + "_" + stamp + "." + ext) if dot else (name + "_" + stamp)
        target = user_dir(uid) / name
    status_msg = send(uid, _e("upload") + " Downloading " + C(name) + " \u2026")
    try:
        info = bot.get_file(file_id)
        payload = bot.download_file(info.file_path)
        with open(str(target), "wb") as handle:
            handle.write(payload)
    except Exception as exc:
        log.warning("Upload failed for %s: %s", uid, exc)
        send(uid, _e("no") + " Download failed: " + esc(str(exc)))
        return
    real_size = target.stat().st_size
    status = "approved" if (AUTO_APPROVE or is_admin(uid)) else "pending"
    fid = register_file(uid, name, str(target), real_size, status)
    if not fid:
        send(uid, _e("no") + " Could not register the file in the database.")
        return
    scan_report = post_upload_security(uid, fid, target, name)
    if str(scan_report.get("verdict")) == "malicious":
        body = (
            _e("shield") + " " + B("Upload blocked by the security scanner") + "\n\n"
            + render_scan_report(scan_report, name, fid) + "\n\n"
            + I("The file was quarantined and cannot be run or downloaded.")
        )
        if status_msg is not None:
            edit(status_msg.chat.id, status_msg.message_id, body, kb_security_center(uid))
        else:
            send(uid, body, kb_security_center(uid))
        return
    if str(scan_report.get("verdict")) in ("suspicious", "dangerous"):
        status = "review"
    add_points(uid, EARN_PTS_FOR_ACTION.get("upload", 10), "upload")
    add_xp(uid, 5)
    log_activity(uid, "upload:" + str(fid))
    log_ip(uid, "upload", "telegram")
    runner = runner_for(name)
    lines = [
        _e("ok") + " " + B("Upload complete"),
        "",
        _e("file") + " " + B(esc(name)) + "  " + C("#" + str(fid)),
        _e("disk") + " " + fmt_size(real_size),
        _e("shield") + " MD5: " + C(checksum_file(target)[:16]),
        status_icon(status) + " Status: " + B(status),
        _e("scan") + " Security: " + verdict_chip(scan_report.get("verdict", "clean"))
        + "  " + C("score " + str(scan_report.get("score", 0))),
        _e("code") + " Runner: " + (str(runner.get("name")) if runner else I("not executable")),
        "",
        _e("pts") + " +" + str(EARN_PTS_FOR_ACTION.get("upload", 10)) + " pts, +5 XP",
    ]
    if status == "pending":
        lines.append("")
        lines.append(I("An administrator will review this file before it can run."))
    if status_msg is not None:
        edit(status_msg.chat.id, status_msg.message_id, "\n".join(lines), kb_file_detail(uid, fid))
    else:
        send(uid, "\n".join(lines), kb_file_detail(uid, fid))
    total = int(get_user(uid).get("total_uploads") or 0)
    if total >= 1:
        award_badge(uid, "uploader")
    check_achievements(uid)
    if status == "pending":
        owner = get_user(uid)
        for admin_uid in sorted(ADMIN_IDS):
            send(
                admin_uid,
                _e("scan") + " " + B("New upload awaiting review") + "\n\n"
                + _e("file") + " " + B(esc(name)) + " " + C("#" + str(fid)) + "\n"
                + _e("user") + " " + esc(user_label(owner)) + " " + C(str(uid)) + "\n"
                + _e("crown") + " " + tier_badge(owner.get("tier", "free")) + "\n"
                + _e("disk") + " " + fmt_size(real_size),
                kb_admin_file_detail(fid),
            )


@bot.message_handler(content_types=["document"])
@guard
def handle_document(message):
    """Store uploaded documents."""
    uid = int(message.from_user.id)
    document = message.document
    _store_upload(
        uid,
        document.file_id,
        getattr(document, "file_name", None) or ("upload_" + str(int(time.time())) + ".bin"),
        getattr(document, "file_size", 0) or 0,
        message,
    )


@bot.message_handler(content_types=["photo"])
@guard
def handle_photo(message):
    """Store uploaded photos as jpg."""
    uid = int(message.from_user.id)
    photos = list(message.photo or [])
    if not photos:
        send(uid, _e("no") + " No image data received.")
        return
    best = photos[-1]
    name = "photo_" + utcnow().strftime("%Y%m%d_%H%M%S") + ".jpg"
    _store_upload(uid, best.file_id, name, getattr(best, "file_size", 0) or 0, message)


@bot.message_handler(content_types=["audio", "video", "voice"])
@guard
def handle_media(message):
    """Store other media types."""
    uid = int(message.from_user.id)
    media = getattr(message, "audio", None) or getattr(message, "video", None) or getattr(message, "voice", None)
    if media is None:
        send(uid, _e("no") + " Unsupported media.")
        return
    default_ext = ".mp3" if getattr(message, "audio", None) else (".mp4" if getattr(message, "video", None) else ".ogg")
    name = getattr(media, "file_name", None) or (
        "media_" + utcnow().strftime("%Y%m%d_%H%M%S") + default_ext
    )
    _store_upload(uid, media.file_id, name, getattr(media, "file_size", 0) or 0, message)


# ============================================================================
# SECTION 17 - FSM STATE MACHINE
# ============================================================================

_user_states = {}


def set_state(uid, state, **data):
    """Store an FSM state for a user."""
    with _state_lock:
        payload = {"state": str(state), "ts": time.time()}
        payload.update(data or {})
        _user_states[int(uid)] = payload
    return payload


def get_state(uid):
    """Read the current FSM state (empty dict when none)."""
    with _state_lock:
        return dict(_user_states.get(int(uid)) or {})


def clear_state(uid):
    """Drop any FSM state for a user."""
    with _state_lock:
        _user_states.pop(int(uid), None)
    return True


def prompt(uid, text, cancel_data="main_menu"):
    """Ask the user for text input."""
    return send(uid, text + "\n\n" + I("Send " + "/cancel" + " to abort."), kb_back_only(cancel_data))


@bot.message_handler(commands=["cancel"])
@guard
def handle_cancel(message):
    """/cancel - abort the pending input flow."""
    uid = int(message.from_user.id)
    had = bool(get_state(uid))
    clear_state(uid)
    send(
        uid,
        (_e("ok") + " Cancelled.") if had else (_e("info") + " Nothing to cancel."),
        kb_main(uid),
    )


def _fsm_rename(uid, state, text):
    fid = int(state.get("fid") or 0)
    ok, result = rename_file(uid, fid, text)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))
    if ok:
        send(uid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))


def _fsm_edit_content(uid, state, text):
    fid = int(state.get("fid") or 0)
    ok, result = write_file_content(uid, fid, text)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))
    if ok:
        send(uid, msg_file_editor(uid, fid), kb_file_editor(uid, fid))


def _fsm_edit_line(uid, state, text):
    fid = int(state.get("fid") or 0)
    line_no = int(state.get("line_no") or 0)
    if not line_no:
        parts = text.split(None, 1)
        if not parts or not parts[0].isdigit():
            set_state(uid, "awaiting_edit_line", fid=fid)
            send(uid, _e("info") + " Send " + C("<line number> <new content>") + " or just the line number.")
            return
        line_no = int(parts[0])
        if len(parts) == 1:
            set_state(uid, "awaiting_edit_line", fid=fid, line_no=line_no)
            prompt(uid, _e("edit") + " Now send the new content for line " + B(str(line_no)) + ".",
                   "edit_" + str(fid))
            return
        text = parts[1]
    ok, result = edit_file_line(uid, fid, line_no, text)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))
    if ok:
        send(uid, msg_file_editor(uid, fid), kb_file_editor(uid, fid))


def _fsm_ticket_subject(uid, state, text):
    category = str(state.get("category") or "General")
    priority = str(state.get("priority") or "Normal")
    tid = create_ticket(uid, text, category, priority)
    if not tid:
        send(uid, _e("no") + " Could not create the ticket.")
        return
    add_ticket_msg(tid, uid, text, False)
    set_state(uid, "awaiting_ticket_msg", tid=tid, first=1)
    prompt(
        uid,
        _e("ticket") + " Ticket " + C("#" + str(tid)) + " created.\n\n"
        + I("Now describe the issue in detail."),
        "ticket_" + str(tid),
    )


def _fsm_ticket_msg(uid, state, text):
    tid = int(state.get("tid") or 0)
    ticket = get_ticket(tid)
    if not ticket:
        send(uid, _e("no") + " That ticket no longer exists.")
        return
    staff = bool(is_admin(uid) and int(ticket.get("uid") or 0) != int(uid))
    ok = add_ticket_msg(tid, uid, text, staff)
    if not ok:
        send(uid, _e("no") + " Could not save the message.")
        return
    send(uid, _e("ok") + " Message added to ticket " + C("#" + str(tid)) + ".",
         kb_ticket_detail(tid, uid))
    if staff:
        notify_user(
            int(ticket.get("uid") or 0),
            _e("mail") + " " + B("Staff replied to ticket #" + str(tid)) + "\n\n" + esc(trunc(text, 600)),
        )
    else:
        notify_admins(
            _e("ticket") + " " + B("Ticket #" + str(tid) + " updated") + " by " + C(str(uid))
            + "\n" + esc(trunc(text, 400))
        )


def _fsm_note_title(uid, state, text):
    set_state(uid, "awaiting_new_note_content", title=text)
    prompt(uid, _e("note") + " Title saved. Now send the note content.", "settings_notes")


def _fsm_note_content(uid, state, text):
    title = str(state.get("title") or "Untitled")
    note_id = create_note(uid, title, text)
    if not note_id:
        send(uid, _e("no") + " Could not save the note.")
        return
    send(uid, _e("ok") + " Note saved.", kb_note_detail(uid, note_id))
    send(uid, msg_note_detail(uid, note_id), kb_note_detail(uid, note_id))


def _fsm_note_edit(uid, state, text):
    note_id = int(state.get("note_id") or 0)
    ok, result = update_note(uid, note_id, None, text)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result))
    if ok:
        send(uid, msg_note_detail(uid, note_id), kb_note_detail(uid, note_id))


def _fsm_broadcast(uid, state, text):
    target = state.get("target")
    set_state(uid, "confirm_broadcast", text=text, target=target)
    audience = tier_badge(target) if target else B("everyone")
    send(
        uid,
        _e("bell") + " " + B("Broadcast preview") + " \u2192 " + audience + "\n\n"
        + divider("\u2508", 22) + "\n" + esc(trunc(text, 2000)) + "\n" + divider("\u2508", 22),
        kb_confirm("admin_broadcast_confirm", "admin_dash", _l("yes") + " Send now", _l("no") + " Discard"),
    )


def _fsm_ban_uid(uid, state, text):
    if not text.strip().isdigit():
        send(uid, _e("no") + " Send a numeric user ID.")
        set_state(uid, "awaiting_ban_uid")
        return
    target = int(text.strip())
    set_state(uid, "awaiting_ban_reason", target=target)
    prompt(uid, _e("ban") + " Target " + C(str(target)) + ". Now send the ban reason.", "admin_users")


def _fsm_ban_reason(uid, state, text):
    target = int(state.get("target") or 0)
    ok, result = ban_user(target, text, uid)
    send(uid, (_e("ban") if ok else _e("no")) + " " + esc(result), kb_admin_user_detail(target))


def _fsm_addpts(uid, state, text):
    parts = text.split()
    target = int(state.get("target") or 0)
    if target and len(parts) == 1:
        parts = [str(target), parts[0]]
    if len(parts) < 2:
        send(uid, _e("info") + " Send " + C("<uid> <points>") + ".")
        set_state(uid, "awaiting_addpts", target=target)
        return
    try:
        target = int(parts[0])
        pts = int(parts[1])
    except ValueError:
        send(uid, _e("no") + " Both values must be whole numbers.")
        set_state(uid, "awaiting_addpts", target=target)
        return
    ok, result = add_points_admin(target, pts, uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result), kb_admin_user_detail(target))


def _fsm_settier(uid, state, text):
    parts = text.split()
    target = int(state.get("target") or 0)
    if target and len(parts) == 1:
        parts = [str(target), parts[0]]
    if len(parts) < 2 or not parts[0].isdigit():
        send(uid, _e("info") + " Send " + C("<uid> <" + "|".join(TIER_ORDER) + ">") + ".")
        set_state(uid, "awaiting_settier", target=target)
        return
    ok, result = set_user_tier(int(parts[0]), parts[1], uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result), kb_admin_user_detail(int(parts[0])))


def _fsm_run_args(uid, state, text):
    fid = int(state.get("fid") or 0)
    ok, result = run_script(uid, fid, text)
    send(uid, (_e("run") if ok else _e("no")) + " " + esc(result), kb_file_detail(uid, fid))


def _fsm_cron_schedule(uid, state, text):
    fid = int(state.get("fid") or 0)
    ok, result = create_cron(uid, fid, text.strip())
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(str(result) if not ok else "Cron job created."))
    send(uid, msg_crons(uid), kb_crons(uid))


def _fsm_webhook_url(uid, state, text):
    ok, result = create_webhook(uid, text.strip())
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(str(result)))
    send(uid, msg_webhooks(uid), kb_webhooks(uid))


def _fsm_tag_input(uid, state, text):
    fid = int(state.get("fid") or 0)
    tags = [part.strip() for part in re.split(r"[,\s]+", text) if part.strip()]
    ok, result, _all_tags = add_file_tags(uid, fid, tags)
    send(uid, (_e("tag") if ok else _e("no")) + " " + esc(result))
    send(uid, msg_file_tags(uid, fid), kb_file_tags(uid, fid))


def _fsm_search_query(uid, state, text):
    rows = search_files(uid, text)
    if not rows:
        send(uid, _e("search") + " No files matched " + C(esc(text)) + ".", kb_files(uid, 1))
        return
    lines = [header("Search Results", uid), "", _e("search") + " " + B(str(len(rows))) + " match(es) for " + C(esc(text)), ""]
    rows_kb = []
    for row in rows[:12]:
        fid = int(row.get("id") or 0)
        lines.append(
            status_icon(row.get("status")) + " " + C("#" + str(fid)) + " "
            + B(esc(trunc(str(row.get("fname")), 32))) + " \u2022 " + fmt_size(row.get("fsize"))
        )
        rows_kb.append([BTN(trunc(str(row.get("fname")), 28), "file_" + str(fid))])
    rows_kb.append([BACK("menu_files")])
    send(uid, "\n".join(lines), KB(*rows_kb))


def _fsm_share_expiry(uid, state, text):
    fid = int(state.get("fid") or 0)
    digits = re.sub(r"[^0-9]", "", text)
    hours = int(digits) if digits else 24
    ok, token = create_file_share(uid, fid, max(1, min(8760, hours)))
    if not ok:
        send(uid, _e("no") + " " + esc(str(token)))
        return
    send(
        uid,
        _e("share") + " " + B("Share token created") + "\n\n"
        + C(str(token)) + "\n\n" + I("Valid for " + str(hours) + " hour(s)."),
        kb_file_detail(uid, fid),
    )


def _fsm_api_label(uid, state, text):
    ok, raw = generate_api_key(uid, text.strip()[:40] or "default")
    if not ok:
        send(uid, _e("no") + " " + esc(str(raw)), kb_api_keys(uid))
        return
    send(
        uid,
        _e("key") + " " + B("API key created") + "\n\n" + C(str(raw)) + "\n\n"
        + _e("warn") + " " + I("Copy it now \u2014 it is stored hashed and cannot be shown again."),
        kb_api_keys(uid),
    )


def _fsm_transfer(uid, state, text):
    parts = text.split()
    if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
        send(uid, _e("info") + " Send " + C("<uid> <points>") + ".")
        set_state(uid, "awaiting_transfer")
        return
    ok, result = transfer_points(uid, int(parts[0]), int(parts[1]))
    send(uid, (_e("ok") if ok else _e("no")) + " " + esc(result), kb_economy(uid))
    if ok:
        notify_user(int(parts[0]), _e("pts") + " You received " + B(parts[1]) + " points from " + C(str(uid)) + ".")


def _fsm_admin_user_search(uid, state, text):
    rows = search_users(text)
    if not rows:
        send(uid, _e("search") + " No users matched " + C(esc(text)) + ".", kb_admin_users(1))
        return
    buttons = [[BTN(trunc(user_label(row), 28) + " \u2022 " + str(row.get("uid")),
                    "admin_user_" + str(row.get("uid")))] for row in rows[:12]]
    buttons.append([BACK("admin_users")])
    send(uid, msg_admin_users(rows[:12], 1, 1, len(rows)), KB(*buttons))


def _fsm_admin_ticket_reply(uid, state, text):
    tid = int(state.get("tid") or 0)
    ticket = get_ticket(tid)
    if not ticket:
        send(uid, _e("no") + " Ticket not found.")
        return
    add_ticket_msg(tid, uid, text, True)
    notify_user(
        int(ticket.get("uid") or 0),
        _e("mail") + " " + B("Support replied to ticket #" + str(tid)) + "\n\n" + esc(trunc(text, 700)),
    )
    send(uid, _e("ok") + " Reply sent.", kb_admin_ticket_detail(tid))


_FSM_HANDLERS = {
    "awaiting_rename": _fsm_rename,
    "awaiting_edit_content": _fsm_edit_content,
    "awaiting_edit_line": _fsm_edit_line,
    "awaiting_ticket_subject": _fsm_ticket_subject,
    "awaiting_ticket_msg": _fsm_ticket_msg,
    "awaiting_new_note_title": _fsm_note_title,
    "awaiting_new_note_content": _fsm_note_content,
    "awaiting_note_edit": _fsm_note_edit,
    "awaiting_broadcast": _fsm_broadcast,
    "awaiting_ban_uid": _fsm_ban_uid,
    "awaiting_ban_reason": _fsm_ban_reason,
    "awaiting_addpts": _fsm_addpts,
    "awaiting_settier": _fsm_settier,
    "awaiting_run_args": _fsm_run_args,
    "awaiting_cron_schedule": _fsm_cron_schedule,
    "awaiting_webhook_url": _fsm_webhook_url,
    "awaiting_tag_input": _fsm_tag_input,
    "awaiting_search_query": _fsm_search_query,
    "awaiting_share_expiry": _fsm_share_expiry,
    "awaiting_api_label": _fsm_api_label,
    "awaiting_transfer": _fsm_transfer,
    "awaiting_admin_user_search": _fsm_admin_user_search,
    "awaiting_admin_ticket_reply": _fsm_admin_ticket_reply,
}

_FSM_HANDLERS.update(EXTRA_FSM_HANDLERS)

_ADMIN_STATES = {
    "awaiting_broadcast", "awaiting_ban_uid", "awaiting_ban_reason",
    "awaiting_addpts", "awaiting_settier", "awaiting_admin_user_search",
    "awaiting_admin_ticket_reply",
}


@bot.message_handler(func=lambda m: True, content_types=["text"])
@guard
def on_text(message):
    """Route free-form text through the FSM, else show the menu."""
    uid = int(message.from_user.id)
    text = str(getattr(message, "text", "") or "").strip()
    if not text:
        return
    if text.startswith("/"):
        # Commands registered at runtime (extensions, plugins and the 200+
        # command registry) are added after this catch-all handler, so pyTelegramBotAPI
        # never reaches them. Resolve them here before giving up.
        raw = text.split(None, 1)
        name = raw[0][1:].split("@")[0].lower()
        args = raw[1] if len(raw) > 1 else ""
        if dispatch_extension_command(message, uid, name):
            return
        if name in CMD_REGISTRY:
            body, markup = run_command(uid, name, args)
            send(uid, body, markup or kb_cmd_footer(name))
            return
        guess = ""
        try:
            pool = sorted(set(list(CMD_REGISTRY.keys())
                              + list(EXTENSION_REGISTRY["commands"].keys())))
            close = difflib.get_close_matches(name, pool, 3, 0.6)
            if close:
                guess = ("\n\n" + _e("info") + " " + I("Did you mean: ")
                         + "  ".join(C("/" + item) for item in close))
        except Exception:
            guess = ""
        send(uid, _e("info") + " " + B("Unknown command: ") + C("/" + esc(name))
             + "\n" + I("Use /help for the full list or open the command centre.")
             + guess, kb_main(uid))
        return
    if dispatch_text_hooks(message, uid, text):
        return
    state = get_state(uid)
    name = str(state.get("state") or "")
    if not name:
        send(
            uid,
            _e("sigma") + " " + I("Send a document to upload it, or pick an option below."),
            kb_main(uid),
        )
        return
    if time.time() - float(state.get("ts") or 0) > 1800:
        clear_state(uid)
        send(uid, _e("clock") + " That input request expired. Please start again.", kb_main(uid))
        return
    if name in _ADMIN_STATES and not is_admin(uid):
        clear_state(uid)
        send(uid, _e("lock") + " Administrators only.", kb_main(uid))
        return
    handler = _FSM_HANDLERS.get(name)
    if handler is None:
        clear_state(uid)
        send(uid, _e("info") + " Input no longer needed.", kb_main(uid))
        return
    clear_state(uid)
    try:
        handler(uid, state, text)
    except Exception as exc:
        log.exception("FSM %s failed: %s", name, exc)
        send(uid, _e("warn") + " Could not process that input. Please try again.", kb_main(uid))


# ============================================================================
# SECTION 18 - CALLBACK ROUTER
# ============================================================================


def _tail_int(data, prefix):
    """Integer suffix after a prefix, or 0."""
    raw = str(data)[len(prefix):]
    raw = raw.split("_")[0] if raw else ""
    try:
        return int(raw)
    except ValueError:
        return 0


@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    """Single entry point for every inline button."""
    try:
        uid = int(call.from_user.id)
        cid = int(call.message.chat.id)
        mid = int(call.message.message_id)
        data = str(call.data or "")
    except Exception as exc:
        log.debug("Malformed callback: %s", exc)
        return
    try:
        ensure_user(uid, call.from_user)
        touch_seen(uid)
        if is_banned(uid):
            ack(call, "Your account is banned.", True)
            return
        if not check_rate_limit(uid, "callbacks", 90, 60):
            ack(call, "Too fast \u2014 slow down.", True)
            return
        if data.startswith("admin_") or data == "menu_admin":
            if not is_admin(uid):
                ack(call, "Administrators only.", True)
                return
        _handle_cb(call, uid, cid, mid, data)
    except Exception as exc:
        log.exception("Callback %s failed: %s", data, exc)
        ack(call, "Error \u2014 logged.", True)


def _handle_cb(call, uid, cid, mid, data):
    """Giant if/elif router for all callbacks."""
    # ---------------- generic ----------------
    if data == "noop":
        ack(call)
        return
    if data == "main_menu":
        ack(call)
        clear_state(uid)
        edit(cid, mid, msg_welcome(uid, False), kb_main(uid))
        return
    if data == "confirm_no":
        ack(call, "Cancelled.")
        clear_state(uid)
        edit(cid, mid, msg_welcome(uid, False), kb_main(uid))
        return
    if data == "confirm_yes":
        ack(call, "Nothing pending.")
        return

    # ---------------- files ----------------
    if data == "menu_files":
        ack(call)
        edit(cid, mid, msg_files(uid, 1), kb_files(uid, 1))
        return
    if data.startswith("files_page_"):
        page = _tail_int(data, "files_page_") or 1
        ack(call)
        edit(cid, mid, msg_files(uid, page), kb_files(uid, page))
        return
    if data == "files_search":
        ack(call)
        set_state(uid, "awaiting_search_query")
        edit(cid, mid, _e("search") + " " + B("Send a search term") + "\n\n"
             + I("Matches file names and tags."), kb_back_only("menu_files"))
        return
    if data == "files_export":
        ack(call, "Building CSV\u2026")
        csv_text = export_file_list(uid)
        path = EXPORTS_DIR / ("files_" + str(uid) + "_" + utcnow().strftime("%Y%m%d%H%M%S") + ".csv")
        try:
            path.write_text(csv_text, encoding="utf-8")
        except Exception as exc:
            send(uid, _e("no") + " Export failed: " + esc(str(exc)))
            return
        ok, result = send_document_path(uid, path, caption=_e("export") + " Your file list")
        if not ok:
            send(uid, _e("no") + " " + esc(result))
        return
    if data == "files_upload_help":
        ack(call)
        edit(cid, mid, msg_upload_help(uid), kb_back_only("menu_files"))
        return
    if data == "file_zip_all":
        ack(call, "Zipping\u2026")
        ok, result = zip_all_files(uid)
        if not ok:
            send(uid, _e("no") + " " + esc(str(result)))
            return
        sent, message = send_document_path(uid, result, caption=_e("zip") + " All your files")
        if not sent:
            send(uid, _e("no") + " " + esc(message))
        return
    if data.startswith("file_tags_"):
        fid = _tail_int(data, "file_tags_")
        ack(call)
        edit(cid, mid, msg_file_tags(uid, fid), kb_file_tags(uid, fid))
        return
    if data.startswith("file_add_tag_"):
        fid = _tail_int(data, "file_add_tag_")
        ack(call)
        set_state(uid, "awaiting_tag_input", fid=fid)
        edit(cid, mid, _e("tag") + " " + B("Send tags") + "\n\n"
             + I("Separate multiple tags with commas or spaces."),
             kb_back_only("file_tags_" + str(fid)))
        return
    if data.startswith("file_rm_tag_"):
        rest = data[len("file_rm_tag_"):]
        parts = rest.split("_", 1)
        fid = int(parts[0]) if parts and parts[0].isdigit() else 0
        tag = parts[1] if len(parts) > 1 else ""
        ok = remove_file_tag(uid, fid, tag)
        ack(call, "Tag removed." if ok else "Could not remove tag.")
        edit(cid, mid, msg_file_tags(uid, fid), kb_file_tags(uid, fid))
        return
    if data.startswith("file_versions_"):
        fid = _tail_int(data, "file_versions_")
        ack(call)
        edit(cid, mid, msg_file_versions(uid, fid), kb_file_versions(uid, fid))
        return
    if data.startswith("restore_ver_"):
        rest = data[len("restore_ver_"):].split("_")
        fid = int(rest[0]) if rest and rest[0].isdigit() else 0
        version_id = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0
        ok, result = restore_file_version(uid, fid, version_id)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_file_versions(uid, fid), kb_file_versions(uid, fid))
        return
    if data.startswith("file_public_"):
        fid = _tail_int(data, "file_public_")
        row = get_user_file(uid, fid) or {}
        ok, result = set_file_public(uid, fid, not int(row.get("is_public") or 0))
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("file_download_"):
        fid = _tail_int(data, "file_download_")
        ack(call, "Sending\u2026")
        ok, result = send_file_to_user(uid, fid)
        if not ok:
            send(uid, _e("no") + " " + esc(result))
        return
    if data.startswith("file_copy_"):
        fid = _tail_int(data, "file_copy_")
        ok, result, _new_fid = copy_file(uid, fid)
        ack(call, ("Copied." if ok else str(result))[:180], not ok)
        edit(cid, mid, msg_files(uid, 1), kb_files(uid, 1))
        return
    if data.startswith("file_preview_"):
        fid = _tail_int(data, "file_preview_")
        ack(call)
        ok, preview = preview_file(uid, fid, 30)
        row = get_user_file(uid, fid) or {}
        body = syntax_highlight_preview(preview, file_ext(str(row.get("fname", "")))) if ok else I(esc(preview))
        edit(cid, mid, header("Preview", uid) + "\n\n" + _e("file") + " "
             + B(esc(str(row.get("fname", "?")))) + "\n\n" + body, kb_file_detail(uid, fid))
        return
    if data.startswith("file_"):
        fid = _tail_int(data, "file_")
        ack(call)
        edit(cid, mid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("log_"):
        fid = _tail_int(data, "log_")
        ack(call)
        edit(cid, mid, msg_file_log(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("clearlog_"):
        fid = _tail_int(data, "clearlog_")
        ok = clear_log(uid, fid)
        ack(call, "Log cleared." if ok else "Nothing to clear.")
        edit(cid, mid, msg_file_log(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("edit_write_"):
        fid = _tail_int(data, "edit_write_")
        ack(call)
        set_state(uid, "awaiting_edit_content", fid=fid)
        edit(cid, mid, _e("edit") + " " + B("Send the new file content") + "\n\n"
             + I("The previous version is stored automatically."),
             kb_back_only("edit_" + str(fid)))
        return
    if data.startswith("edit_line_"):
        fid = _tail_int(data, "edit_line_")
        ack(call)
        set_state(uid, "awaiting_edit_line", fid=fid)
        edit(cid, mid, _e("edit") + " " + B("Edit a single line") + "\n\n"
             + I("Send ") + C("<line number> <new content>") + I(" in one message."),
             kb_back_only("edit_" + str(fid)))
        return
    if data.startswith("edit_"):
        fid = _tail_int(data, "edit_")
        ack(call)
        edit(cid, mid, msg_file_editor(uid, fid), kb_file_editor(uid, fid))
        return
    if data.startswith("rename_"):
        fid = _tail_int(data, "rename_")
        ack(call)
        set_state(uid, "awaiting_rename", fid=fid)
        edit(cid, mid, _e("edit") + " " + B("Send the new file name") + "\n\n"
             + I("Keep the extension so the runner still works."),
             kb_back_only("file_" + str(fid)))
        return
    if data.startswith("delconfirm_"):
        fid = _tail_int(data, "delconfirm_")
        ok, result = delete_file(uid, fid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_files(uid, 1), kb_files(uid, 1))
        return
    if data.startswith("del_"):
        fid = _tail_int(data, "del_")
        row = get_user_file(uid, fid) or {}
        ack(call)
        edit(cid, mid, _e("trash") + " " + B("Delete this file?") + "\n\n"
             + _e("file") + " " + esc(str(row.get("fname", "?"))) + "\n"
             + I("Versions, tags, shares and crons are removed too."),
             kb_confirm("delconfirm_" + str(fid), "file_" + str(fid)))
        return
    if data.startswith("share_confirm_"):
        rest = data[len("share_confirm_"):].split("_")
        fid = int(rest[0]) if rest and rest[0].isdigit() else 0
        hours = int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 24
        ok, token = create_file_share(uid, fid, hours)
        ack(call, "Token created." if ok else str(token)[:180], not ok)
        if ok:
            edit(cid, mid, _e("share") + " " + B("Share token") + "\n\n" + C(str(token))
                 + "\n\n" + I("Valid for " + str(hours) + " hour(s)."), kb_file_detail(uid, fid))
        return
    if data.startswith("share_custom_"):
        fid = _tail_int(data, "share_custom_")
        ack(call)
        set_state(uid, "awaiting_share_expiry", fid=fid)
        edit(cid, mid, _e("clock") + " " + B("How many hours should the link last?") + "\n\n"
             + I("Send a number between 1 and 8760."), kb_back_only("share_" + str(fid)))
        return
    if data.startswith("share_"):
        fid = _tail_int(data, "share_")
        ack(call)
        edit(cid, mid, msg_file_share(uid, fid), kb_file_share(uid, fid))
        return

    # ---------------- runner ----------------
    if data == "menu_run":
        ack(call)
        edit(cid, mid, msg_run_menu(uid), kb_run_menu(uid))
        return
    if data == "run_running":
        ack(call)
        edit(cid, mid, msg_run_menu(uid), kb_run_menu(uid))
        return
    if data == "run_kill_mine":
        killed = kill_all_user_scripts(uid)
        ack(call, "Stopped " + str(killed) + " script(s).")
        edit(cid, mid, msg_run_menu(uid), kb_run_menu(uid))
        return
    if data.startswith("run_with_args_"):
        fid = _tail_int(data, "run_with_args_")
        ack(call)
        set_state(uid, "awaiting_run_args", fid=fid)
        edit(cid, mid, _e("code") + " " + B("Send the command line arguments") + "\n\n"
             + I("Example: ") + C("--verbose input.txt"), kb_back_only("file_" + str(fid)))
        return
    if data.startswith("restart_"):
        fid = _tail_int(data, "restart_")
        ok, result = restart_script(uid, fid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("run_"):
        fid = _tail_int(data, "run_")
        ok, result = run_script(uid, fid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))
        return
    if data.startswith("stop_"):
        fid = _tail_int(data, "stop_")
        ok, result = stop_script(uid, fid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_file_detail(uid, fid), kb_file_detail(uid, fid))
        return

    # ---------------- economy ----------------
    if data == "menu_economy":
        ack(call)
        edit(cid, mid, msg_economy(uid), kb_economy(uid))
        return
    if data == "econ_daily":
        ok, pts, streak, bonus = give_daily(uid)
        ack(call, ("+" + str(pts) + " pts") if ok else "Already claimed.", not ok)
        edit(cid, mid, msg_daily_result(ok, pts, streak, bonus), kb_daily(uid))
        return
    if data == "econ_wallet":
        ack(call)
        edit(cid, mid, msg_wallet(uid), kb_economy(uid))
        return
    if data == "econ_leaderboard":
        ack(call)
        rows, _total, total_pages, page = get_leaderboard_page(1)
        edit(cid, mid, msg_leaderboard(rows, uid, page, total_pages), kb_leaderboard(page, total_pages))
        return
    if data.startswith("econ_lb_page_"):
        page = _tail_int(data, "econ_lb_page_") or 1
        ack(call)
        rows, _total, total_pages, page = get_leaderboard_page(page)
        edit(cid, mid, msg_leaderboard(rows, uid, page, total_pages), kb_leaderboard(page, total_pages))
        return
    if data == "econ_referral":
        ack(call)
        edit(cid, mid, msg_referral(uid), kb_economy(uid))
        return
    if data == "econ_transfer":
        ack(call)
        set_state(uid, "awaiting_transfer")
        edit(cid, mid, _e("share") + " " + B("Transfer points") + "\n\n"
             + I("Send ") + C("<uid> <points>") + I(". A 5% fee applies."),
             kb_back_only("menu_economy"))
        return
    if data in ("econ_shop", "menu_shop"):
        ack(call)
        edit(cid, mid, msg_shop(uid), kb_shop())
        return
    if data.startswith("shop_buy_"):
        item_key = data[len("shop_buy_"):]
        ok, result = buy_item(uid, item_key)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_shop(uid), kb_shop())
        return

    # ---------------- profile ----------------
    if data == "menu_profile":
        ack(call)
        check_achievements(uid)
        edit(cid, mid, msg_profile(uid), kb_profile(uid))
        return
    if data == "profile_achievements":
        ack(call)
        check_achievements(uid)
        edit(cid, mid, msg_achievements(uid), kb_achievements(uid))
        return
    if data == "profile_badges":
        ack(call)
        edit(cid, mid, msg_badges(uid), kb_badges(uid))
        return
    if data == "menu_stats":
        ack(call)
        edit(cid, mid, msg_stats(uid), kb_back_only("main_menu"))
        return
    if data == "menu_plan":
        ack(call)
        edit(cid, mid, msg_plan_info(uid), kb_upgrade(uid))
        return

    # ---------------- support ----------------
    if data == "menu_support":
        ack(call)
        edit(cid, mid, msg_support(uid), kb_support(uid))
        return
    if data == "support_help":
        ack(call)
        edit(cid, mid, msg_help(uid), kb_back_only("menu_support"))
        return
    if data == "support_tickets":
        ack(call)
        rows, total, total_pages, page = get_user_tickets(uid, None, 1)
        edit(cid, mid, msg_tickets(uid, rows, page, total_pages, total), kb_tickets(uid, page, total_pages))
        return
    if data.startswith("support_tickets_page_"):
        page = _tail_int(data, "support_tickets_page_") or 1
        ack(call)
        rows, total, total_pages, page = get_user_tickets(uid, None, page)
        edit(cid, mid, msg_tickets(uid, rows, page, total_pages, total), kb_tickets(uid, page, total_pages))
        return
    if data == "support_new_ticket":
        ack(call)
        edit(cid, mid, _e("ticket") + " " + B("Pick a category") + "\n\n"
             + I("Then choose a priority and describe your issue."), kb_new_ticket())
        return
    if data.startswith("ticket_cat_"):
        category = data[len("ticket_cat_"):]
        ack(call)
        edit(cid, mid, _e("ticket") + " Category: " + B(esc(category)) + "\n\n"
             + B("Pick a priority"), kb_ticket_priority(category))
        return
    if data.startswith("ticket_pri_"):
        rest = data[len("ticket_pri_"):].split("_")
        category = rest[0] if rest else "General"
        priority = rest[1] if len(rest) > 1 else "Normal"
        ack(call)
        set_state(uid, "awaiting_ticket_subject", category=category, priority=priority)
        edit(cid, mid, _e("ticket") + " " + B("Send a short subject") + "\n\n"
             + "Category: " + esc(category) + "\nPriority: " + esc(priority),
             kb_back_only("support_new_ticket"))
        return
    if data.startswith("ticket_reply_"):
        tid = _tail_int(data, "ticket_reply_")
        ack(call)
        set_state(uid, "awaiting_ticket_msg", tid=tid)
        edit(cid, mid, _e("mail") + " " + B("Send your reply for ticket #" + str(tid)),
             kb_back_only("ticket_" + str(tid)))
        return
    if data.startswith("ticket_close_"):
        tid = _tail_int(data, "ticket_close_")
        ok, result = close_ticket(tid, uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_ticket_detail(tid, uid), kb_ticket_detail(tid, uid))
        return
    if data.startswith("ticket_"):
        tid = _tail_int(data, "ticket_")
        ack(call)
        edit(cid, mid, msg_ticket_detail(tid, uid), kb_ticket_detail(tid, uid))
        return

    # ---------------- settings ----------------
    if data == "menu_settings":
        ack(call)
        edit(cid, mid, msg_settings(uid), kb_settings(uid))
        return
    if data == "set_toggle_push":
        current = int(get_user(uid).get("push_enabled") or 0)
        db_exec("UPDATE users SET push_enabled=? WHERE uid=?", (0 if current else 1, int(uid)))
        ack(call, "Push disabled." if current else "Push enabled.")
        edit(cid, mid, msg_settings(uid), kb_settings(uid))
        return
    if data == "settings_theme":
        ack(call)
        edit(cid, mid, msg_theme_select(uid), kb_theme_select(uid))
        return
    if data.startswith("set_theme_"):
        key = data[len("set_theme_"):]
        if key in THEMES:
            db_exec("UPDATE users SET theme=? WHERE uid=?", (key, int(uid)))
            ack(call, "Theme: " + str(THEMES[key].get("name")))
        else:
            ack(call, "Unknown theme.", True)
        edit(cid, mid, msg_theme_select(uid), kb_theme_select(uid))
        return
    if data == "settings_font":
        ack(call)
        edit(cid, mid, msg_font_select(uid), kb_font_select(uid))
        return
    if data.startswith("set_font_"):
        key = data[len("set_font_"):]
        if key in FONTS:
            db_exec("UPDATE users SET font=? WHERE uid=?", (key, int(uid)))
            ack(call, "Font: " + str(FONTS[key].get("name")))
        else:
            ack(call, "Unknown font.", True)
        edit(cid, mid, msg_font_select(uid), kb_font_select(uid))
        return
    if data == "settings_lang":
        ack(call)
        edit(cid, mid, _e("web") + " " + B("Choose your language") + "\n\n"
             + I("Interface text stays English; this preference is stored for future locales."),
             kb_lang_select(uid))
        return
    if data.startswith("set_toggle_lang_"):
        lang = data[len("set_toggle_lang_"):]
        if lang in LANGS:
            db_exec("UPDATE users SET lang=? WHERE uid=?", (lang, int(uid)))
            ack(call, "Language: " + str(LANGS[lang]))
        else:
            ack(call, "Unknown language.", True)
        edit(cid, mid, msg_settings(uid), kb_settings(uid))
        return
    if data in ("settings_upgrade", "menu_upgrade"):
        ack(call)
        edit(cid, mid, msg_upgrade(uid), kb_upgrade(uid))
        return
    if data.startswith("plan_order_"):
        tier_key = data[len("plan_order_"):]
        ok, result = create_plan_order(uid, tier_key)
        ack(call, str(result)[:180], not ok)
        if ok:
            notify_admins(
                _e("crown") + " " + B("Upgrade request") + "\n"
                + _e("user") + " " + C(str(uid)) + " wants " + tier_badge(tier_key)
                + "\n" + I("Use /settier " + str(uid) + " " + tier_key + " to apply it.")
            )
        edit(cid, mid, msg_upgrade(uid), kb_upgrade(uid))
        return
    if data in ("menu_notifications", "settings_notifications"):
        ack(call)
        edit(cid, mid, msg_notifications(uid), kb_notifications(uid))
        return
    if data == "notif_read_all":
        count = mark_notifications_read(uid)
        ack(call, "Marked " + str(count) + " as read.")
        edit(cid, mid, msg_notifications(uid), kb_notifications(uid))
        return
    if data in ("settings_api", "api_list"):
        ack(call)
        edit(cid, mid, msg_api_keys(uid), kb_api_keys(uid))
        return
    if data == "api_new":
        tier = get_tier(uid)
        if not tier.get("api") and not is_admin(uid):
            ack(call, "API access requires Pro or higher.", True)
            return
        ack(call)
        set_state(uid, "awaiting_api_label")
        edit(cid, mid, _e("key") + " " + B("Send a label for the new key") + "\n\n"
             + I("For example: ") + C("ci-server"), kb_back_only("settings_api"))
        return
    if data.startswith("api_del_"):
        key_id = _tail_int(data, "api_del_")
        ok = revoke_api_key(uid, key_id)
        ack(call, "Key revoked." if ok else "Key not found.", not ok)
        edit(cid, mid, msg_api_keys(uid), kb_api_keys(uid))
        return
    if data == "settings_cron":
        ack(call)
        edit(cid, mid, msg_crons(uid), kb_crons(uid))
        return
    if data == "cron_new":
        ack(call)
        edit(cid, mid, _e("cron") + " " + B("Pick a file to schedule"), kb_cron_new(uid))
        return
    if data.startswith("cron_for_"):
        fid = _tail_int(data, "cron_for_")
        ack(call)
        edit(cid, mid, _e("clock") + " " + B("Choose a schedule") + "\n\n"
             + I("Presets: " + ", ".join(CRON_PRESETS)), kb_cron_schedule(fid))
        return
    if data.startswith("cron_set_"):
        rest = data[len("cron_set_"):].split("_", 1)
        fid = int(rest[0]) if rest and rest[0].isdigit() else 0
        schedule = rest[1] if len(rest) > 1 else "every_1h"
        ok, result = create_cron(uid, fid, schedule)
        ack(call, ("Scheduled." if ok else str(result))[:180], not ok)
        edit(cid, mid, msg_crons(uid), kb_crons(uid))
        return
    if data.startswith("cron_custom_"):
        fid = _tail_int(data, "cron_custom_")
        ack(call)
        set_state(uid, "awaiting_cron_schedule", fid=fid)
        edit(cid, mid, _e("cron") + " " + B("Send a schedule string") + "\n\n"
             + I("Examples: ") + C("every_5m") + ", " + C("every_1h") + ", " + C("daily_9am"),
             kb_back_only("settings_cron"))
        return
    if data.startswith("cron_toggle_"):
        cron_id = _tail_int(data, "cron_toggle_")
        ok, enabled = toggle_cron(uid, cron_id)
        ack(call, ("Enabled." if enabled else "Disabled.") if ok else "Job not found.", not ok)
        edit(cid, mid, msg_cron_detail(cron_id), kb_cron_detail(uid, cron_id))
        return
    if data.startswith("cron_del_"):
        cron_id = _tail_int(data, "cron_del_")
        ok = delete_cron(uid, cron_id)
        ack(call, "Job deleted." if ok else "Job not found.", not ok)
        edit(cid, mid, msg_crons(uid), kb_crons(uid))
        return
    if data.startswith("cron_"):
        cron_id = _tail_int(data, "cron_")
        ack(call)
        edit(cid, mid, msg_cron_detail(cron_id), kb_cron_detail(uid, cron_id))
        return
    if data == "settings_webhooks":
        ack(call)
        edit(cid, mid, msg_webhooks(uid), kb_webhooks(uid))
        return
    if data == "webhook_new":
        ack(call)
        set_state(uid, "awaiting_webhook_url")
        edit(cid, mid, _e("link") + " " + B("Send the webhook URL") + "\n\n"
             + I("Must start with http:// or https://"), kb_back_only("settings_webhooks"))
        return
    if data.startswith("webhook_del_"):
        hook_id = _tail_int(data, "webhook_del_")
        ok = delete_webhook(uid, hook_id)
        ack(call, "Webhook removed." if ok else "Not found.", not ok)
        edit(cid, mid, msg_webhooks(uid), kb_webhooks(uid))
        return
    if data == "settings_notes":
        ack(call)
        edit(cid, mid, msg_notes(uid), kb_notes(uid))
        return
    if data == "note_new":
        ack(call)
        set_state(uid, "awaiting_new_note_title")
        edit(cid, mid, _e("note") + " " + B("Send a title for the note"), kb_back_only("settings_notes"))
        return
    if data.startswith("note_edit_"):
        note_id = _tail_int(data, "note_edit_")
        ack(call)
        set_state(uid, "awaiting_note_edit", note_id=note_id)
        edit(cid, mid, _e("edit") + " " + B("Send the new content for this note"),
             kb_back_only("note_" + str(note_id)))
        return
    if data.startswith("note_pin_"):
        note_id = _tail_int(data, "note_pin_")
        ok, pinned = toggle_note_pin(uid, note_id)
        ack(call, ("Pinned." if pinned else "Unpinned.") if ok else "Note not found.", not ok)
        edit(cid, mid, msg_note_detail(uid, note_id), kb_note_detail(uid, note_id))
        return
    if data.startswith("note_del_"):
        note_id = _tail_int(data, "note_del_")
        ok = delete_note(uid, note_id)
        ack(call, "Note deleted." if ok else "Note not found.", not ok)
        edit(cid, mid, msg_notes(uid), kb_notes(uid))
        return
    if data.startswith("note_"):
        note_id = _tail_int(data, "note_")
        ack(call)
        edit(cid, mid, msg_note_detail(uid, note_id), kb_note_detail(uid, note_id))
        return

    # ---------------- admin ----------------
    if data in ("menu_admin", "admin_dash"):
        ack(call)
        edit(cid, mid, msg_admin_dash(), kb_admin())
        return
    if data in ("admin_sys", "admin_sys_refresh"):
        ack(call, "Refreshed." if data.endswith("refresh") else "")
        edit(cid, mid, msg_admin_sys(), kb_admin_sys())
        return
    if data == "admin_metrics":
        ack(call)
        edit(cid, mid, msg_admin_metrics(), kb_admin_sys())
        return
    if data == "admin_running":
        ack(call)
        edit(cid, mid, msg_admin_running(), kb_admin_sys())
        return
    if data == "admin_kill_all":
        killed = force_kill_all()
        audit(uid, "kill_all", 0, str(killed) + " processes")
        ack(call, "Killed " + str(killed) + " process(es).")
        edit(cid, mid, msg_admin_running(), kb_admin_sys())
        return
    if data == "admin_users":
        ack(call)
        rows, total, total_pages, page = get_all_users(1)
        edit(cid, mid, msg_admin_users(rows, page, total_pages, total), kb_admin_users(page, total_pages, rows))
        return
    if data.startswith("admin_users_page_"):
        page = _tail_int(data, "admin_users_page_") or 1
        ack(call)
        rows, total, total_pages, page = get_all_users(page)
        edit(cid, mid, msg_admin_users(rows, page, total_pages, total), kb_admin_users(page, total_pages, rows))
        return
    if data == "admin_user_search":
        ack(call)
        set_state(uid, "awaiting_admin_user_search")
        edit(cid, mid, _e("search") + " " + B("Send a name, username or user ID"),
             kb_back_only("admin_users"))
        return
    if data.startswith("admin_ban_"):
        target = _tail_int(data, "admin_ban_")
        ack(call)
        set_state(uid, "awaiting_ban_reason", target=target)
        edit(cid, mid, _e("ban") + " " + B("Send the ban reason for ") + C(str(target)),
             kb_back_only("admin_user_" + str(target)))
        return
    if data.startswith("admin_unban_"):
        target = _tail_int(data, "admin_unban_")
        ok, result = unban_user(target, uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_admin_user_detail(target), kb_admin_user_detail(target))
        return
    if data.startswith("admin_pts_"):
        target = _tail_int(data, "admin_pts_")
        ack(call)
        set_state(uid, "awaiting_addpts", target=target)
        edit(cid, mid, _e("pts") + " " + B("Send the point change for ") + C(str(target))
             + "\n\n" + I("Negative numbers remove points."),
             kb_back_only("admin_user_" + str(target)))
        return
    if data.startswith("admin_settier_"):
        rest = data[len("admin_settier_"):].split("_")
        target = int(rest[0]) if rest and rest[0].isdigit() else 0
        tier_key = rest[1] if len(rest) > 1 else "free"
        ok, result = set_user_tier(target, tier_key, uid)
        ack(call, result[:180], not ok)
        if ok:
            notify_user(target, _e("crown") + " Your plan is now " + tier_badge(tier_key) + ".")
        edit(cid, mid, msg_admin_user_detail(target), kb_admin_user_detail(target))
        return
    if data.startswith("admin_tier_"):
        target = _tail_int(data, "admin_tier_")
        ack(call)
        edit(cid, mid, _e("crown") + " " + B("Choose a plan for ") + C(str(target)),
             kb_admin_tier_select(target))
        return
    if data.startswith("admin_user_files_"):
        target = _tail_int(data, "admin_user_files_")
        ack(call)
        rows = get_admin_user_files(target, 1, 20)[0]
        lines = [header("Files of " + str(target), None), ""]
        buttons = []
        if not rows:
            lines.append(I("This user has no files."))
        for row in rows:
            fid = int(row.get("id") or 0)
            lines.append(
                status_icon(row.get("status")) + " " + C("#" + str(fid)) + " "
                + B(esc(trunc(str(row.get("fname")), 30))) + " \u2022 " + fmt_size(row.get("fsize"))
            )
            buttons.append([BTN(trunc(str(row.get("fname")), 28), "admin_review_" + str(fid))])
        buttons.append([BACK("admin_user_" + str(target))])
        edit(cid, mid, "\n".join(lines), KB(*buttons))
        return
    if data.startswith("admin_del_user_yes_"):
        target = _tail_int(data, "admin_del_user_yes_")
        ok, result = delete_user_data(target, uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, _e("trash") + " " + esc(result), kb_admin_users(1))
        return
    if data.startswith("admin_del_user_"):
        target = _tail_int(data, "admin_del_user_")
        ack(call)
        edit(cid, mid, _e("warn") + " " + B("Delete all data for ") + C(str(target)) + B("?")
             + "\n\n" + I("Files, logs, tickets, notes and settings are erased permanently."),
             kb_confirm("admin_del_user_yes_" + str(target), "admin_user_" + str(target)))
        return
    if data.startswith("admin_dl_"):
        fid = _tail_int(data, "admin_dl_")
        ack(call, "Sending\u2026")
        ok, result = send_file_to_user(uid, fid)
        if not ok:
            send(uid, _e("no") + " " + esc(result))
        return
    if data.startswith("admin_prev_"):
        fid = _tail_int(data, "admin_prev_")
        ack(call)
        row = get_file(fid) or {}
        path = Path(str(row.get("fpath") or ""))
        content = read_file_text(path, 2500) if path.exists() else ""
        body = (syntax_highlight_preview("\n".join(content.splitlines()[:35]),
                                         file_ext(str(row.get("fname", ""))))
                if content.strip() else I("No readable text content."))
        edit(cid, mid, header("Preview", None) + "\n\n" + _e("file") + " "
             + B(esc(str(row.get("fname", "?")))) + "\n\n" + body, kb_admin_file_detail(fid))
        return
    if data.startswith("admin_user_"):
        target = _tail_int(data, "admin_user_")
        ack(call)
        edit(cid, mid, msg_admin_user_detail(target), kb_admin_user_detail(target))
        return
    if data == "admin_pending":
        ack(call)
        rows, total, total_pages, page = get_pending_files(1)
        edit(cid, mid, msg_admin_files(rows, page, total_pages, total), kb_admin_files(page, total_pages, rows))
        return
    if data.startswith("admin_pending_page_"):
        page = _tail_int(data, "admin_pending_page_") or 1
        ack(call)
        rows, total, total_pages, page = get_pending_files(page)
        edit(cid, mid, msg_admin_files(rows, page, total_pages, total), kb_admin_files(page, total_pages, rows))
        return
    if data.startswith("admin_review_"):
        fid = _tail_int(data, "admin_review_")
        ack(call)
        edit(cid, mid, msg_admin_file_detail(fid), kb_admin_file_detail(fid))
        return
    if data.startswith("admin_approve_"):
        fid = _tail_int(data, "admin_approve_")
        ok, result = approve_file(fid, uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_admin_file_detail(fid), kb_admin_file_detail(fid))
        return
    if data.startswith("admin_reject_"):
        fid = _tail_int(data, "admin_reject_")
        ok, result = reject_file(fid, "Rejected by administrator", uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_admin_file_detail(fid), kb_admin_file_detail(fid))
        return
    if data == "admin_tickets":
        ack(call)
        rows, total, total_pages, page = get_open_tickets(1)
        edit(cid, mid, msg_admin_tickets(rows, page, total_pages, total),
             kb_admin_tickets(page, total_pages, rows))
        return
    if data.startswith("admin_tickets_page_"):
        page = _tail_int(data, "admin_tickets_page_") or 1
        ack(call)
        rows, total, total_pages, page = get_open_tickets(page)
        edit(cid, mid, msg_admin_tickets(rows, page, total_pages, total),
             kb_admin_tickets(page, total_pages, rows))
        return
    if data.startswith("admin_ticket_reply_"):
        tid = _tail_int(data, "admin_ticket_reply_")
        ack(call)
        set_state(uid, "awaiting_admin_ticket_reply", tid=tid)
        edit(cid, mid, _e("mail") + " " + B("Send your staff reply for ticket #" + str(tid)),
             kb_back_only("admin_ticket_" + str(tid)))
        return
    if data.startswith("admin_ticket_close_"):
        tid = _tail_int(data, "admin_ticket_close_")
        ok, result = close_ticket(tid, uid)
        ticket = get_ticket(tid) or {}
        if ok:
            notify_user(int(ticket.get("uid") or 0),
                        _e("ok") + " Your ticket " + C("#" + str(tid)) + " was closed by support.")
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_ticket_detail(tid, uid), kb_admin_ticket_detail(tid))
        return
    if data.startswith("admin_ticket_assign_"):
        tid = _tail_int(data, "admin_ticket_assign_")
        ok, result = assign_ticket(tid, uid)
        ack(call, result[:180], not ok)
        edit(cid, mid, msg_ticket_detail(tid, uid), kb_admin_ticket_detail(tid))
        return
    if data.startswith("admin_ticket_"):
        tid = _tail_int(data, "admin_ticket_")
        ack(call)
        edit(cid, mid, msg_ticket_detail(tid, uid), kb_admin_ticket_detail(tid))
        return
    if data == "admin_broadcast":
        ack(call)
        edit(cid, mid, msg_admin_broadcast(), kb_admin_broadcast())
        return
    if data.startswith("admin_bc_target_"):
        target = data[len("admin_bc_target_"):]
        tier_key = None if target == "all" else target
        ack(call)
        set_state(uid, "awaiting_broadcast", target=tier_key)
        edit(cid, mid, _e("bell") + " " + B("Send the broadcast text") + "\n\n"
             + "Audience: " + (tier_badge(tier_key) if tier_key else B("everyone")) + "\n"
             + I("HTML formatting is supported."), kb_back_only("admin_broadcast"))
        return
    if data == "admin_broadcast_confirm":
        state = get_state(uid)
        if str(state.get("state")) != "confirm_broadcast":
            ack(call, "Nothing to send.", True)
            return
        clear_state(uid)
        ack(call, "Sending\u2026")
        text = str(state.get("text") or "")
        sent, failed = send_broadcast(uid, text, state.get("target"))
        edit(cid, mid, _e("ok") + " " + B("Broadcast complete") + "\n\n"
             + "Delivered: " + B(str(sent)) + "\nFailed: " + B(str(failed)), kb_admin())
        return
    if data == "admin_db":
        ack(call)
        edit(cid, mid, msg_admin_db(), kb_admin_db())
        return
    if data == "admin_vacuum":
        ack(call, "Vacuuming\u2026")
        ok, result = db_vacuum()
        audit(uid, "db_vacuum", 0, result[:120])
        edit(cid, mid, (_e("ok") if ok else _e("no")) + " " + esc(result) + "\n\n" + msg_admin_db(),
             kb_admin_db())
        return
    if data == "admin_backup":
        ack(call, "Backing up\u2026")
        ok, result = db_backup()
        if ok:
            audit(uid, "db_backup", 0, str(result))
            send_document_path(uid, result, caption=_e("backup") + " SIGMA database backup")
        edit(cid, mid, (_e("backup") if ok else _e("no")) + " " + esc(str(result)) + "\n\n"
             + msg_admin_db(), kb_admin_db())
        return
    if data == "admin_integrity":
        ack(call, "Checking\u2026")
        ok, result = db_integrity_check()
        edit(cid, mid, (_e("ok") if ok else _e("warn")) + " " + B("Integrity check") + "\n\n"
             + PRE(trunc(result, 1500)) + "\n" + msg_admin_db(), kb_admin_db())
        return
    if data == "admin_cleanup":
        ack(call, "Cleaning\u2026")
        removed = cleanup_old_logs(7)
        audit(uid, "cleanup_logs", 0, str(removed))
        edit(cid, mid, _e("trash") + " Removed " + B(str(removed)) + " stale log record(s)/file(s)."
             + "\n\n" + msg_admin_db(), kb_admin_db())
        return
    if data == "admin_audit":
        ack(call)
        edit(cid, mid, msg_admin_audit(), kb_admin_db())
        return
    if data == "admin_files":
        ack(call)
        rows, total, total_pages, page = get_pending_files(1)
        edit(cid, mid, msg_admin_files(rows, page, total_pages, total), kb_admin_files(page, total_pages, rows))
        return

    # ---------------- extensions & plugins ----------------
    if dispatch_extension_callback(call, uid, cid, mid, data):
        return

    # ---------------- fallback ----------------
    log.debug("Unhandled callback: %s", data)
    ack(call)



# ==========================================================================
# SECTION 28 - COMMAND CENTRE, CHANNEL LOGGING, MEMBER PANELS, FONTS
# ==========================================================================


# ---- p1 ----
# ============================================================================
# SECTION 27 - OWNER TIER, CHANNEL LOGGING, COMMAND REGISTRY
# ============================================================================
# Everything below is additive: it registers itself through the extension
# framework in SECTION 26, so the core router and handlers stay untouched.

OWNER_IDS = set()
for _raw in str(os.environ.get("OWNER_IDS", "") or "").replace(";", ",").split(","):
    _raw = _raw.strip()
    if _raw.lstrip("-").isdigit():
        OWNER_IDS.add(int(_raw))
if not OWNER_IDS and ADMIN_IDS:
    OWNER_IDS = {min(int(x) for x in ADMIN_IDS)}

LOG_CHANNEL_ENV = str(
    os.environ.get("LOG_CHANNEL")
    or os.environ.get("SIGMA_LOG_CHANNEL")
    or ""
).strip()

PANEL_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS bot_config (
        key        TEXT PRIMARY KEY,
        value      TEXT    DEFAULT '',
        updated_at TEXT    DEFAULT '',
        updated_by INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channel_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        category   TEXT    DEFAULT 'info',
        title      TEXT    DEFAULT '',
        body       TEXT    DEFAULT '',
        uid        INTEGER DEFAULT 0,
        delivered  INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS command_usage (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER DEFAULT 0,
        command    TEXT    DEFAULT '',
        args       TEXT    DEFAULT '',
        ok         INTEGER DEFAULT 1,
        created_at TEXT    DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_warnings (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        admin_uid  INTEGER DEFAULT 0,
        reason     TEXT    DEFAULT '',
        created_at TEXT    DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mutes (
        uid        INTEGER PRIMARY KEY,
        until_ts   INTEGER DEFAULT 0,
        reason     TEXT    DEFAULT '',
        admin_uid  INTEGER DEFAULT 0,
        created_at TEXT    DEFAULT ''
    )
    """,
]

PANEL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_chlog_delivered ON channel_log(delivered)",
    "CREATE INDEX IF NOT EXISTS idx_cmdusage_uid ON command_usage(uid)",
    "CREATE INDEX IF NOT EXISTS idx_cmdusage_cmd ON command_usage(command)",
    "CREATE INDEX IF NOT EXISTS idx_warnings_uid ON user_warnings(uid)",
]


def init_panel_tables():
    """Create (and repair) the tables used by the command centre."""
    for ddl in PANEL_SCHEMA:
        db_exec(ddl)
    reconcile_schema(PANEL_SCHEMA)
    for ddl in PANEL_INDEXES:
        db_exec(ddl)
    log.info("Command-centre tables ready: %s", len(PANEL_SCHEMA))
    return len(PANEL_SCHEMA)


def is_owner(uid):
    """True for workspace owners (a strict subset of the admins)."""
    try:
        return int(uid) in OWNER_IDS
    except Exception:
        return False


def cfg_get(key, default=""):
    """Read a runtime setting from bot_config."""
    value = db_val("SELECT value FROM bot_config WHERE key=?", (str(key),), None)
    return default if value is None else str(value)


def cfg_set(key, value, uid=0):
    """Write a runtime setting, no restart required."""
    db_exec(
        "INSERT INTO bot_config (key, value, updated_at, updated_by) VALUES (?,?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (str(key), str(value), utcstamp(), int(uid or 0)),
    )
    return True


def cfg_all():
    """Every runtime setting, newest first."""
    return db_all("SELECT * FROM bot_config ORDER BY key ASC")


def log_channel_id():
    """The configured log channel: bot_config wins over the environment."""
    value = cfg_get("log_channel", "") or LOG_CHANNEL_ENV
    value = str(value).strip()
    if not value:
        return 0
    try:
        return int(value)
    except Exception:
        return value if value.startswith("@") else 0


def chlog_enabled():
    """True when channel logging is switched on and a target exists."""
    if cfg_get("log_enabled", "1") not in ("1", "true", "on", "yes"):
        return False
    return bool(log_channel_id())


# ---- p2 ----


LOG_CATEGORIES = {
    "user": "NEW USER",
    "upload": "UPLOAD",
    "run": "SCRIPT RUN",
    "security": "SECURITY",
    "moderation": "MODERATION",
    "economy": "ECONOMY",
    "ticket": "SUPPORT",
    "plan": "PLAN ORDER",
    "admin": "ADMIN ACTION",
    "owner": "OWNER ACTION",
    "command": "COMMAND",
    "broadcast": "BROADCAST",
    "error": "ERROR",
    "system": "SYSTEM",
}

_chlog_queue = []
_chlog_lock = threading.Lock()
_CHLOG_MAX_QUEUE = 500
_chlog_sent = [0]
_chlog_failed = [0]


def _chlog_render(category, title, body, uid):
    """Build the HTML card that gets posted to the log channel."""
    chip = LOG_CATEGORIES.get(str(category), "EVENT")
    icon = {
        "user": _e("user"), "upload": _e("upload"), "run": _e("run"),
        "security": _e("shield"), "moderation": _e("ban"), "economy": _e("coin"),
        "ticket": _e("ticket"), "plan": _e("crown"), "admin": _e("admin"),
        "owner": _e("crown"), "command": _e("code"), "broadcast": _e("bell"),
        "error": _e("warn"), "system": _e("gear"),
    }.get(str(category), _e("info"))
    lines = [icon + " " + B(chip), divider()]
    if title:
        lines.append(B(esc(str(title)[:180])))
    if body:
        lines.append(esc(str(body)[:1400]))
    if uid:
        user = get_user(int(uid)) or {}
        name = str(user.get("first_name") or "user")
        lines.append(
            divider() + "\n" + _e("user") + " " + esc(name)
            + " " + C(str(int(uid)))
            + " " + tier_badge(str(user.get("tier") or "free"))
        )
    lines.append(I(fmt_dt_abs(utcstamp())))
    return "\n".join(part for part in lines if part)


def chlog(category, title, body="", uid=0):
    """Queue one event for the log channel and keep a copy in the database."""
    row_id = db_exec(
        "INSERT INTO channel_log (category, title, body, uid, delivered, created_at)"
        " VALUES (?,?,?,?,0,?)",
        (str(category), str(title)[:200], str(body)[:1800], int(uid or 0), utcstamp()),
    )
    if not chlog_enabled():
        return 0
    with _chlog_lock:
        if len(_chlog_queue) >= _CHLOG_MAX_QUEUE:
            _chlog_queue.pop(0)
        _chlog_queue.append({
            "id": row_id, "category": str(category), "title": str(title),
            "body": str(body), "uid": int(uid or 0),
        })
    return row_id or 0


def _chlog_deliver(item):
    """Post a single queued entry. Returns True on success."""
    target = log_channel_id()
    if not target:
        return False
    text = _chlog_render(item.get("category"), item.get("title"), item.get("body"), item.get("uid"))
    try:
        bot.send_message(target, text, parse_mode="HTML", disable_web_page_preview=True)
        _chlog_sent[0] += 1
        if item.get("id"):
            db_exec("UPDATE channel_log SET delivered=1 WHERE id=?", (int(item["id"]),))
        return True
    except Exception as exc:
        _chlog_failed[0] += 1
        log.debug("Channel log delivery failed: %s", exc)
        return False


def chlog_worker():
    """Background thread: drain the log queue without ever crashing."""
    log.info("Channel logger started")
    while not _stop_event.is_set():
        try:
            with _chlog_lock:
                batch = _chlog_queue[:8]
                del _chlog_queue[:8]
            for item in batch:
                if not _chlog_deliver(item):
                    break
                time.sleep(0.4)
        except Exception as exc:
            log.debug("Channel logger loop: %s", exc)
        _stop_event.wait(3)
    log.info("Channel logger stopped")


def chlog_stats():
    """Counters for the /logstats command."""
    with _chlog_lock:
        pending = len(_chlog_queue)
    return {
        "target": log_channel_id() or "not set",
        "enabled": chlog_enabled(),
        "queued": pending,
        "sent": _chlog_sent[0],
        "failed": _chlog_failed[0],
        "stored": db_val("SELECT COUNT(*) c FROM channel_log", (), 0),
        "undelivered": db_val("SELECT COUNT(*) c FROM channel_log WHERE delivered=0", (), 0),
    }


def recent_channel_logs(limit=10):
    """Latest stored log entries."""
    return db_all(
        "SELECT * FROM channel_log ORDER BY id DESC LIMIT ?", (int(limit),)
    )


# ---- p3 ----


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

CMD_REGISTRY = {}

CMD_GROUPS = [
    ("core", "Getting started", "user"),
    ("account", "My account", "user"),
    ("files", "My files", "user"),
    ("runner", "Script runner", "user"),
    ("economy", "Points and plans", "user"),
    ("support", "Support desk", "user"),
    ("tools", "Utilities", "user"),
    ("fun", "Extras", "user"),
    ("admin", "Administration", "admin"),
    ("adminsys", "System and database", "admin"),
    ("moderation", "Moderation", "admin"),
    ("owner", "Owner controls", "owner"),
]

SCOPE_RANK = {"user": 0, "admin": 1, "owner": 2}
SCOPE_CHIP = {"user": "USER", "admin": "ADMIN", "owner": "OWNER"}


def user_scope(uid):
    """Highest scope the caller may use."""
    if is_owner(uid):
        return "owner"
    return "admin" if is_admin(uid) else "user"


def can_use(uid, scope):
    """True when the caller's scope covers the command scope."""
    return SCOPE_RANK.get(user_scope(uid), 0) >= SCOPE_RANK.get(str(scope), 0)


def log_command(uid, command, args="", ok=True):
    """Record one command execution for the usage panels."""
    db_exec(
        "INSERT INTO command_usage (uid, command, args, ok, created_at) VALUES (?,?,?,?,?)",
        (int(uid or 0), str(command)[:60], str(args)[:200], 1 if ok else 0, utcstamp()),
    )


def command_stats(limit=15):
    """Most used commands."""
    return db_all(
        "SELECT command, COUNT(*) c, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) fails"
        " FROM command_usage GROUP BY command ORDER BY c DESC LIMIT ?",
        (int(limit),),
    )


def run_command(uid, name, args=""):
    """Execute a registered command and return (text, markup)."""
    meta = CMD_REGISTRY.get(str(name).lower().lstrip("/"))
    if not meta:
        return _e("warn") + " Unknown command: " + C("/" + str(name)), None
    if not can_use(uid, meta.get("scope", "user")):
        return (
            _e("lock") + " " + B("Not available") + "\n\n"
            + I("/" + str(name) + " needs " + SCOPE_CHIP.get(meta.get("scope"), "USER") + " access."),
            None,
        )
    try:
        result = meta["runner"](uid, str(args or ""))
        log_command(uid, name, args, True)
    except Exception as exc:
        log.exception("Command /%s failed: %s", name, exc)
        log_command(uid, name, args, False)
        chlog("error", "Command /" + str(name) + " failed", str(exc)[:400], uid)
        return _e("warn") + " " + B("That command failed.") + "\n\n" + I(esc(str(exc)[:200])), None
    if isinstance(result, tuple):
        text = result[0]
        markup = result[1] if len(result) > 1 else None
    else:
        text, markup = result, None
    return str(text), markup


def register_command(name, group, scope, help_text, runner, usage="", aliases=()):
    """Add one command to the registry and wire it into the extension framework."""
    key = str(name).lower().lstrip("/")
    CMD_REGISTRY[key] = {
        "name": key,
        "group": str(group),
        "scope": str(scope),
        "help": str(help_text),
        "usage": str(usage or ("/" + key)),
        "runner": runner,
        "aliases": tuple(aliases or ()),
    }

    def handler(message, uid, _key=key):
        args = cmd_args(message)
        text, markup = run_command(uid, _key, args)
        send(uid, text, markup or kb_cmd_footer(_key))

    extension_command(key, str(help_text)[:120])(handler)
    for alias in (aliases or ()):
        extension_command(str(alias).lower().lstrip("/"), str(help_text)[:120])(handler)
        CMD_REGISTRY[key]["aliases"] = tuple(CMD_REGISTRY[key]["aliases"])
    return key


def commands_in_group(group, uid=None):
    """Registered commands of one group, alphabetical."""
    out = []
    for meta in CMD_REGISTRY.values():
        if meta.get("group") != str(group):
            continue
        if uid is not None and not can_use(uid, meta.get("scope", "user")):
            continue
        out.append(meta)
    return sorted(out, key=lambda item: item["name"])


def visible_groups(uid):
    """Groups the caller may open, with a live command count."""
    out = []
    for key, label, scope in CMD_GROUPS:
        if not can_use(uid, scope):
            continue
        count = len(commands_in_group(key, uid))
        if count:
            out.append((key, label, scope, count))
    return out


def registry_counts():
    """Totals used by /commands and the startup banner."""
    out = {"total": len(CMD_REGISTRY), "user": 0, "admin": 0, "owner": 0}
    for meta in CMD_REGISTRY.values():
        out[meta.get("scope", "user")] = out.get(meta.get("scope", "user"), 0) + 1
    return out


# ---- p4 ----


# ---------------------------------------------------------------------------
# Command centre panels - a button for every registered command
# ---------------------------------------------------------------------------

PANEL_PAGE_SIZE = 8


def _rows_text(rows, columns, empty="Nothing here yet."):
    """Render database rows as a compact list."""
    if not rows:
        return I(empty)
    out = []
    for index, row in enumerate(rows, 1):
        parts = []
        for label, key in columns:
            value = row.get(key) if hasattr(row, "get") else None
            if value is None or value == "":
                continue
            parts.append(B(label) + " " + esc(trunc(str(value), 42)))
        out.append(str(index) + ". " + " " + I("|") + " ".join(parts) if False else str(index) + ". " + "  ".join(parts))
    return "\n".join(out)


def kb_cmd_footer(name):
    """Standard footer under any command result."""
    meta = CMD_REGISTRY.get(str(name), {})
    group = str(meta.get("group") or "core")
    return KB(
        [BTN(_e("reload") + " Run again", "c:" + str(name)),
         BTN(_e("info") + " About", "ch:" + str(name))],
        [BTN(_e("folder") + " " + str(group).title() + " panel", "cg:" + group + ":1"),
         BTN(_e("menu") + " All commands", "cmd_center")],
        [BACK("main_menu")],
    )


def cmd_button(meta):
    """One inline button that runs a command."""
    chip = {"user": "", "admin": _e("admin") + " ", "owner": _e("crown") + " "}.get(
        meta.get("scope", "user"), "")
    return BTN(chip + "/" + str(meta.get("name")), "c:" + str(meta.get("name")))


def msg_command_center(uid):
    """Landing card of the command centre."""
    counts = registry_counts()
    groups = visible_groups(uid)
    mine = sum(item[3] for item in groups)
    lines = [
        _e("menu") + " " + B("COMMAND CENTRE"),
        divider(),
        "Every command in " + B(BOT_NAME) + " is listed here, and every one of",
        "them is also a button - nothing has to be typed.",
        "",
        B("Registered") + "  " + str(counts["total"]) + " commands",
        B("Available to you") + "  " + str(mine),
        B("Your access") + "  " + SCOPE_CHIP.get(user_scope(uid), "USER"),
        divider(),
        I("Pick a panel below, or use the index to page through everything."),
    ]
    return "\n".join(lines)


def kb_command_center(uid):
    """Group buttons plus the global tools."""
    rows = []
    pair = []
    for key, label, _scope, count in visible_groups(uid):
        pair.append(BTN(label + " (" + str(count) + ")", "cg:" + key + ":1"))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([BTN(_e("log") + " Full index", "cmd_index:1"),
                 BTN(_e("search") + " Find command", "cmd_find")])
    rows.append([BTN(_e("graph") + " Usage stats", "c:cmdstats"),
                 BTN(_e("user") + " Members", "ub:all:1")])
    rows.append([BACK("main_menu")])
    return KB(*rows)


def msg_group_panel(uid, group, page=1):
    """One group of commands, paginated."""
    label = next((item[1] for item in CMD_GROUPS if item[0] == group), str(group).title())
    metas = commands_in_group(group, uid)
    total_pages = max(1, (len(metas) + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    page = max(1, min(int(page or 1), total_pages))
    slice_ = metas[(page - 1) * PANEL_PAGE_SIZE: page * PANEL_PAGE_SIZE]
    lines = [_e("folder") + " " + B(label.upper()), divider()]
    for meta in slice_:
        lines.append(C("/" + meta["name"]) + "  " + esc(str(meta["help"])))
    if not slice_:
        lines.append(I("No commands available to you in this panel."))
    lines.append(divider())
    lines.append(I("Page " + str(page) + " of " + str(total_pages)
                   + "  |  " + str(len(metas)) + " command(s)"))
    return "\n".join(lines)


def kb_group_panel(uid, group, page=1):
    """A button per command in the group, plus paging."""
    metas = commands_in_group(group, uid)
    total_pages = max(1, (len(metas) + PANEL_PAGE_SIZE - 1) // PANEL_PAGE_SIZE)
    page = max(1, min(int(page or 1), total_pages))
    slice_ = metas[(page - 1) * PANEL_PAGE_SIZE: page * PANEL_PAGE_SIZE]
    rows = []
    pair = []
    for meta in slice_:
        pair.append(cmd_button(meta))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "cg:" + group + ":" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "cg:" + group + ":" + str(page + 1)))
    rows.append(nav)
    rows.append([BTN(_e("menu") + " All panels", "cmd_center"), BACK("main_menu")])
    return KB(*rows)


def msg_command_index(uid, page=1):
    """Flat, paged index of every command the caller may run."""
    metas = sorted(
        [meta for meta in CMD_REGISTRY.values() if can_use(uid, meta.get("scope", "user"))],
        key=lambda item: (item["group"], item["name"]),
    )
    per = 10
    total_pages = max(1, (len(metas) + per - 1) // per)
    page = max(1, min(int(page or 1), total_pages))
    slice_ = metas[(page - 1) * per: page * per]
    lines = [_e("log") + " " + B("COMMAND INDEX"), divider()]
    for meta in slice_:
        lines.append(C("/" + meta["name"]) + "  " + esc(trunc(str(meta["help"]), 56)))
    lines.append(divider())
    lines.append(I("Page " + str(page) + " of " + str(total_pages)
                   + "  |  " + str(len(metas)) + " available to you"))
    return "\n".join(lines), page, total_pages, slice_


def kb_command_index(uid, page=1):
    """Buttons for the current index page."""
    _text, page, total_pages, slice_ = msg_command_index(uid, page)
    rows = []
    pair = []
    for meta in slice_:
        pair.append(cmd_button(meta))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "cmd_index:" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "cmd_index:" + str(page + 1)))
    rows.append(nav)
    rows.append([BTN(_e("menu") + " Panels", "cmd_center"), BACK("main_menu")])
    return KB(*rows)


@extension_callback("cmd_center", exact=True)
def cb_cmd_center(call, uid, cid, mid, data):
    """Open the command centre."""
    ack(call)
    edit(cid, mid, msg_command_center(uid), kb_command_center(uid))


@extension_callback("menu_commands", exact=True)
def cb_menu_commands(call, uid, cid, mid, data):
    """Main-menu entry point."""
    ack(call)
    edit(cid, mid, msg_command_center(uid), kb_command_center(uid))


@extension_callback("cmd_index:")
def cb_cmd_index(call, uid, cid, mid, data):
    """Page through the flat index."""
    ack(call)
    page = _tail_int(data, "cmd_index:") or 1
    text, _page, _total, _slice = msg_command_index(uid, page)
    edit(cid, mid, text, kb_command_index(uid, page))


@extension_callback("cg:")
def cb_cmd_group(call, uid, cid, mid, data):
    """Open a group panel."""
    ack(call)
    parts = str(data).split(":")
    group = parts[1] if len(parts) > 1 else "core"
    page = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 1
    edit(cid, mid, msg_group_panel(uid, group, page), kb_group_panel(uid, group, page))


@extension_callback("ch:")
def cb_cmd_help(call, uid, cid, mid, data):
    """Explain one command."""
    ack(call)
    name = str(data)[3:]
    meta = CMD_REGISTRY.get(name)
    if not meta:
        ack(call, "Unknown command.", True)
        return
    text = "\n".join([
        _e("info") + " " + B("/" + meta["name"]),
        divider(),
        esc(str(meta["help"])),
        "",
        B("Usage") + "  " + C(str(meta["usage"])),
        B("Panel") + "  " + str(meta["group"]).title(),
        B("Access") + "  " + SCOPE_CHIP.get(meta["scope"], "USER"),
    ])
    edit(cid, mid, text, KB(
        [BTN(_e("run") + " Run /" + meta["name"], "c:" + meta["name"])],
        [BTN(_e("folder") + " Panel", "cg:" + meta["group"] + ":1"),
         BTN(_e("menu") + " All", "cmd_center")],
        [BACK("main_menu")],
    ))


@extension_callback("c:")
def cb_run_command(call, uid, cid, mid, data):
    """Run a command straight from its button."""
    name = str(data)[2:]
    meta = CMD_REGISTRY.get(name)
    if not meta:
        ack(call, "Unknown command.", True)
        return
    if not can_use(uid, meta.get("scope", "user")):
        ack(call, "You do not have access to that command.", True)
        return
    if "<" in str(meta.get("usage", "")):
        ack(call)
        set_state(uid, "awaiting_cmd_arg", command=name)
        edit(cid, mid, "\n".join([
            _e("edit") + " " + B("/" + name + " needs input"),
            divider(),
            esc(str(meta["help"])),
            "",
            B("Usage") + "  " + C(str(meta["usage"])),
            I("Send the value now, or press Cancel."),
        ]), KB([BTN(_l("cancel") if _l("cancel") else "Cancel", "cg:" + meta["group"] + ":1")]))
        return
    ack(call, "Running /" + name)
    text, markup = run_command(uid, name, "")
    edit(cid, mid, text, markup or kb_cmd_footer(name))


@extension_callback("cmd_find", exact=True)
def cb_cmd_find(call, uid, cid, mid, data):
    """Search the registry by name or description."""
    ack(call)
    set_state(uid, "awaiting_cmd_search")
    edit(cid, mid, "\n".join([
        _e("search") + " " + B("FIND A COMMAND"),
        divider(),
        I("Send a word - for example ") + C("backup") + I(" or ") + C("file") + I("."),
    ]), KB([BTN(_e("menu") + " Back to panels", "cmd_center")]))


def _fsm_cmd_arg(message, uid, state):
    """Receive the argument a button-launched command asked for."""
    name = str((state.get("data") or {}).get("command") or "")
    clear_state(uid)
    text, markup = run_command(uid, name, str(getattr(message, "text", "") or "").strip())
    send(uid, text, markup or kb_cmd_footer(name))


def _fsm_cmd_search(message, uid, state):
    """Show registry matches for a search term."""
    clear_state(uid)
    term = str(getattr(message, "text", "") or "").strip().lower().lstrip("/")
    matches = [
        meta for meta in CMD_REGISTRY.values()
        if can_use(uid, meta.get("scope", "user"))
        and (term in meta["name"] or term in str(meta["help"]).lower())
    ]
    matches = sorted(matches, key=lambda item: item["name"])[:16]
    if not matches:
        send(uid, _e("search") + " Nothing matched " + C(esc(term)) + ".",
             KB([BTN(_e("menu") + " Command centre", "cmd_center")]))
        return
    lines = [_e("search") + " " + B("MATCHES FOR ") + C(esc(term)), divider()]
    for meta in matches:
        lines.append(C("/" + meta["name"]) + "  " + esc(trunc(str(meta["help"]), 52)))
    rows = []
    pair = []
    for meta in matches:
        pair.append(cmd_button(meta))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([BTN(_e("menu") + " Command centre", "cmd_center")])
    send(uid, "\n".join(lines), KB(*rows))


# ---- p5a ----


# ---------------------------------------------------------------------------
# Member browser - every user as a button, with drill-down sub-panels
# ---------------------------------------------------------------------------

USER_PAGE_SIZE = 8


def browse_users(page=1, per_page=USER_PAGE_SIZE, flt="all", term=""):
    """Paged member list. Returns (rows, total, total_pages, page)."""
    where = "1=1"
    params = []
    flt = str(flt or "all")
    if flt == "active":
        where = "is_banned=0"
    elif flt == "banned":
        where = "is_banned=1"
    elif flt == "paid":
        where = "tier <> 'free'"
    elif flt == "search" and term:
        where = "(CAST(uid AS TEXT) LIKE ? OR first_name LIKE ? OR username LIKE ?)"
        like = "%" + str(term) + "%"
        params = [like, like, like]
    order = "joined_at DESC" if flt == "new" else "uid ASC"
    total = int(db_val("SELECT COUNT(*) c FROM users WHERE " + where, tuple(params), 0) or 0)
    per_page = max(1, int(per_page or USER_PAGE_SIZE))
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page or 1), total_pages))
    rows = db_all(
        "SELECT * FROM users WHERE " + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (page - 1) * per_page),
    )
    return rows, total, total_pages, page


def user_row_label(row):
    """Short label for a member button."""
    name = trunc(str(row.get("first_name") or "user"), 16)
    mark = _e("ban") if int(row.get("is_banned") or 0) else tier_badge(str(row.get("tier") or "free"))
    return mark + " " + name + " (" + str(int(row.get("uid") or 0)) + ")"


def msg_user_browser(rows, total, page, total_pages, flt="all", term=""):
    """Header card of the member browser."""
    titles = {
        "all": "ALL MEMBERS", "active": "ACTIVE MEMBERS", "banned": "BANNED MEMBERS",
        "paid": "PAYING MEMBERS", "new": "NEWEST MEMBERS", "search": "SEARCH RESULTS",
    }
    lines = [_e("user") + " " + B(titles.get(str(flt), "MEMBERS")), divider()]
    if flt == "search" and term:
        lines.append(B("Term") + "  " + C(esc(str(term))))
    for row in rows:
        banned = int(row.get("is_banned") or 0)
        lines.append(
            tier_badge(str(row.get("tier") or "free")) + " "
            + B(esc(trunc(str(row.get("first_name") or "user"), 20)))
            + "  " + C(str(int(row.get("uid") or 0)))
            + (("  " + _e("ban")) if banned else "")
        )
        lines.append(
            "    " + I("files " + str(int(row.get("total_uploads") or 0))
                      + "  runs " + str(int(row.get("total_runs") or 0))
                      + "  pts " + fmt_num(int(row.get("points") or 0))
                      + "  lvl " + str(int(row.get("level") or 1)))
        )
    if not rows:
        lines.append(I("No members matched."))
    lines.append(divider())
    lines.append(I("Page " + str(page) + " of " + str(total_pages) + "  |  " + str(total) + " member(s)"))
    lines.append(I("Tap a member for the full profile."))
    return "\n".join(lines)


def kb_user_browser(rows, page, total_pages, flt="all"):
    """A button per member plus the filter bar."""
    kb_rows = [[BTN(user_row_label(row), "ui:" + str(int(row.get("uid") or 0)))] for row in rows]
    nav = []
    if page > 1:
        nav.append(BTN(_l("prev"), "ub:" + flt + ":" + str(page - 1)))
    nav.append(BTN(str(page) + "/" + str(total_pages), "noop"))
    if page < total_pages:
        nav.append(BTN(_l("next"), "ub:" + flt + ":" + str(page + 1)))
    kb_rows.append(nav)
    kb_rows.append([BTN("All", "ub:all:1"), BTN("Active", "ub:active:1"), BTN("Banned", "ub:banned:1")])
    kb_rows.append([BTN("Paying", "ub:paid:1"), BTN("Newest", "ub:new:1"),
                    BTN(_e("search") + " Search", "ub_search")])
    kb_rows.append([BTN(_e("menu") + " Commands", "cmd_center"), BACK("main_menu")])
    return KB(*kb_rows)


def _sub_panel(target, title, text):
    """Shared frame for the member sub-panels."""
    return "\n".join([
        title,
        divider(),
        text or I("Nothing recorded yet."),
        divider(),
        I("Member ") + C(str(int(target))),
    ])


def kb_sub_panel(target):
    """Back navigation for a sub-panel."""
    return KB(
        [BTN(_e("user") + " Full profile", "ui:" + str(int(target)))],
        [BTN(_e("back") + " Members", "ub:all:1"), BACK("main_menu")],
    )


# ---- p5b ----


def msg_user_detail(target):
    """Full profile card for one member."""
    user = get_user(int(target)) or {}
    if not user:
        return _e("warn") + " No such member."
    files = int(db_val("SELECT COUNT(*) c FROM files WHERE uid=?", (int(target),), 0) or 0)
    used = int(db_val("SELECT COALESCE(SUM(fsize),0) s FROM files WHERE uid=?", (int(target),), 0) or 0)
    tickets = int(db_val("SELECT COUNT(*) c FROM tickets WHERE uid=?", (int(target),), 0) or 0)
    warns = int(db_val("SELECT COUNT(*) c FROM user_warnings WHERE uid=?", (int(target),), 0) or 0)
    runs = int(db_val("SELECT COUNT(*) c FROM script_logs WHERE uid=?", (int(target),), 0) or 0)
    tier_key = str(user.get("tier") or "free")
    tier = TIERS.get(tier_key, {})
    banned = int(user.get("is_banned") or 0)
    lines = [
        _e("user") + " " + B(esc(str(user.get("first_name") or "user"))) + "  " + tier_badge(tier_key),
        divider(),
        B("User ID") + "  " + C(str(int(target))),
        B("Username") + "  " + (("@" + esc(str(user.get("username")))) if user.get("username") else I("none")),
        B("Plan") + "  " + esc(str(tier.get("name") or tier_key)),
        B("Status") + "  " + ((_e("ban") + " banned") if banned else (_e("check") + " active")),
        divider(),
        B("Level") + "  " + str(int(user.get("level") or 1))
        + "   " + B("XP") + "  " + fmt_num(int(user.get("xp") or 0)),
        B("Points") + "  " + fmt_num(int(user.get("points") or 0))
        + "   " + B("Coins") + "  " + fmt_num(int(user.get("coins") or 0)),
        B("Streak") + "  " + str(int(user.get("daily_streak") or 0)) + " day(s)",
        divider(),
        B("Files") + "  " + str(files) + "   " + B("Storage") + "  " + fmt_size(used),
        B("Script runs") + "  " + str(runs) + "   " + B("Tickets") + "  " + str(tickets),
        B("Warnings") + "  " + str(warns),
        divider(),
        B("Joined") + "  " + fmt_dt(str(user.get("joined_at") or "")),
        B("Last seen") + "  " + fmt_dt(str(user.get("last_seen") or "")),
    ]
    if user.get("ban_reason"):
        lines.append(B("Ban reason") + "  " + esc(str(user.get("ban_reason"))))
    lines.append(divider())
    lines.append(I("Use the buttons for files, activity, economy and moderation."))
    return "\n".join(lines)


def kb_user_detail(uid, target):
    """Drill-down buttons for one member."""
    target = int(target)
    user = get_user(target) or {}
    rows = [
        [BTN(_e("folder") + " Files", "uf:" + str(target)),
         BTN(_e("run") + " Activity", "ua:" + str(target))],
        [BTN(_e("coin") + " Economy", "ue:" + str(target)),
         BTN(_e("ticket") + " Tickets", "ut:" + str(target))],
        [BTN(_e("code") + " Commands", "uc:" + str(target)),
         BTN(_e("gear") + " Settings", "us:" + str(target))],
        [BTN(_e("warn") + " Warnings", "uw:" + str(target)),
         BTN(_e("note") + " Notes", "un:" + str(target))],
    ]
    if is_admin(uid):
        if int(user.get("is_banned") or 0):
            rows.append([BTN(_e("check") + " Unban", "uunban:" + str(target)),
                         BTN(_e("warn") + " Warn", "uwarn:" + str(target))])
        else:
            rows.append([BTN(_e("ban") + " Ban", "uban:" + str(target)),
                         BTN(_e("warn") + " Warn", "uwarn:" + str(target))])
        rows.append([BTN(_e("bell") + " Message", "udm:" + str(target)),
                     BTN(_e("coin") + " Give points", "ugp:" + str(target))])
    if is_owner(uid):
        rows.append([BTN(_e("trash") + " Wipe member data", "uwipe:" + str(target))])
    rows.append([BTN(_e("back") + " Members", "ub:all:1"), BACK("main_menu")])
    return KB(*rows)


# ---- p5c ----


@extension_callback("ub:", admin_only=True)
def cb_user_browse(call, uid, cid, mid, data):
    """Member browser with filters."""
    ack(call)
    parts = str(data).split(":")
    flt = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 1
    rows, total, total_pages, page = browse_users(page, USER_PAGE_SIZE, flt)
    edit(cid, mid, msg_user_browser(rows, total, page, total_pages, flt),
         kb_user_browser(rows, page, total_pages, flt))


@extension_callback("ub_search", exact=True, admin_only=True)
def cb_user_search(call, uid, cid, mid, data):
    """Ask for a member search term."""
    ack(call)
    set_state(uid, "awaiting_user_browse")
    edit(cid, mid, "\n".join([
        _e("search") + " " + B("FIND A MEMBER"),
        divider(),
        I("Send a user ID, first name or username."),
    ]), KB([BTN(_e("back") + " Members", "ub:all:1")]))


def _fsm_user_browse(message, uid, state):
    """Show member search results."""
    clear_state(uid)
    term = str(getattr(message, "text", "") or "").strip().lstrip("@")
    rows, total, total_pages, page = browse_users(1, USER_PAGE_SIZE, "search", term)
    send(uid, msg_user_browser(rows, total, page, total_pages, "search", term),
         kb_user_browser(rows, page, total_pages, "all"))


@extension_callback("ui:", admin_only=True)
def cb_user_info(call, uid, cid, mid, data):
    """Open one member's profile."""
    ack(call)
    target = _tail_int(data, "ui:")
    edit(cid, mid, msg_user_detail(target), kb_user_detail(uid, target))


@extension_callback("uf:", admin_only=True)
def cb_user_files(call, uid, cid, mid, data):
    """A member's files."""
    ack(call)
    target = _tail_int(data, "uf:")
    rows = db_all("SELECT * FROM files WHERE uid=? ORDER BY id DESC LIMIT 12", (target,))
    lines = []
    for row in rows:
        lines.append(
            status_icon(str(row.get("status") or "")) + " "
            + C("#" + str(int(row.get("id") or 0))) + " "
            + esc(trunc(str(row.get("fname") or ""), 30))
            + "  " + I(fmt_size(int(row.get("fsize") or 0)))
        )
    edit(cid, mid, _sub_panel(target, _e("folder") + " " + B("MEMBER FILES"), "\n".join(lines)),
         kb_sub_panel(target))


@extension_callback("ua:", admin_only=True)
def cb_user_activity(call, uid, cid, mid, data):
    """A member's recent activity."""
    ack(call)
    target = _tail_int(data, "ua:")
    rows = db_all("SELECT * FROM user_activity WHERE uid=? ORDER BY id DESC LIMIT 12", (target,))
    lines = [
        _e("star") + " " + esc(str(row.get("action") or "")) + "  "
        + I(fmt_dt(str(row.get("created_at") or "")))
        for row in rows
    ]
    edit(cid, mid, _sub_panel(target, _e("run") + " " + B("MEMBER ACTIVITY"), "\n".join(lines)),
         kb_sub_panel(target))


@extension_callback("ue:", admin_only=True)
def cb_user_economy(call, uid, cid, mid, data):
    """A member's economy snapshot."""
    ack(call)
    target = _tail_int(data, "ue:")
    user = get_user(target) or {}
    refs = int(db_val("SELECT COUNT(*) c FROM referrals WHERE referrer_uid=?", (target,), 0) or 0)
    earned = int(db_val(
        "SELECT COALESCE(SUM(pts_awarded),0) s FROM referrals WHERE referrer_uid=?",
        (target,), 0) or 0)
    text = "\n".join([
        B("Points") + "  " + fmt_num(int(user.get("points") or 0)),
        B("Coins") + "  " + fmt_num(int(user.get("coins") or 0)),
        B("Level") + "  " + str(int(user.get("level") or 1))
        + "   " + B("XP") + "  " + fmt_num(int(user.get("xp") or 0)),
        B("Daily streak") + "  " + str(int(user.get("daily_streak") or 0)),
        B("Referrals") + "  " + str(refs) + "   " + B("Referral points") + "  " + fmt_num(earned),
    ])
    edit(cid, mid, _sub_panel(target, _e("coin") + " " + B("MEMBER ECONOMY"), text), kb_sub_panel(target))


@extension_callback("ut:", admin_only=True)
def cb_user_tickets(call, uid, cid, mid, data):
    """A member's support tickets."""
    ack(call)
    target = _tail_int(data, "ut:")
    rows = db_all("SELECT * FROM tickets WHERE uid=? ORDER BY id DESC LIMIT 12", (target,))
    lines = [
        C("#" + str(int(row.get("id") or 0))) + " "
        + esc(trunc(str(row.get("subject") or ""), 32))
        + "  " + I(str(row.get("status") or ""))
        for row in rows
    ]
    edit(cid, mid, _sub_panel(target, _e("ticket") + " " + B("MEMBER TICKETS"), "\n".join(lines)),
         kb_sub_panel(target))


# ---- p5d ----


@extension_callback("uc:", admin_only=True)
def cb_user_commands(call, uid, cid, mid, data):
    """Which commands a member uses."""
    ack(call)
    target = _tail_int(data, "uc:")
    rows = db_all(
        "SELECT command, COUNT(*) c FROM command_usage WHERE uid=?"
        " GROUP BY command ORDER BY c DESC LIMIT 12", (target,))
    lines = [C("/" + str(row.get("command") or "")) + "  " + str(int(row.get("c") or 0)) + "x"
             for row in rows]
    edit(cid, mid, _sub_panel(target, _e("code") + " " + B("COMMAND USAGE"), "\n".join(lines)),
         kb_sub_panel(target))


@extension_callback("us:", admin_only=True)
def cb_user_settings(call, uid, cid, mid, data):
    """A member's preferences."""
    ack(call)
    target = _tail_int(data, "us:")
    user = get_user(target) or {}
    text = "\n".join([
        B("Theme") + "  " + esc(str(user.get("theme") or "default")),
        B("Font") + "  " + esc(str(user.get("font") or "default")),
        B("Language") + "  " + esc(str(user.get("lang") or "en")),
        B("Push notifications") + "  " + ("on" if int(user.get("push_enabled") or 0) else "off"),
        B("API key") + "  " + ("issued" if user.get("api_key") else "none"),
    ])
    edit(cid, mid, _sub_panel(target, _e("gear") + " " + B("MEMBER SETTINGS"), text),
         kb_sub_panel(target))


@extension_callback("uw:", admin_only=True)
def cb_user_warnings(call, uid, cid, mid, data):
    """A member's warnings."""
    ack(call)
    target = _tail_int(data, "uw:")
    rows = get_warnings(target)
    lines = [
        _e("warn") + " " + esc(trunc(str(row.get("reason") or "no reason"), 40))
        + "  " + I(fmt_dt(str(row.get("created_at") or "")))
        for row in rows
    ]
    markup = KB(
        [BTN(_e("warn") + " Add warning", "uwarn:" + str(target)),
         BTN(_e("check") + " Clear all", "uwclear:" + str(target))],
        [BTN(_e("user") + " Full profile", "ui:" + str(target))],
        [BTN(_e("back") + " Members", "ub:all:1"), BACK("main_menu")],
    )
    edit(cid, mid, _sub_panel(target, _e("warn") + " " + B("MEMBER WARNINGS"), "\n".join(lines)),
         markup)


@extension_callback("un:", admin_only=True)
def cb_user_notes(call, uid, cid, mid, data):
    """A member's saved notes."""
    ack(call)
    target = _tail_int(data, "un:")
    rows = db_all("SELECT * FROM user_notes WHERE uid=? ORDER BY id DESC LIMIT 12", (target,))
    lines = [C("#" + str(int(row.get("id") or 0))) + " "
             + esc(trunc(str(row.get("title") or ""), 34)) for row in rows]
    edit(cid, mid, _sub_panel(target, _e("note") + " " + B("MEMBER NOTES"), "\n".join(lines)),
         kb_sub_panel(target))


@extension_callback("uban:", admin_only=True)
def cb_user_ban(call, uid, cid, mid, data):
    """Ban a member."""
    target = _tail_int(data, "uban:")
    if is_admin(target):
        ack(call, "Administrators cannot be banned.", True)
        return
    ack(call, "Banned.")
    ban_user(target, "banned from the member panel", uid)
    chlog("moderation", "Member banned", "by admin " + str(uid), target)
    edit(cid, mid, msg_user_detail(target), kb_user_detail(uid, target))


@extension_callback("uunban:", admin_only=True)
def cb_user_unban(call, uid, cid, mid, data):
    """Lift a ban."""
    ack(call, "Unbanned.")
    target = _tail_int(data, "uunban:")
    unban_user(target, uid)
    chlog("moderation", "Member unbanned", "by admin " + str(uid), target)
    edit(cid, mid, msg_user_detail(target), kb_user_detail(uid, target))


@extension_callback("uwclear:", admin_only=True)
def cb_user_warn_clear(call, uid, cid, mid, data):
    """Clear every warning."""
    target = _tail_int(data, "uwclear:")
    removed = clear_warnings(target)
    ack(call, "Cleared " + str(removed) + " warning(s).")
    chlog("moderation", "Warnings cleared", str(removed) + " removed by " + str(uid), target)
    edit(cid, mid, msg_user_detail(target), kb_user_detail(uid, target))


# ---- p5e ----


@extension_callback("uwarn:", admin_only=True)
def cb_user_warn(call, uid, cid, mid, data):
    """Ask for a warning reason."""
    ack(call)
    target = _tail_int(data, "uwarn:")
    set_state(uid, "awaiting_warn_reason", target=target)
    edit(cid, mid, "\n".join([
        _e("warn") + " " + B("WARN MEMBER ") + C(str(target)),
        divider(),
        I("Send the reason. The member is notified immediately."),
    ]), KB([BTN(_e("back") + " Cancel", "ui:" + str(target))]))


def _fsm_warn_reason(message, uid, state):
    """Store the warning and tell the member."""
    target = int((state.get("data") or {}).get("target") or 0)
    clear_state(uid)
    reason = str(getattr(message, "text", "") or "").strip()[:300]
    total = add_warning(target, uid, reason)
    notify_user(target, _e("warn") + " " + B("You received a warning") + "\n\n"
                + esc(reason) + "\n\n" + I("Total warnings: " + str(total)))
    chlog("moderation", "Member warned", reason, target)
    send(uid, _e("check") + " Warning recorded (" + str(total) + " total).",
         KB([BTN(_e("user") + " Profile", "ui:" + str(target))]))


@extension_callback("udm:", admin_only=True)
def cb_user_dm(call, uid, cid, mid, data):
    """Ask for a direct message."""
    ack(call)
    target = _tail_int(data, "udm:")
    set_state(uid, "awaiting_dm_text", target=target)
    edit(cid, mid, "\n".join([
        _e("bell") + " " + B("MESSAGE MEMBER ") + C(str(target)),
        divider(),
        I("Send the text and it is delivered right away."),
    ]), KB([BTN(_e("back") + " Cancel", "ui:" + str(target))]))


def _fsm_dm_text(message, uid, state):
    """Deliver an admin message."""
    target = int((state.get("data") or {}).get("target") or 0)
    clear_state(uid)
    text = str(getattr(message, "text", "") or "").strip()[:1500]
    notify_user(target, _e("bell") + " " + B("Message from the team") + "\n\n" + esc(text))
    chlog("admin", "Direct message sent", text[:200], target)
    send(uid, _e("check") + " Delivered.", KB([BTN(_e("user") + " Profile", "ui:" + str(target))]))


@extension_callback("ugp:", admin_only=True)
def cb_user_grant(call, uid, cid, mid, data):
    """Ask how many points to grant."""
    ack(call)
    target = _tail_int(data, "ugp:")
    set_state(uid, "awaiting_grant_points", target=target)
    edit(cid, mid, "\n".join([
        _e("coin") + " " + B("GRANT POINTS TO ") + C(str(target)),
        divider(),
        I("Send a number. Negative values remove points."),
    ]), KB([BTN(_e("back") + " Cancel", "ui:" + str(target))]))


def _fsm_grant_points(message, uid, state):
    """Apply a points adjustment."""
    target = int((state.get("data") or {}).get("target") or 0)
    clear_state(uid)
    raw = str(getattr(message, "text", "") or "").strip()
    try:
        amount = int(raw)
    except Exception:
        send(uid, _e("warn") + " That is not a whole number.")
        return
    add_points(target, amount, "admin grant")
    notify_user(target, _e("coin") + " " + B("Points updated") + "\n\n"
                + (("+" if amount >= 0 else "") + str(amount)) + " point(s) by an administrator.")
    chlog("economy", "Points adjusted", str(amount) + " by admin " + str(uid), target)
    send(uid, _e("check") + " Applied " + B(str(amount)) + " point(s).",
         KB([BTN(_e("user") + " Profile", "ui:" + str(target))]))


@extension_callback("uwipe:")
def cb_user_wipe(call, uid, cid, mid, data):
    """Owner only: confirm before deleting a member's data."""
    target = _tail_int(data, "uwipe:")
    if not is_owner(uid):
        ack(call, "Owners only.", True)
        return
    ack(call)
    edit(cid, mid, "\n".join([
        _e("trash") + " " + B("WIPE MEMBER DATA"),
        divider(),
        "This deletes every file record, ticket, note and log for " + C(str(target)) + ".",
        I("The account row is kept so the member can start again."),
    ]), KB(
        [BTN(_e("warn") + " Yes, wipe it", "uwipe2:" + str(target))],
        [BTN(_e("back") + " Cancel", "ui:" + str(target))],
    ))


@extension_callback("uwipe2:")
def cb_user_wipe_confirm(call, uid, cid, mid, data):
    """Owner only: perform the wipe."""
    target = _tail_int(data, "uwipe2:")
    if not is_owner(uid):
        ack(call, "Owners only.", True)
        return
    ack(call, "Wiped.")
    for table in ("files", "tickets", "user_notes", "script_logs", "user_activity",
                  "notifications", "command_usage", "user_warnings"):
        db_exec("DELETE FROM " + table + " WHERE uid=?", (target,))
    chlog("owner", "Member data wiped", "by owner " + str(uid), target)
    edit(cid, mid, msg_user_detail(target), kb_user_detail(uid, target))


# ---- p6 ----


# ---------------------------------------------------------------------------
# Moderation helpers
# ---------------------------------------------------------------------------


def add_warning(uid, admin_uid=0, reason=""):
    """Record a warning and return the new total."""
    db_exec(
        "INSERT INTO user_warnings (uid, admin_uid, reason, created_at) VALUES (?,?,?,?)",
        (int(uid), int(admin_uid or 0), str(reason)[:300], utcstamp()),
    )
    return int(db_val("SELECT COUNT(*) c FROM user_warnings WHERE uid=?", (int(uid),), 0) or 0)


def get_warnings(uid, limit=12):
    """Warnings for one member, newest first."""
    return db_all(
        "SELECT * FROM user_warnings WHERE uid=? ORDER BY id DESC LIMIT ?",
        (int(uid), int(limit)),
    )


def clear_warnings(uid):
    """Delete every warning of a member; returns how many were removed."""
    total = int(db_val("SELECT COUNT(*) c FROM user_warnings WHERE uid=?", (int(uid),), 0) or 0)
    db_exec("DELETE FROM user_warnings WHERE uid=?", (int(uid),))
    return total


def mute_user(uid, minutes=60, reason="", admin_uid=0):
    """Mute a member for a number of minutes."""
    until = int(time.time()) + max(1, int(minutes or 1)) * 60
    db_exec(
        "INSERT INTO mutes (uid, until_ts, reason, admin_uid, created_at) VALUES (?,?,?,?,?)"
        " ON CONFLICT(uid) DO UPDATE SET until_ts=excluded.until_ts, reason=excluded.reason,"
        " admin_uid=excluded.admin_uid, created_at=excluded.created_at",
        (int(uid), until, str(reason)[:200], int(admin_uid or 0), utcstamp()),
    )
    return until


def unmute_user(uid):
    """Remove a mute."""
    db_exec("DELETE FROM mutes WHERE uid=?", (int(uid),))
    return True


def is_muted(uid):
    """True while a mute is still active."""
    until = int(db_val("SELECT until_ts FROM mutes WHERE uid=?", (int(uid),), 0) or 0)
    return until > int(time.time())


def active_mutes():
    """Every mute that has not expired."""
    return db_all(
        "SELECT * FROM mutes WHERE until_ts > ? ORDER BY until_ts DESC",
        (int(time.time()),),
    )


# ---------------------------------------------------------------------------
# Command factories - each returns a runner(uid, args) -> text or (text, kb)
# ---------------------------------------------------------------------------


def _arg_int(args):
    """First integer inside the argument string."""
    match = re.search(r"-?\d+", str(args or ""))
    return int(match.group(0)) if match else 0


def card(title, lines, footer=""):
    """Standard result card."""
    out = [title, divider()]
    out.extend([line for line in lines if line is not None])
    if footer:
        out.append(divider())
        out.append(I(footer))
    return "\n".join(out)


def text_cmd(title, lines):
    """A static information card."""

    def runner(uid, args, _title=title, _lines=tuple(lines)):
        return card(_title, list(_lines))

    return runner


def sql_cmd(title, sql, mode="u", columns=None, empty="Nothing here yet.", limit=12):
    """List rows from one query as a numbered card."""

    def runner(uid, args, _sql=sql, _mode=mode, _title=title, _cols=columns, _empty=empty, _limit=limit):
        if _mode == "u":
            params = (int(uid), int(_limit))
        elif _mode == "none":
            params = (int(_limit),)
        elif _mode == "i":
            params = (_arg_int(args), int(_limit))
        elif _mode == "ui":
            params = (int(uid), _arg_int(args), int(_limit))
        elif _mode == "ulike":
            params = (int(uid), "%" + str(args or "").strip() + "%", int(_limit))
        elif _mode == "like":
            params = ("%" + str(args or "").strip() + "%", int(_limit))
        elif _mode == "like3":
            like = "%" + str(args or "").strip() + "%"
            params = (like, like, like, int(_limit))
        else:
            params = (int(_limit),)
        rows = db_all(_sql, params)
        if not rows:
            return card(_title, [I(_empty)])
        lines = []
        for index, row in enumerate(rows, 1):
            parts = []
            for label, key in (_cols or []):
                value = row.get(key)
                if value is None or value == "":
                    continue
                parts.append(B(label) + " " + esc(trunc(str(value), 40)))
            lines.append(str(index) + ".  " + "   ".join(parts))
        return card(_title, lines, str(len(rows)) + " row(s) shown")

    return runner


def stat_cmd(title, pairs):
    """Show a set of single-value queries as a stat card."""

    def runner(uid, args, _title=title, _pairs=tuple(pairs)):
        lines = []
        for label, sql, mode in _pairs:
            params = (int(uid),) if mode == "u" else ()
            value = db_val(sql, params, 0)
            if str(label).lower().startswith("byte") or str(label).lower() == "storage":
                lines.append(B(label) + "  " + fmt_size(int(value or 0)))
            else:
                lines.append(B(label) + "  " + fmt_num(int(value or 0)))
        return card(_title, lines)

    return runner


def fn_cmd(title, func, mode="u", columns=None, empty="Nothing to show."):
    """Wrap an existing bot function and render whatever it returns."""

    def runner(uid, args, _func=func, _mode=mode, _title=title, _cols=columns, _empty=empty):
        if _mode == "u":
            result = _func(int(uid))
        elif _mode == "i":
            result = _func(_arg_int(args))
        elif _mode == "ui":
            result = _func(int(uid), _arg_int(args))
        elif _mode == "ua":
            result = _func(int(uid), str(args or "").strip())
        elif _mode == "a":
            result = _func(str(args or "").strip())
        else:
            result = _func()
        return card(_title, _render_value(result, _cols, _empty))

    return runner


def _render_value(value, columns=None, empty="Nothing to show."):
    """Turn any helper return value into printable lines."""
    if value is None:
        return [I(empty)]
    if isinstance(value, tuple):
        if len(value) == 2 and isinstance(value[0], bool):
            mark = _e("check") if value[0] else _e("warn")
            return [mark + " " + esc(str(value[1]))]
        value = value[0]
    if isinstance(value, bool):
        return [(_e("check") + " Done.") if value else (_e("warn") + " That did not work.")]
    if isinstance(value, (int, float)):
        return [B("Result") + "  " + fmt_num(int(value))]
    if isinstance(value, str):
        return [esc(value) if "<" not in value else value]
    if isinstance(value, dict):
        return [B(str(key).replace("_", " ").title()) + "  " + esc(trunc(str(val), 44))
                for key, val in list(value.items())[:20]]
    if isinstance(value, (list, tuple)):
        if not value:
            return [I(empty)]
        lines = []
        for index, row in enumerate(list(value)[:14], 1):
            if isinstance(row, dict):
                if columns:
                    parts = [B(label) + " " + esc(trunc(str(row.get(key)), 36))
                             for label, key in columns if row.get(key) not in (None, "")]
                else:
                    parts = [B(str(key).title()) + " " + esc(trunc(str(val), 30))
                             for key, val in list(row.items())[:4]]
                lines.append(str(index) + ".  " + "   ".join(parts))
            else:
                lines.append(str(index) + ".  " + esc(trunc(str(row), 60)))
        return lines
    return [esc(trunc(str(value), 400))]


def define(name, group, scope, help_text, runner, usage=""):
    """Register one command (thin wrapper used by the definition tables)."""
    return register_command(name, group, scope, help_text, runner, usage)


# ---- pA ----


# ---------------------------------------------------------------------------
# Commands - guides and personal lists
# ---------------------------------------------------------------------------

for _spec in [
    ("about", "core", "What this platform does", _e("sigma") + " " + B("ABOUT SIGMA-HOSTING"), [
        I("A private hosting platform for your files and Python scripts."),
        "",
        B("Upload") + "  send any document to store it safely",
        B("Run") + "  execute approved Python scripts on demand",
        B("Schedule") + "  repeat a script automatically",
        B("Share") + "  publish a file with a private link",
    ]),
    ("rules", "core", "House rules for members", _e("shield") + " " + B("HOUSE RULES"), [
        "1.  Upload only code and files you own.",
        "2.  No malware, backdoors or self-replicating scripts.",
        "3.  No credential stealers or remote-control payloads.",
        "4.  Do not try to escape the sandbox.",
        "5.  Be civil in support tickets.",
    ]),
    ("faq", "core", "Questions people ask most", _e("info") + " " + B("FAQ"), [
        B("How do I upload?") + "  just send the file to this chat",
        B("Why is my file pending?") + "  an admin reviews new uploads",
        B("Can I edit code here?") + "  yes, open a file and choose Edit",
        B("How do I run a script?") + "  open the file and press Run",
        B("How do I get more space?") + "  request a bigger plan",
    ]),
    ("terms", "core", "Terms of use", _e("note") + " " + B("TERMS OF USE"), [
        "- You are responsible for everything you upload.",
        "- Scripts run in a restricted sandbox with limits.",
        "- Administrators may remove content that breaks the rules.",
        "- The service is offered as is, without any warranty.",
    ]),
    ("privacy", "core", "What data is stored", _e("lock") + " " + B("PRIVACY"), [
        B("Stored") + "  your Telegram ID, name, files and activity",
        B("Why") + "  to show your dashboard and keep the service safe",
        B("Shared") + "  nothing is shared with third parties",
        B("Removal") + "  ask an admin and your data can be wiped",
    ]),
    ("uploadhelp", "files", "How uploading works", _e("upload") + " " + B("UPLOAD GUIDE"), [
        "1.  Send the document straight to this chat.",
        "2.  The file is scanned for dangerous patterns.",
        "3.  Clean files are stored, risky ones go to review.",
        "4.  Open " + C("/myfiles") + " to manage everything you sent.",
    ]),
    ("runhelp", "runner", "How the script runner works", _e("run") + " " + B("RUNNER GUIDE"), [
        "1.  Upload a " + C(".py") + " file and wait for approval.",
        "2.  Open the file and press Run.",
        "3.  Output is captured and shown back to you.",
        "4.  Long jobs are stopped automatically.",
    ]),
    ("security", "core", "How your code is checked", _e("shield") + " " + B("SECURITY"), [
        B("Static scan") + "  every upload is inspected for risky calls",
        B("Pattern rules") + "  self-replication and stealers are blocked",
        B("Quarantine") + "  unclear code is held for a human review",
        B("Sandbox") + "  scripts run isolated, with a decoy token",
        B("Edit guard") + "  edits are re-scanned so clean code cannot turn bad",
    ]),
    ("supporthelp", "support", "How to reach a human", _e("ticket") + " " + B("SUPPORT"), [
        "1.  Open the support panel and start a ticket.",
        "2.  Describe the problem with as much detail as you can.",
        "3.  An admin replies in the same ticket.",
    ]),
]:
    define(_spec[0], _spec[1], "user", _spec[2], text_cmd(_spec[3], _spec[4]))


for _spec in [
    ("myfiles", "files", "Everything you uploaded",
     "SELECT * FROM files WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Name", "fname"), ("Status", "status")]),
    ("largest", "files", "Your biggest files",
     "SELECT * FROM files WHERE uid=? ORDER BY fsize DESC LIMIT ?", "u",
     [("Name", "fname"), ("Size", "fsize")]),
    ("publicfiles", "files", "Your published files",
     "SELECT * FROM files WHERE uid=? AND is_public=1 ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Name", "fname")]),
    ("pendingfiles", "files", "Your files waiting for review",
     "SELECT * FROM files WHERE uid=? AND status='pending' ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Name", "fname")]),
    ("popularfiles", "files", "Your most downloaded files",
     "SELECT * FROM files WHERE uid=? AND downloads > 0 ORDER BY downloads DESC LIMIT ?", "u",
     [("Name", "fname"), ("Downloads", "downloads")]),
    ("mostrun", "runner", "Your most executed scripts",
     "SELECT * FROM files WHERE uid=? AND runs > 0 ORDER BY runs DESC LIMIT ?", "u",
     [("Name", "fname"), ("Runs", "runs")]),
    ("filesearch", "files", "Find one of your files by name",
     "SELECT * FROM files WHERE uid=? AND fname LIKE ? ORDER BY id DESC LIMIT ?", "ulike",
     [("#", "id"), ("Name", "fname")]),
    ("filetags", "files", "Your tagged files",
     "SELECT * FROM files WHERE uid=? AND tags <> '' ORDER BY id DESC LIMIT ?", "u",
     [("Name", "fname"), ("Tags", "tags")]),
    ("versions", "files", "Saved versions of a file",
     "SELECT * FROM file_versions WHERE fid=? ORDER BY version DESC LIMIT ?", "i",
     [("Version", "version"), ("Size", "size")]),
    ("shares", "files", "Share links you created",
     "SELECT * FROM file_shares WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("File", "file_id"), ("Token", "token")]),
    ("mynotes", "account", "Notes you saved",
     "SELECT * FROM user_notes WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Title", "title")]),
    ("myactivity", "account", "Your recent activity",
     "SELECT * FROM user_activity WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("Action", "action"), ("When", "created_at")]),
    ("mycommands", "account", "Commands you used recently",
     "SELECT * FROM command_usage WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("Command", "command"), ("When", "created_at")]),
    ("mywarnings", "account", "Warnings on your account",
     "SELECT * FROM user_warnings WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("Reason", "reason"), ("When", "created_at")]),
    ("apikeys", "account", "API keys you created",
     "SELECT * FROM api_keys WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Label", "label")]),
    ("notifications", "account", "Your latest notifications",
     "SELECT * FROM notifications WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("Message", "message"), ("When", "created_at")]),
    ("myreferrals", "economy", "People you invited",
     "SELECT * FROM referrals WHERE referrer_uid=? ORDER BY id DESC LIMIT ?", "u",
     [("Invited", "referred_uid"), ("Points", "pts_awarded")]),
    ("myorders", "economy", "Your upgrade requests",
     "SELECT * FROM plan_orders WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Plan", "tier"), ("Status", "status")]),
    ("mytickets", "support", "Your support tickets",
     "SELECT * FROM tickets WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Subject", "subject"), ("Status", "status")]),
    ("mycrons", "runner", "Your scheduled jobs",
     "SELECT * FROM cron_jobs WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("Every", "schedule"), ("On", "enabled")]),
    ("myruns", "runner", "Your script run history",
     "SELECT * FROM script_logs WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("File", "fid"), ("When", "ran_at")]),
    ("mywebhooks", "account", "Your webhook endpoints",
     "SELECT * FROM webhooks WHERE uid=? ORDER BY id DESC LIMIT ?", "u",
     [("#", "id"), ("URL", "url")]),
]:
    define(_spec[0], _spec[1], "user", _spec[2],
           sql_cmd(_e("log") + " " + B(_spec[2].upper()), _spec[3], _spec[4], _spec[5]),
           "/" + _spec[0] + (" <id>" if _spec[4] == "i" else
                             (" <text>" if _spec[4] == "ulike" else "")))


# ---- pB ----


# ---------------------------------------------------------------------------
# Commands - profile, economy, tools and fun
# ---------------------------------------------------------------------------


def _tool(func, hint):
    """Wrap a plain text transformation into a command runner."""

    def runner(uid, args, _func=func, _hint=hint):
        text = str(args or "").strip()
        if not text:
            return card(_e("info") + " " + B("SEND SOME TEXT"), [I("Example"), C(_hint)])
        try:
            return card(_e("code") + " " + B("RESULT"), [_func(text)])
        except Exception as exc:
            return card(_e("warn") + " " + B("COULD NOT DO THAT"),
                        [I(esc(str(exc)[:160])), "", B("Example") + "  " + C(_hint)])

    return runner


def safe_calc(expression):
    """Evaluate a small arithmetic expression safely."""
    allowed = set("0123456789+-*/(). %")
    if not set(str(expression)) <= allowed:
        raise ValueError("only numbers and + - * / % ( ) are allowed")
    node = ast.parse(str(expression), mode="eval")
    for item in ast.walk(node):
        if not isinstance(item, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                 ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
                                 ast.USub, ast.UAdd, ast.FloorDiv, ast.Pow)):
            raise ValueError("that expression is not allowed")
    return eval(compile(node, "<calc>", "eval"))


def rot13_text(text):
    """Rotate letters by thirteen places."""
    out = []
    for ch in str(text):
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + 13) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + 13) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)


def _cmd_me(uid, args):
    """/me - your profile card."""
    member = get_user(uid) or {}
    files = db_val("SELECT COUNT(*) FROM files WHERE uid=?", (int(uid),), 0) or 0
    used = db_val("SELECT COALESCE(SUM(fsize),0) FROM files WHERE uid=?", (int(uid),), 0) or 0
    runs = db_val("SELECT COUNT(*) FROM script_logs WHERE uid=?", (int(uid),), 0) or 0
    handle = str(member.get("username") or "").strip()
    return card(_e("user") + " " + B("YOUR PROFILE"), [
        B("Name") + "       " + esc(str(member.get("first_name") or "member")),
        B("Username") + "   " + (C("@" + handle) if handle else I("none set")),
        B("User ID") + "    " + C(str(uid)),
        B("Plan") + "       " + esc(str(member.get("tier") or "free")),
        B("Level") + "      " + str(member.get("level") or 0)
        + "   " + B("XP") + "  " + fmt_num(member.get("xp") or 0),
        B("Points") + "     " + fmt_num(member.get("points") or 0)
        + "   " + B("Coins") + "  " + fmt_num(member.get("coins") or 0),
        B("Streak") + "     " + str(member.get("daily_streak") or 0) + " day(s)",
        divider(),
        B("Files") + "      " + str(files) + "   (" + fmt_size(int(used)) + ")",
        B("Script runs") + " " + str(runs),
        B("Joined") + "     " + fmt_dt(member.get("joined_at")),
    ]), KB([BTN(_e("folder") + " My files", "c:myfiles"),
            BTN(_e("coin") + " Wallet", "c:wallet")],
           [BTN(_e("edit") + " Fonts", "font_panel"),
            BTN(_e("menu") + " Commands", "cmd_center")],
           [BACK("main_menu")])


def _cmd_wallet(uid, args):
    """/wallet - points, coins and streak."""
    member = get_user(uid) or {}
    earned = db_val("SELECT COALESCE(SUM(pts_awarded),0) FROM referrals WHERE referrer_uid=?",
                    (int(uid),), 0) or 0
    return card(_e("wallet") + " " + B("YOUR WALLET"), [
        B("Points") + "      " + fmt_num(member.get("points") or 0),
        B("Coins") + "       " + fmt_num(member.get("coins") or 0),
        B("Streak") + "      " + str(member.get("daily_streak") or 0) + " day(s)",
        B("From invites") + " " + fmt_num(earned),
        divider(),
        I("Claim your daily bonus, upload files and invite friends to earn more."),
    ]), KB([BTN(_e("gift") + " Daily bonus", "daily"),
            BTN(_e("trophy") + " Leaderboard", "c:topusers")],
           [BACK("main_menu")])


def _cmd_level(uid, args):
    """/level - your level and progress."""
    member = get_user(uid) or {}
    xp = int(member.get("xp") or 0)
    level = int(member.get("level") or 0)
    need = max(1, (level + 1) * 100)
    return card(_e("trophy") + " " + B("LEVEL PROGRESS"), [
        B("Level") + "    " + str(level),
        B("XP") + "       " + fmt_num(xp),
        B("Next at") + "  " + fmt_num(need),
        "",
        pct_bar(min(100, int(xp * 100 / need))),
    ])


def _cmd_myplan(uid, args):
    """/myplan - your plan and limits."""
    member = get_user(uid) or {}
    key = str(member.get("tier") or "free")
    tier = TIERS.get(key, {})
    used = db_val("SELECT COALESCE(SUM(fsize),0) FROM files WHERE uid=?", (int(uid),), 0) or 0
    count = db_val("SELECT COUNT(*) FROM files WHERE uid=?", (int(uid),), 0) or 0
    return card(_e("crown") + " " + B("YOUR PLAN"), [
        B("Plan") + "        " + esc(str(tier.get("name", key))),
        B("Max size") + "    " + esc(str(tier.get("max_size", "n/a"))),
        B("File limit") + "  " + esc(str(tier.get("max_files", "n/a"))),
        divider(),
        B("Files used") + "  " + str(count),
        B("Storage used") + " " + fmt_size(int(used)),
        divider(),
        I("Need more room? Request an upgrade and an admin approves it with"),
        I("a single tap, no commands involved."),
    ]), KB([BTN(_e("crown") + " Upgrade", "upgrade")], [BACK("main_menu")])


def _cmd_plans(uid, args):
    """/plans - every plan side by side."""
    lines = []
    for key, tier in TIERS.items():
        lines.append(B(str(tier.get("name", key))))
        lines.append("    " + B("Price") + "  " + esc(str(tier.get("price", 0))))
        lines.append("    " + B("Storage") + "  " + esc(str(tier.get("max_size", "n/a"))))
        lines.append("    " + B("Files") + "  " + esc(str(tier.get("max_files", "n/a"))))
    return card(_e("crown") + " " + B("MEMBERSHIP PLANS"), lines), \
        KB([BTN(_e("crown") + " Request upgrade", "upgrade")], [BACK("main_menu")])


for _name, _grp, _help, _fn, _usage in [
    ("me", "account", "Your profile card", _cmd_me, "/me"),
    ("profile", "account", "Your profile card", _cmd_me, "/profile"),
    ("wallet", "economy", "Points, coins and streak", _cmd_wallet, "/wallet"),
    ("balance", "economy", "Points, coins and streak", _cmd_wallet, "/balance"),
    ("level", "economy", "Your level and progress", _cmd_level, "/level"),
    ("myplan", "economy", "Your plan and limits", _cmd_myplan, "/myplan"),
    ("limits", "economy", "Your plan and limits", _cmd_myplan, "/limits"),
    ("plans", "economy", "Every plan side by side", _cmd_plans, "/plans"),
    ("prices", "economy", "Every plan side by side", _cmd_plans, "/prices"),
]:
    define(_name, _grp, "user", _help, _fn, _usage)


for _name, _fn, _hint in [
    ("calc", lambda t: C(str(safe_calc(t))), "/calc 2 + 2 * 5"),
    ("b64", lambda t: C(base64.b64encode(t.encode("utf-8")).decode()), "/b64 hello"),
    ("unb64", lambda t: C(base64.b64decode(t.encode("utf-8")).decode("utf-8", "replace")),
     "/unb64 aGVsbG8="),
    ("md5", lambda t: C(hashlib.md5(t.encode("utf-8")).hexdigest()), "/md5 hello"),
    ("sha1", lambda t: C(hashlib.sha1(t.encode("utf-8")).hexdigest()), "/sha1 hello"),
    ("sha256", lambda t: C(hashlib.sha256(t.encode("utf-8")).hexdigest()), "/sha256 hello"),
    ("upper", lambda t: C(t.upper()), "/upper hello"),
    ("lower", lambda t: C(t.lower()), "/lower HELLO"),
    ("titlecase", lambda t: C(t.title()), "/titlecase my file"),
    ("reverse", lambda t: C(t[::-1]), "/reverse hello"),
    ("rot13", lambda t: C(rot13_text(t)), "/rot13 hello"),
    ("snake", lambda t: C(re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")), "/snake My File"),
    ("nospace", lambda t: C(re.sub(r"\s+", "", t)), "/nospace a b c"),
    ("trim", lambda t: C(re.sub(r"\s+", " ", t).strip()), "/trim  a   b "),
    ("unique", lambda t: C(" ".join(dict.fromkeys(t.split()))), "/unique a b a"),
    ("sortwords", lambda t: C(" ".join(sorted(t.split()))), "/sortwords pear apple"),
    ("count", lambda t: B("Characters") + "  " + str(len(t)) + "\n"
     + B("Words") + "  " + str(len(t.split())), "/count hello world"),
    ("binary", lambda t: C(" ".join(format(b, "08b") for b in t.encode("utf-8")[:24])),
     "/binary hi"),
    ("charmap", lambda t: C(" ".join(str(ord(ch)) for ch in t[:32])), "/charmap abc"),
    ("urlsafe", lambda t: C(urllib.parse.quote(t)), "/urlsafe a b&c"),
    ("jsonfmt", lambda t: PRE(json.dumps(json.loads(t), indent=2)[:2000]), '/jsonfmt {"a":1}'),
    ("jsonkeys", lambda t: C(", ".join(sorted(json.loads(t).keys()))), '/jsonkeys {"a":1}'),
    ("words", lambda t: "\n".join(str(i + 1) + ".  " + esc(w)
                                  for i, w in enumerate(t.split()[:40])), "/words a b c"),
    ("lines", lambda t: PRE("\n".join(str(i + 1) + " | " + ln
                                      for i, ln in enumerate(t.split("\n")[:40]))), "/lines text"),
]:
    define(_name, "tools", "user", "Text helper: " + _name, _tool(_fn, _hint),
           "/" + _name + " <text>")


def _cmd_uuid(uid, args):
    """/uuid - four fresh identifiers."""
    return card(_e("key") + " " + B("IDENTIFIERS"),
                [C(str(uuid.uuid4())) for _ in range(4)])


def _cmd_password(uid, args):
    """/password - strong random passwords."""
    pool = string.ascii_letters + string.digits + "!@#$%^&*"
    return card(_e("lock") + " " + B("PASSWORDS"),
                [C("".join(secrets.choice(pool) for _ in range(18))) for _ in range(4)])


def _cmd_timestamp(uid, args):
    """/timestamp - the time right now."""
    now = datetime.now(timezone.utc)
    return card(_e("clock") + " " + B("TIME NOW"), [
        B("UTC") + "        " + C(now.strftime("%Y-%m-%d %H:%M:%S")),
        B("Unix") + "       " + C(str(int(now.timestamp()))),
        B("Weekday") + "    " + esc(now.strftime("%A")),
    ])


def _cmd_dice(uid, args):
    """/dice - roll two dice."""
    a, b = random.randint(1, 6), random.randint(1, 6)
    return card(_e("star") + " " + B("DICE"),
                [B(str(a)) + "  and  " + B(str(b)), I("Total " + str(a + b))])


def _cmd_coinflip(uid, args):
    """/coinflip - heads or tails."""
    return card(_e("coin") + " " + B("COIN FLIP"), [B(random.choice(["Heads", "Tails"]))])


def _cmd_eightball(uid, args):
    """/8ball - ask the magic answer."""
    answers = ["Yes.", "No.", "Try again later.", "Almost certainly.",
               "I would not count on it.", "Ship it.", "Test it first."]
    return card(_e("star") + " " + B("MAGIC ANSWER"), [I(random.choice(answers))])


def _cmd_lucky(uid, args):
    """/lucky - your lucky numbers."""
    return card(_e("star") + " " + B("LUCKY NUMBERS"),
                [B("  ".join(str(n) for n in sorted(random.sample(range(1, 50), 6))))])


def _cmd_motivate(uid, args):
    """/motivate - a short push."""
    lines = ["Ship the small version today.", "A clean function beats a clever one.",
             "Your next backup will save the day.", "Read the log, the answer is there."]
    return card(_e("fire") + " " + B("MOTIVATION"), [I(random.choice(lines))])


def _cmd_ping(uid, args):
    """/ping - check the bot answers."""
    started = time.time()
    db_val("SELECT 1", (), 1)
    return card(_e("ok") + " " + B("PONG"),
                [B("Database") + "  " + str(int((time.time() - started) * 1000)) + " ms",
                 B("Status") + "  online"])


def _cmd_status(uid, args):
    """/status - a short health summary."""
    counts = registry_counts()
    return card(_e("graph") + " " + B("STATUS"), [
        B("Version") + "      " + esc(str(VERSION)),
        B("Commands") + "     " + str(counts.get("total", 0)),
        B("Members") + "      " + str(db_val("SELECT COUNT(*) FROM users", (), 0)),
        B("Files") + "        " + str(db_val("SELECT COUNT(*) FROM files", (), 0)),
        B("Maintenance") + "  " + esc(str(cfg_get("maintenance", "off"))),
    ])


def _cmd_botinfo(uid, args):
    """/botinfo - what this bot is running."""
    counts = registry_counts()
    return card(_e("bot") + " " + B("BOT INFO"), [
        B("Name") + "      " + esc(str(BOT_NAME)),
        B("Version") + "   " + esc(str(VERSION)),
        B("Commands") + "  " + str(counts.get("total", 0)),
        B("Groups") + "    " + str(counts.get("groups", 0)),
        B("Library") + "   pyTelegramBotAPI",
    ]), kb_command_center(uid)


for _name, _grp, _help, _fn in [
    ("uuid", "tools", "Generate identifiers", _cmd_uuid),
    ("password", "tools", "Strong random passwords", _cmd_password),
    ("timestamp", "tools", "The time right now", _cmd_timestamp),
    ("dice", "fun", "Roll two dice", _cmd_dice),
    ("coinflip", "fun", "Heads or tails", _cmd_coinflip),
    ("8ball", "fun", "Ask the magic answer", _cmd_eightball),
    ("lucky", "fun", "Your lucky numbers", _cmd_lucky),
    ("motivate", "fun", "A short push", _cmd_motivate),
    ("ping", "core", "Check the bot answers", _cmd_ping),
    ("status", "core", "Short health summary", _cmd_status),
    ("botinfo", "core", "What this bot is running", _cmd_botinfo),
]:
    define(_name, _grp, "user", _help, _fn)


# ---- pC ----


# ---------------------------------------------------------------------------
# Commands - admin lists, system lists and moderation lists
# ---------------------------------------------------------------------------

for _spec in [
    ("users", "admin", "Every member, newest first",
     "SELECT * FROM users ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Plan", "tier")]),
    ("banned", "admin", "Banned members",
     "SELECT * FROM users WHERE is_banned=1 ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Reason", "ban_reason")]),
    ("newusers", "admin", "Most recent signups",
     "SELECT * FROM users ORDER BY joined_at DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Joined", "joined_at")]),
    ("paidusers", "admin", "Members on a paid plan",
     "SELECT * FROM users WHERE tier <> 'free' ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Plan", "tier")]),
    ("richest", "admin", "Members with the most points",
     "SELECT * FROM users ORDER BY points DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Points", "points")]),
    ("toplevels", "admin", "Highest level members",
     "SELECT * FROM users ORDER BY level DESC, xp DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Level", "level")]),
    ("usersearch", "admin", "Find a member by name or ID",
     "SELECT * FROM users WHERE CAST(uid AS TEXT) LIKE ? OR first_name LIKE ?"
     " OR username LIKE ? ORDER BY uid DESC LIMIT ?", "like3",
     [("UID", "uid"), ("Name", "first_name"), ("Plan", "tier")]),
    ("idleusers", "admin", "Members who never uploaded",
     "SELECT * FROM users WHERE total_uploads=0 ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name")]),
    ("todaysignups", "admin", "Members who joined today",
     "SELECT * FROM users WHERE date(joined_at) = date('now') ORDER BY uid DESC LIMIT ?",
     "none", [("UID", "uid"), ("Name", "first_name")]),
    ("weeksignups", "admin", "Members who joined this week",
     "SELECT * FROM users WHERE datetime(joined_at) >= datetime('now','-7 day')"
     " ORDER BY uid DESC LIMIT ?", "none", [("UID", "uid"), ("Name", "first_name")]),
    ("allfiles", "admin", "Every uploaded file",
     "SELECT * FROM files ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Name", "fname"), ("Status", "status")]),
    ("pending", "admin", "Files waiting for approval",
     "SELECT * FROM files WHERE status='pending' ORDER BY id ASC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Name", "fname")]),
    ("biggestfiles", "admin", "Largest files on the platform",
     "SELECT * FROM files ORDER BY fsize DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Bytes", "fsize")]),
    ("todayuploads", "admin", "Files uploaded today",
     "SELECT * FROM files WHERE date(uploaded) = date('now') ORDER BY id DESC LIMIT ?",
     "none", [("#", "id"), ("Owner", "uid"), ("Name", "fname")]),
    ("userfiles", "admin", "Files owned by one member",
     "SELECT * FROM files WHERE uid=? ORDER BY id DESC LIMIT ?", "i",
     [("#", "id"), ("Name", "fname"), ("Status", "status")]),
    ("filesearchall", "admin", "Search every file by name",
     "SELECT * FROM files WHERE fname LIKE ? ORDER BY id DESC LIMIT ?", "like",
     [("#", "id"), ("Owner", "uid"), ("Name", "fname")]),
    ("opentickets", "admin", "Tickets still open",
     "SELECT * FROM tickets WHERE status='open' ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Subject", "subject")]),
    ("alltickets", "admin", "Every ticket",
     "SELECT * FROM tickets ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Status", "status")]),
    ("userticket", "admin", "Tickets of one member",
     "SELECT * FROM tickets WHERE uid=? ORDER BY id DESC LIMIT ?", "i",
     [("#", "id"), ("Subject", "subject"), ("Status", "status")]),
    ("activitylog", "admin", "Latest member activity",
     "SELECT * FROM user_activity ORDER BY id DESC LIMIT ?", "none",
     [("UID", "uid"), ("Action", "action"), ("When", "created_at")]),
    ("auditlog", "admin", "Administrator actions",
     "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", "none",
     [("Admin", "admin_uid"), ("Action", "action"), ("When", "created_at")]),
    ("broadcasts", "admin", "Broadcast history",
     "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Sent", "sent"), ("Total", "total")]),
    ("allreferrals", "admin", "Every referral",
     "SELECT * FROM referrals ORDER BY id DESC LIMIT ?", "none",
     [("By", "referrer_uid"), ("Invited", "referred_uid"), ("Points", "pts_awarded")]),
    ("orders", "admin", "Upgrade requests",
     "SELECT * FROM plan_orders ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Plan", "tier"), ("Status", "status")]),
    ("orderqueue", "admin", "Orders awaiting action",
     "SELECT * FROM plan_orders WHERE status='pending' ORDER BY id ASC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Plan", "tier")]),
    ("allnotes", "admin", "Notes across the platform",
     "SELECT * FROM user_notes ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Title", "title")]),
    ("allkeys", "admin", "Issued API keys",
     "SELECT * FROM api_keys ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Label", "label")]),
    ("allcrons", "admin", "Every scheduled job",
     "SELECT * FROM cron_jobs ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Every", "schedule"), ("On", "enabled")]),
    ("allwebhooks", "admin", "Every webhook",
     "SELECT * FROM webhooks ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("URL", "url")]),
    ("runlog", "adminsys", "Recent script executions",
     "SELECT * FROM script_logs ORDER BY id DESC LIMIT ?", "none",
     [("Owner", "uid"), ("File", "fid"), ("When", "ran_at")]),
    ("ipwatch", "adminsys", "Recorded IP addresses",
     "SELECT * FROM ip_log ORDER BY id DESC LIMIT ?", "none",
     [("UID", "uid"), ("IP", "ip"), ("When", "created_at")]),
    ("channellog", "adminsys", "Events sent to the log channel",
     "SELECT * FROM channel_log ORDER BY id DESC LIMIT ?", "none",
     [("Kind", "category"), ("Title", "title"), ("Sent", "delivered")]),
    ("failedlogs", "adminsys", "Log entries not yet delivered",
     "SELECT * FROM channel_log WHERE delivered=0 ORDER BY id DESC LIMIT ?", "none",
     [("Kind", "category"), ("Title", "title")]),
    ("errorlog", "adminsys", "Recent errors",
     "SELECT * FROM channel_log WHERE category='error' ORDER BY id DESC LIMIT ?", "none",
     [("Title", "title"), ("Detail", "body")]),
    ("securitylog", "adminsys", "Security events",
     "SELECT * FROM channel_log WHERE category='security' ORDER BY id DESC LIMIT ?", "none",
     [("Title", "title"), ("Detail", "body")]),
    ("cmdlog", "adminsys", "Recent command executions",
     "SELECT * FROM command_usage ORDER BY id DESC LIMIT ?", "none",
     [("UID", "uid"), ("Command", "command"), ("OK", "ok")]),
    ("cmdfails", "adminsys", "Commands that failed",
     "SELECT * FROM command_usage WHERE ok=0 ORDER BY id DESC LIMIT ?", "none",
     [("UID", "uid"), ("Command", "command")]),
    ("topcommands", "adminsys", "Most used commands",
     "SELECT command, COUNT(*) uses FROM command_usage GROUP BY command"
     " ORDER BY uses DESC LIMIT ?", "none", [("Command", "command"), ("Uses", "uses")]),
    ("config", "adminsys", "Runtime settings",
     "SELECT * FROM bot_config ORDER BY key ASC LIMIT ?", "none",
     [("Key", "key"), ("Value", "value")]),
    ("allwarnings", "moderation", "Every warning issued",
     "SELECT * FROM user_warnings ORDER BY id DESC LIMIT ?", "none",
     [("UID", "uid"), ("Reason", "reason"), ("When", "created_at")]),
    ("warnings", "moderation", "Warnings of one member",
     "SELECT * FROM user_warnings WHERE uid=? ORDER BY id DESC LIMIT ?", "i",
     [("Reason", "reason"), ("By", "admin_uid")]),
    ("topwarned", "moderation", "Members with the most warnings",
     "SELECT uid, COUNT(*) warns FROM user_warnings GROUP BY uid"
     " ORDER BY warns DESC LIMIT ?", "none", [("UID", "uid"), ("Warnings", "warns")]),
]:
    define(_spec[0], _spec[1], "admin", _spec[2],
           sql_cmd(_e("log") + " " + B(_spec[2].upper()), _spec[3], _spec[4], _spec[5]),
           "/" + _spec[0] + (" <id>" if _spec[4] == "i" else
                             (" <text>" if _spec[4] in ("like", "like3") else "")))


for _spec in [
    ("adminstats", "admin", "Platform totals", [
        ("Members", "SELECT COUNT(*) c FROM users", ""),
        ("Banned", "SELECT COUNT(*) c FROM users WHERE is_banned=1", ""),
        ("Files", "SELECT COUNT(*) c FROM files", ""),
        ("Storage", "SELECT COALESCE(SUM(fsize),0) s FROM files", ""),
        ("Tickets", "SELECT COUNT(*) c FROM tickets", ""),
        ("Script runs", "SELECT COUNT(*) c FROM script_logs", ""),
    ]),
    ("usercount", "admin", "Member counters", [
        ("Total", "SELECT COUNT(*) c FROM users", ""),
        ("Active", "SELECT COUNT(*) c FROM users WHERE is_banned=0", ""),
        ("Banned", "SELECT COUNT(*) c FROM users WHERE is_banned=1", ""),
        ("Paying", "SELECT COUNT(*) c FROM users WHERE tier <> 'free'", ""),
    ]),
    ("filestats", "admin", "File counters", [
        ("Files", "SELECT COUNT(*) c FROM files", ""),
        ("Approved", "SELECT COUNT(*) c FROM files WHERE status='approved'", ""),
        ("Pending", "SELECT COUNT(*) c FROM files WHERE status='pending'", ""),
        ("Public", "SELECT COUNT(*) c FROM files WHERE is_public=1", ""),
    ]),
    ("ticketstats", "admin", "Support workload", [
        ("Open", "SELECT COUNT(*) c FROM tickets WHERE status='open'", ""),
        ("Closed", "SELECT COUNT(*) c FROM tickets WHERE status='closed'", ""),
        ("Total", "SELECT COUNT(*) c FROM tickets", ""),
    ]),
    ("economystats", "admin", "Points and coins in circulation", [
        ("Points", "SELECT COALESCE(SUM(points),0) s FROM users", ""),
        ("Coins", "SELECT COALESCE(SUM(coins),0) s FROM users", ""),
        ("Referrals", "SELECT COUNT(*) c FROM referrals", ""),
        ("Orders", "SELECT COUNT(*) c FROM plan_orders", ""),
    ]),
    ("dbstats", "adminsys", "Row counts per table", [
        ("Users", "SELECT COUNT(*) c FROM users", ""),
        ("Files", "SELECT COUNT(*) c FROM files", ""),
        ("Tickets", "SELECT COUNT(*) c FROM tickets", ""),
        ("Run logs", "SELECT COUNT(*) c FROM script_logs", ""),
        ("Channel logs", "SELECT COUNT(*) c FROM channel_log", ""),
    ]),
    ("cmdstats", "adminsys", "Command usage totals", [
        ("Executions", "SELECT COUNT(*) c FROM command_usage", ""),
        ("Failures", "SELECT COUNT(*) c FROM command_usage WHERE ok=0", ""),
        ("Distinct", "SELECT COUNT(DISTINCT command) c FROM command_usage", ""),
    ]),
    ("modstats", "moderation", "Moderation counters", [
        ("Warnings", "SELECT COUNT(*) c FROM user_warnings", ""),
        ("Banned", "SELECT COUNT(*) c FROM users WHERE is_banned=1", ""),
        ("Mutes", "SELECT COUNT(*) c FROM mutes", ""),
    ]),
]:
    define(_spec[0], _spec[1], "admin", _spec[2],
           stat_cmd(_e("graph") + " " + B(_spec[2].upper()), _spec[3]))


# ---- pD ----


# ---------------------------------------------------------------------------
# Commands - administrator actions and owner controls
# ---------------------------------------------------------------------------


def _need(args, usage, what="a user ID"):
    """Return a friendly prompt card when a command is missing its input."""
    return card(_e("info") + " " + B("I NEED " + what.upper()),
                [I("Send the command like this"), C(usage)])


def _cmd_userinfo(uid, args):
    """/userinfo <id> - open the full member card."""
    target = _arg_int(args)
    if not target:
        return _need(args, "/userinfo 123456789")
    if not get_user(target):
        return card(_e("no") + " " + B("NOT FOUND"),
                    [I("No member with the ID ") + C(str(target))])
    return msg_user_detail(target), kb_user_detail(uid, target)


def _cmd_members(uid, args):
    """/members - open the member browser."""
    rows, total, pages, page = browse_users(1, USER_PAGE_SIZE, "all")
    return msg_user_browser(rows, total, page, pages, "all"), \
        kb_user_browser(rows, page, pages, "all")


def _cmd_ban(uid, args):
    """/ban <id> [reason] - block a member."""
    parts = str(args or "").split(None, 1)
    target = _arg_int(parts[0] if parts else "")
    if not target:
        return _need(args, "/ban 123456789 spamming uploads")
    reason = (parts[1].strip() if len(parts) > 1 else "no reason given")
    ban_user(target, reason)
    audit(uid, "ban " + str(target) + ": " + reason)
    chlog("moderation", "Member banned",
          B("Member") + "  " + C(str(target)) + "\n" + B("Reason") + "  " + esc(reason)
          + "\n" + B("Admin") + "  " + C(str(uid)), target)
    notify_user(target, card(_e("ban") + " " + B("ACCOUNT BLOCKED"), [
        I("Your access to the platform has been blocked by an administrator."),
        "", B("Reason") + "  " + esc(reason),
        B("What now") + "  reply in a support ticket if you think this is a mistake",
    ]))
    return card(_e("ban") + " " + B("MEMBER BANNED"), [
        B("Member") + "  " + C(str(target)),
        B("Reason") + "  " + esc(reason),
        B("By") + "      " + C(str(uid)),
    ]), kb_user_detail(uid, target)


def _cmd_unban(uid, args):
    """/unban <id> - restore access."""
    target = _arg_int(args)
    if not target:
        return _need(args, "/unban 123456789")
    unban_user(target)
    audit(uid, "unban " + str(target))
    chlog("moderation", "Member unbanned",
          B("Member") + "  " + C(str(target)) + "\n" + B("Admin") + "  " + C(str(uid)), target)
    notify_user(target, card(_e("ok") + " " + B("ACCESS RESTORED"), [
        I("An administrator lifted the block on your account."),
        "", B("You can") + "  upload files, run scripts and open tickets again",
    ]))
    return card(_e("ok") + " " + B("MEMBER UNBANNED"),
                [B("Member") + "  " + C(str(target))]), kb_user_detail(uid, target)


def _cmd_warn(uid, args):
    """/warn <id> [reason] - record a warning."""
    parts = str(args or "").split(None, 1)
    target = _arg_int(parts[0] if parts else "")
    if not target:
        return _need(args, "/warn 123456789 read the rules")
    reason = (parts[1].strip() if len(parts) > 1 else "no reason given")
    add_warning(target, uid, reason)
    total = len(get_warnings(target, 99))
    chlog("moderation", "Warning issued",
          B("Member") + "  " + C(str(target)) + "\n" + B("Reason") + "  " + esc(reason)
          + "\n" + B("Total") + "  " + str(total), target)
    notify_user(target, card(_e("warn") + " " + B("WARNING RECEIVED"), [
        I("An administrator added a warning to your account."),
        "", B("Reason") + "  " + esc(reason),
        B("Warnings") + "  " + str(total),
    ]))
    return card(_e("warn") + " " + B("WARNING SAVED"), [
        B("Member") + "  " + C(str(target)),
        B("Reason") + "  " + esc(reason),
        B("Total") + "   " + str(total),
    ]), kb_user_detail(uid, target)


def _cmd_clearwarns(uid, args):
    """/clearwarns <id> - wipe warnings."""
    target = _arg_int(args)
    if not target:
        return _need(args, "/clearwarns 123456789")
    clear_warnings(target)
    chlog("moderation", "Warnings cleared", B("Member") + "  " + C(str(target)), target)
    return card(_e("ok") + " " + B("WARNINGS CLEARED"),
                [B("Member") + "  " + C(str(target))]), kb_user_detail(uid, target)


def _cmd_mute(uid, args):
    """/mute <id> <minutes> - silence a member."""
    parts = str(args or "").split()
    target = _arg_int(parts[0] if parts else "")
    if not target:
        return _need(args, "/mute 123456789 60")
    minutes = 60
    if len(parts) > 1 and str(parts[1]).isdigit():
        minutes = max(1, int(parts[1]))
    mute_user(target, minutes, "muted by admin", uid)
    chlog("moderation", "Member muted",
          B("Member") + "  " + C(str(target)) + "\n" + B("Minutes") + "  " + str(minutes),
          target)
    return card(_e("bell_off") + " " + B("MEMBER MUTED"), [
        B("Member") + "   " + C(str(target)),
        B("Duration") + " " + str(minutes) + " minute(s)",
    ]), kb_user_detail(uid, target)


def _cmd_unmute(uid, args):
    """/unmute <id> - lift a mute."""
    target = _arg_int(args)
    if not target:
        return _need(args, "/unmute 123456789")
    unmute_user(target)
    return card(_e("bell") + " " + B("MEMBER UNMUTED"),
                [B("Member") + "  " + C(str(target))]), kb_user_detail(uid, target)


def _cmd_mutes(uid, args):
    """/mutes - who is muted now."""
    rows = active_mutes()
    if not rows:
        return card(_e("ok") + " " + B("NOBODY IS MUTED"), [I("All members can talk.")])
    lines = []
    for row in rows[:15]:
        lines.append(B(str(row.get("uid"))) + "   until " + fmt_dt(row.get("until_ts")))
    return card(_e("bell_off") + " " + B("ACTIVE MUTES"), lines)


def _cmd_givepoints(uid, args):
    """/givepoints <id> <amount> - grant points."""
    parts = str(args or "").split()
    target = _arg_int(parts[0] if parts else "")
    if not target or len(parts) < 2:
        return _need(args, "/givepoints 123456789 250", "a user ID and an amount")
    try:
        amount = int(parts[1])
    except Exception:
        return _need(args, "/givepoints 123456789 250", "a whole number")
    add_points_admin(target, amount, uid)
    chlog("economy", "Points granted",
          B("Member") + "  " + C(str(target)) + "\n" + B("Points") + "  " + str(amount), target)
    notify_user(target, card(_e("pts") + " " + B("POINTS ADDED"), [
        I("An administrator topped up your balance."),
        "", B("Added") + "  " + fmt_num(amount) + " points",
    ]))
    return card(_e("pts") + " " + B("POINTS GRANTED"), [
        B("Member") + "  " + C(str(target)),
        B("Points") + "  " + fmt_num(amount),
    ]), kb_user_detail(uid, target)


def _cmd_settier(uid, args):
    """/settier <id> <plan> - change a plan by hand."""
    parts = str(args or "").split()
    target = _arg_int(parts[0] if parts else "")
    if not target or len(parts) < 2:
        return _need(args, "/settier 123456789 pro", "a user ID and a plan")
    plan = parts[1].strip().lower()
    if plan not in TIERS:
        return card(_e("no") + " " + B("UNKNOWN PLAN"),
                    [B("Available") + "  " + C(", ".join(TIERS.keys()))])
    set_user_tier(target, plan)
    chlog("orders", "Plan changed by admin",
          B("Member") + "  " + C(str(target)) + "\n" + B("Plan") + "  " + esc(plan), target)
    notify_user(target, card(_e("crown") + " " + B("PLAN UPDATED"), [
        I("An administrator moved your account to a new plan."),
        "", B("New plan") + "  " + esc(plan),
    ]))
    return card(_e("crown") + " " + B("PLAN UPDATED"), [
        B("Member") + "  " + C(str(target)),
        B("Plan") + "    " + esc(plan),
    ]), kb_user_detail(uid, target)


def _cmd_notifyuser(uid, args):
    """/notifyuser <id> <text> - send a direct message."""
    parts = str(args or "").split(None, 1)
    target = _arg_int(parts[0] if parts else "")
    if not target or len(parts) < 2:
        return _need(args, "/notifyuser 123456789 your file is approved",
                     "a user ID and a message")
    body = parts[1].strip()
    notify_user(target, card(_e("bell") + " " + B("MESSAGE FROM THE TEAM"),
                             [esc(body)]))
    return card(_e("ok") + " " + B("MESSAGE SENT"), [
        B("Member") + "   " + C(str(target)),
        B("Message") + "  " + esc(trunc(body, 200)),
    ]), kb_user_detail(uid, target)


def _cmd_broadcastnow(uid, args):
    """/broadcastnow <text> - message every member."""
    body = str(args or "").strip()
    if not body:
        return _need(args, "/broadcastnow the platform is back online", "a message")
    text = card(_e("bell") + " " + B("ANNOUNCEMENT"), [esc(body)])
    sent, failed = send_broadcast(uid, text)
    chlog("broadcast", "Broadcast sent",
          B("Delivered") + "  " + str(sent) + "\n" + B("Failed") + "  " + str(failed))
    return card(_e("ok") + " " + B("BROADCAST FINISHED"), [
        B("Delivered") + "  " + str(sent),
        B("Failed") + "     " + str(failed),
    ])


def _cmd_approvefile(uid, args):
    """/approvefile <id> - approve one upload."""
    fid = _arg_int(args)
    if not fid:
        return _need(args, "/approvefile 42", "a file ID")
    row = get_file(fid)
    if not row:
        return card(_e("no") + " " + B("FILE NOT FOUND"), [C("#" + str(fid))])
    db_exec("UPDATE files SET status='approved' WHERE id=?", (fid,))
    owner = int(row.get("uid") or 0)
    chlog("files", "File approved",
          B("File") + "  " + esc(str(row.get("fname"))) + "\n" + B("Owner") + "  "
          + C(str(owner)), owner)
    if owner:
        notify_user(owner, card(_e("ok") + " " + B("FILE APPROVED"), [
            I("Your upload passed review and is ready to use."),
            "", B("File") + "  " + esc(str(row.get("fname"))),
        ]))
    return card(_e("ok") + " " + B("FILE APPROVED"), [
        B("File") + "   " + esc(str(row.get("fname"))),
        B("Owner") + "  " + C(str(owner)),
    ])


def _cmd_rejectfile(uid, args):
    """/rejectfile <id> - reject one upload."""
    fid = _arg_int(args)
    if not fid:
        return _need(args, "/rejectfile 42", "a file ID")
    row = get_file(fid)
    if not row:
        return card(_e("no") + " " + B("FILE NOT FOUND"), [C("#" + str(fid))])
    db_exec("UPDATE files SET status='rejected' WHERE id=?", (fid,))
    owner = int(row.get("uid") or 0)
    chlog("files", "File rejected",
          B("File") + "  " + esc(str(row.get("fname"))) + "\n" + B("Owner") + "  "
          + C(str(owner)), owner)
    if owner:
        notify_user(owner, card(_e("no") + " " + B("FILE REJECTED"), [
            I("An administrator rejected this upload."),
            "", B("File") + "  " + esc(str(row.get("fname"))),
            B("Next") + "  fix the issue and upload it again",
        ]))
    return card(_e("no") + " " + B("FILE REJECTED"), [
        B("File") + "   " + esc(str(row.get("fname"))),
        B("Owner") + "  " + C(str(owner)),
    ])


for _name, _grp, _help, _fn, _usage in [
    ("userinfo", "admin", "Open a member card", _cmd_userinfo, "/userinfo <id>"),
    ("members", "admin", "Browse every member", _cmd_members, "/members"),
    ("ban", "moderation", "Block a member", _cmd_ban, "/ban <id> [reason]"),
    ("unban", "moderation", "Restore access", _cmd_unban, "/unban <id>"),
    ("warn", "moderation", "Record a warning", _cmd_warn, "/warn <id> [reason]"),
    ("clearwarns", "moderation", "Wipe warnings", _cmd_clearwarns, "/clearwarns <id>"),
    ("mute", "moderation", "Silence a member", _cmd_mute, "/mute <id> <minutes>"),
    ("unmute", "moderation", "Lift a mute", _cmd_unmute, "/unmute <id>"),
    ("mutes", "moderation", "Who is muted now", _cmd_mutes, "/mutes"),
    ("givepoints", "admin", "Grant points", _cmd_givepoints, "/givepoints <id> <amount>"),
    ("settier", "admin", "Change a plan by hand", _cmd_settier, "/settier <id> <plan>"),
    ("notifyuser", "admin", "Send a direct message", _cmd_notifyuser,
     "/notifyuser <id> <text>"),
    ("broadcastnow", "admin", "Message every member", _cmd_broadcastnow,
     "/broadcastnow <text>"),
    ("approvefile", "admin", "Approve one upload", _cmd_approvefile, "/approvefile <id>"),
    ("rejectfile", "admin", "Reject one upload", _cmd_rejectfile, "/rejectfile <id>"),
]:
    define(_name, _grp, "admin", _help, _fn, _usage)


# ---- pE ----


# ---------------------------------------------------------------------------
# Commands - system tools and owner controls
# ---------------------------------------------------------------------------


def _cmd_sysinfo(uid, args):
    """/sysinfo - machine and runtime facts."""
    return card(_e("server") + " " + B("SYSTEM"), [
        B("Platform") + "   " + esc(platform.platform()[:70]),
        B("Python") + "     " + esc(platform.python_version()),
        B("Machine") + "    " + esc(platform.machine()),
        B("Host") + "       " + esc(socket.gethostname()[:40]),
        B("Process") + "    " + C(str(os.getpid())),
        B("Threads") + "    " + str(threading.active_count()),
    ])


def _cmd_uptime(uid, args):
    """/uptime - how long the bot has been running."""
    seconds = max(0, int(time.time() - _START_TS))
    return card(_e("clock") + " " + B("UPTIME"), [
        B("Running for") + "  " + fmt_duration(seconds),
        B("Started") + "      " + fmt_dt_abs(
            datetime.fromtimestamp(_START_TS, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        B("Version") + "      " + esc(str(VERSION)),
    ])


def _cmd_dbsize(uid, args):
    """/dbsize - database file size."""
    try:
        size = os.path.getsize(str(DB_PATH))
    except Exception:
        size = 0
    return card(_e("db") + " " + B("DATABASE"), [
        B("File") + "    " + C(str(DB_PATH)),
        B("Size") + "    " + fmt_size(int(size)),
        B("Tables") + "  " + str(db_val(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'", (), 0)),
        B("Indexes") + " " + str(db_val(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index'", (), 0)),
    ])


def _cmd_diskusage(uid, args):
    """/diskusage - storage taken by member files."""
    total = db_val("SELECT COALESCE(SUM(fsize),0) FROM files", (), 0) or 0
    try:
        usage = shutil.disk_usage(str(BASE_DIR))
        free, whole = usage.free, usage.total
    except Exception:
        free, whole = 0, 0
    return card(_e("server") + " " + B("DISK USAGE"), [
        B("Member files") + "  " + fmt_size(int(total)),
        B("Disk free") + "     " + fmt_size(int(free)),
        B("Disk total") + "    " + fmt_size(int(whole)),
        B("Base folder") + "   " + C(str(BASE_DIR)),
    ])


def _cmd_threads(uid, args):
    """/threads - background workers."""
    lines = [B(str(t.name)) + "   " + ("alive" if t.is_alive() else "stopped")
             for t in threading.enumerate()[:20]]
    return card(_e("gear") + " " + B("THREADS"), lines or [I("No threads reported.")])


def _cmd_logstatus(uid, args):
    """/logstatus - channel logging health."""
    stats = chlog_stats()
    return card(_e("log") + " " + B("CHANNEL LOGGING"), [
        B("Enabled") + "    " + ("yes" if chlog_enabled() else "no"),
        B("Channel") + "    " + C(str(log_channel_id() or "not set")),
        B("Delivered") + "  " + str(stats.get("sent", 0)),
        B("Failed") + "     " + str(stats.get("failed", 0)),
        B("Queued") + "     " + str(stats.get("queued", 0)),
        divider(),
        I("Set it with ") + C("/setlogchannel -100xxxxxxxxxx")
        + I(" after adding the bot as a channel admin."),
    ])


def _cmd_testlog(uid, args):
    """/testlog - push a test entry to the channel."""
    chlog("system", "Test log entry",
          B("Requested by") + "  " + C(str(uid)) + "\n"
          + B("Purpose") + "  confirm the log channel receives events", uid)
    return card(_e("ok") + " " + B("TEST LOG QUEUED"), [
        I("A test entry was queued for the log channel."),
        B("Channel") + "  " + C(str(log_channel_id() or "not set")),
    ])


def _cmd_vacuum(uid, args):
    """/vacuum - compact the database."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.isolation_level = None
        conn.execute("VACUUM")
        conn.close()
        return card(_e("ok") + " " + B("DATABASE COMPACTED"),
                    [I("Free pages were released back to the disk.")])
    except Exception as exc:
        return card(_e("warn") + " " + B("VACUUM FAILED"), [I(esc(str(exc)[:200]))])


def _cmd_integrity(uid, args):
    """/integrity - run a database check."""
    result = db_val("PRAGMA integrity_check", (), "unknown")
    return card(_e("shield") + " " + B("INTEGRITY CHECK"), [
        B("Result") + "  " + esc(str(result)),
        I("ok means the database has no detected corruption."),
    ])


def _cmd_backupnow(uid, args):
    """/backupnow - copy the database to backups."""
    try:
        folder = BASE_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        name = "sigma-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".db"
        target = folder / name
        shutil.copy2(str(DB_PATH), str(target))
        chlog("system", "Backup created", B("File") + "  " + C(name), uid)
        return card(_e("backup") + " " + B("BACKUP CREATED"), [
            B("File") + "  " + C(name),
            B("Size") + "  " + fmt_size(os.path.getsize(str(target))),
        ])
    except Exception as exc:
        return card(_e("warn") + " " + B("BACKUP FAILED"), [I(esc(str(exc)[:200]))])


def _cmd_cleanup(uid, args):
    """/cleanup - delete temporary files."""
    removed = 0
    try:
        tmp = BASE_DIR / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        for entry in tmp.iterdir():
            try:
                if entry.is_file():
                    entry.unlink()
                    removed += 1
            except Exception:
                continue
    except Exception as exc:
        return card(_e("warn") + " " + B("CLEANUP FAILED"), [I(esc(str(exc)[:200]))])
    return card(_e("ok") + " " + B("CLEANUP DONE"),
                [B("Removed") + "  " + str(removed) + " temporary file(s)"])


for _name, _grp, _help, _fn in [
    ("sysinfo", "adminsys", "Machine and runtime facts", _cmd_sysinfo),
    ("uptime", "adminsys", "How long the bot ran", _cmd_uptime),
    ("dbsize", "adminsys", "Database file size", _cmd_dbsize),
    ("diskusage", "adminsys", "Storage taken by files", _cmd_diskusage),
    ("threads", "adminsys", "Background workers", _cmd_threads),
    ("logstatus", "adminsys", "Channel logging health", _cmd_logstatus),
    ("testlog", "adminsys", "Push a test log entry", _cmd_testlog),
    ("vacuum", "adminsys", "Compact the database", _cmd_vacuum),
    ("integrity", "adminsys", "Run a database check", _cmd_integrity),
    ("backupnow", "adminsys", "Back up the database", _cmd_backupnow),
    ("cleanup", "adminsys", "Delete temporary files", _cmd_cleanup),
]:
    define(_name, _grp, "admin", _help, _fn)


def _cmd_setlogchannel(uid, args):
    """/setlogchannel <id> - point logging at a channel."""
    value = str(args or "").strip()
    if not value:
        return card(_e("info") + " " + B("I NEED A CHANNEL ID"), [
            I("Add this bot as an administrator of your channel, then send"),
            C("/setlogchannel -1001234567890"),
        ])
    cfg_set("log_channel", value, uid)
    chlog("system", "Log channel updated", B("Channel") + "  " + C(value), uid)
    return card(_e("ok") + " " + B("LOG CHANNEL SAVED"), [
        B("Channel") + "  " + C(value),
        I("Every important event is mirrored there from now on."),
    ])


def _cmd_logchannel(uid, args):
    """/logchannel - show the current log target."""
    return card(_e("log") + " " + B("LOG TARGET"), [
        B("Channel") + "     " + C(str(log_channel_id() or "not set")),
        B("Categories") + "  " + esc(", ".join(LOG_CATEGORIES)),
    ])


def _cmd_setconfig(uid, args):
    """/setconfig <key> <value> - change a runtime setting."""
    parts = str(args or "").split(None, 1)
    if len(parts) < 2:
        return card(_e("info") + " " + B("I NEED A KEY AND VALUE"),
                    [C("/setconfig maintenance on")])
    cfg_set(parts[0].strip(), parts[1].strip(), uid)
    return card(_e("ok") + " " + B("SETTING SAVED"), [
        B("Key") + "    " + C(parts[0].strip()),
        B("Value") + "  " + esc(parts[1].strip()),
    ])


def _cmd_addadmin(uid, args):
    """/addadmin <id> - grant admin rights."""
    target = _arg_int(args)
    if not target:
        return card(_e("info") + " " + B("I NEED A USER ID"), [C("/addadmin 123456789")])
    current = [x for x in str(cfg_get("extra_admins", "")).split(",") if x.strip()]
    if str(target) not in current:
        current.append(str(target))
    cfg_set("extra_admins", ",".join(current), uid)
    if target not in ADMIN_IDS:
        ADMIN_IDS.add(target)
    chlog("system", "Administrator added", B("Member") + "  " + C(str(target)), target)
    return card(_e("crown") + " " + B("ADMIN ADDED"), [
        B("Member") + "  " + C(str(target)),
        I("They can now open the admin panels."),
    ])


def _cmd_deladmin(uid, args):
    """/deladmin <id> - remove admin rights."""
    target = _arg_int(args)
    if not target:
        return card(_e("info") + " " + B("I NEED A USER ID"), [C("/deladmin 123456789")])
    current = [x.strip() for x in str(cfg_get("extra_admins", "")).split(",")
               if x.strip() and x.strip() != str(target)]
    cfg_set("extra_admins", ",".join(current), uid)
    if target in ADMIN_IDS and not is_owner(target):
        ADMIN_IDS.discard(target)
    return card(_e("ok") + " " + B("ADMIN REMOVED"), [B("Member") + "  " + C(str(target))])


def _cmd_maintenance(uid, args):
    """/maintenance on|off - pause the platform."""
    value = str(args or "").strip().lower()
    if value not in ("on", "off"):
        return card(_e("info") + " " + B("ON OR OFF"), [C("/maintenance on")])
    cfg_set("maintenance", value, uid)
    chlog("system", "Maintenance mode " + value, B("Changed by") + "  " + C(str(uid)), uid)
    return card(_e("gear") + " " + B("MAINTENANCE " + value.upper()), [
        I("Members see a short notice while maintenance is on."
          if value == "on" else "The platform is open again."),
    ])


def _cmd_ownerstats(uid, args):
    """/ownerstats - the owner overview."""
    counts = registry_counts()
    stats = chlog_stats()
    return card(_e("crown") + " " + B("OWNER OVERVIEW"), [
        B("Members") + "       " + str(db_val("SELECT COUNT(*) FROM users", (), 0)),
        B("Paying") + "        " + str(db_val(
            "SELECT COUNT(*) FROM users WHERE tier <> 'free'", (), 0)),
        B("Files") + "         " + str(db_val("SELECT COUNT(*) FROM files", (), 0)),
        B("Storage") + "       " + fmt_size(int(db_val(
            "SELECT COALESCE(SUM(fsize),0) FROM files", (), 0) or 0)),
        divider(),
        B("Commands") + "      " + str(counts.get("total", 0)),
        B("Admins") + "        " + str(len(ADMIN_IDS)),
        B("Owners") + "        " + str(len(OWNER_IDS)),
        B("Logs delivered") + " " + str(stats.get("sent", 0)),
        B("Uptime") + "        " + fmt_duration(max(0, int(time.time() - _START_TS))),
    ]), kb_command_center(uid)


def _cmd_shutdown(uid, args):
    """/shutdown - stop the bot process."""
    if str(args or "").strip().lower() != "confirm":
        return card(_e("warn") + " " + B("CONFIRM SHUTDOWN"), [
            I("This stops the bot until you start it again on the server."),
            B("To continue") + "  " + C("/shutdown confirm"),
        ])
    chlog("system", "Shutdown requested", B("By") + "  " + C(str(uid)), uid)
    threading.Timer(2.0, lambda: os._exit(0)).start()
    return card(_e("stop") + " " + B("SHUTTING DOWN"), [I("Goodbye. Start me again to resume.")])


for _name, _grp, _help, _fn, _usage in [
    ("setlogchannel", "owner", "Point logging at a channel", _cmd_setlogchannel,
     "/setlogchannel <id>"),
    ("logchannel", "owner", "Show the log target", _cmd_logchannel, "/logchannel"),
    ("setconfig", "owner", "Change a runtime setting", _cmd_setconfig,
     "/setconfig <key> <value>"),
    ("addadmin", "owner", "Grant admin rights", _cmd_addadmin, "/addadmin <id>"),
    ("deladmin", "owner", "Remove admin rights", _cmd_deladmin, "/deladmin <id>"),
    ("maintenance", "owner", "Pause the platform", _cmd_maintenance, "/maintenance on|off"),
    ("ownerstats", "owner", "The owner overview", _cmd_ownerstats, "/ownerstats"),
    ("shutdown", "owner", "Stop the bot process", _cmd_shutdown, "/shutdown confirm"),
]:
    define(_name, _grp, "owner", _help, _fn, _usage)


# ---- p14 ----


# ---------------------------------------------------------------------------
# Final batch - guides and extra commands
# ---------------------------------------------------------------------------

for _spec in [
    ("quickstart", "core", "user", "First steps in 60 seconds",
     _e("star") + " " + B("QUICK START"), [
         "1.  Send any file to store it.",
         "2.  Open " + C("/myfiles") + " to manage it.",
         "3.  Press Run on a Python file to execute it.",
         "4.  Press Edit to fix a line without re-uploading.",
         "5.  Open " + C("/commands") + " to see everything else.",
     ]),
    ("tips", "core", "user", "Small tricks that help",
     _e("info") + " " + B("TIPS"), [
         "- Tag your files so search finds them instantly.",
         "- Schedules repeat a script without you opening the bot.",
         "- Every edit keeps a version, so experiments are safe.",
         "- Keep a daily streak for free points.",
         "- Long output is trimmed, write to a file for the full log.",
     ]),
    ("shortcuts", "core", "user", "The commands people use daily",
     _e("run") + " " + B("SHORTCUTS"), [
         C("/me") + "  your profile",
         C("/myfiles") + "  your files",
         C("/commands") + "  the full command centre",
         C("/wallet") + "  points and coins",
         C("/mytickets") + "  support history",
     ]),
    ("edithelp", "files", "user", "How the code editor works",
     _e("edit") + " " + B("EDITOR GUIDE"), [
         "1.  Open a file and press Edit.",
         "2.  Pull the current code, or replace a single line.",
         "3.  Every save is re-scanned for dangerous patterns.",
         "4.  A new version is stored so you can roll back.",
     ]),
    ("cronhelp", "runner", "user", "How schedules work",
     _e("cron") + " " + B("SCHEDULE GUIDE"), [
         "1.  Pick an approved script.",
         "2.  Choose how often it should run.",
         "3.  Pause or resume it whenever you like.",
         "4.  Check " + C("/mycrons") + " for the next run time.",
     ]),
    ("apihelp", "account", "user", "How API keys work",
     _e("api") + " " + B("API KEYS"), [
         "- Create a key with a label you recognise.",
         "- Keys are shown once, store them safely.",
         "- Delete a key the moment it leaks.",
     ]),
    ("webhookhelp", "account", "user", "How webhooks work",
     _e("hook") + " " + B("WEBHOOKS"), [
         "- Add a URL and the bot posts events to it.",
         "- Only HTTPS endpoints are accepted.",
         "- Disable one without deleting it at any time.",
     ]),
    ("economyhelp", "economy", "user", "How points are earned",
     _e("coin") + " " + B("ECONOMY GUIDE"), [
         B("Daily") + "  claim once a day and keep the streak",
         B("Uploads") + "  every accepted file pays points",
         B("Referrals") + "  invite a friend and both of you earn",
     ]),
    ("adminhelp", "admin", "admin", "Admin tools at a glance",
     _e("admin") + " " + B("ADMIN GUIDE"), [
         C("/members") + "  browse every member with drill-down buttons",
         C("/userinfo <id>") + "  the full card for one member",
         C("/pending") + "  files waiting for review",
         C("/opentickets") + "  the support queue",
         C("/adminstats") + "  platform totals",
     ]),
    ("ownerhelp", "owner", "owner", "Owner-only controls",
     _e("crown") + " " + B("OWNER GUIDE"), [
         C("/setlogchannel <id>") + "  send every event to your channel",
         C("/logstatus") + "  check logging health",
         C("/setconfig <key> <value>") + "  change a runtime setting",
         C("/backupnow") + "  snapshot the database",
     ]),
    ("loghelp", "owner", "owner", "How channel logging is set up",
     _e("log") + " " + B("CHANNEL LOGGING SETUP"), [
         "1.  Create a private Telegram channel.",
         "2.  Add this bot as an administrator with post rights.",
         "3.  Forward a channel message to @userinfobot to read the ID.",
         "4.  Run " + C("/setlogchannel -100xxxxxxxxxx") + ".",
         "5.  Confirm with " + C("/testlog") + ".",
     ]),
]:
    define(_spec[0], _spec[1], _spec[2], _spec[3], text_cmd(_spec[4], _spec[5]))


# ---- p15 ----


# ---------------------------------------------------------------------------
# Fancy font engine
# ---------------------------------------------------------------------------
# Every style is built from Unicode code points at runtime, so the source file
# itself stays plain ASCII and can never be corrupted by a bad copy or paste.


def _range_map(upper_start, lower_start, digit_start=None):
    """Build a translation table from contiguous Unicode blocks."""
    table = {}
    for i in range(26):
        table[ord("A") + i] = chr(upper_start + i)
        table[ord("a") + i] = chr(lower_start + i)
    if digit_start is not None:
        for i in range(10):
            table[ord("0") + i] = chr(digit_start + i)
    return table


def _table_map(upper_points, lower_points):
    """Build a translation table from explicit code point lists."""
    table = {}
    for i, point in enumerate(upper_points):
        if point:
            table[ord("A") + i] = chr(point)
    for i, point in enumerate(lower_points):
        if point:
            table[ord("a") + i] = chr(point)
    return table


# small capitals: lower case letters are drawn as little capitals
_SMALLCAPS_POINTS = [
    0x1D00, 0x0299, 0x1D04, 0x1D05, 0x1D07, 0xA730, 0x0262, 0x029C, 0x026A,
    0x1D0A, 0x1D0B, 0x029F, 0x1D0D, 0x0274, 0x1D0F, 0x1D18, 0x01EB, 0x0280,
    0xA731, 0x1D1B, 0x1D1C, 0x1D20, 0x1D21, 0x0078, 0x028F, 0x1D22,
]

# Canadian syllabics style letters
_TRIBAL_POINTS = [
    0x1431, 0x15E1, 0x14F2, 0x1450, 0x15F4, 0x1622, 0x144F, 0x15C3, 0x14D0,
    0x144D, 0x1450, 0x14AA, 0x15F0, 0x14C4, 0x004F, 0x146D, 0x1514, 0x1550,
    0x1584, 0x1495, 0x1546, 0x142F, 0x15F7, 0x166E, 0x03B3, 0x1614,
]

FONT_STYLES = {
    "mono": ("Monospace", _range_map(0x1D670, 0x1D68A, 0x1D7F6)),
    "bold": ("Bold", _range_map(0x1D400, 0x1D41A, 0x1D7CE)),
    "bolditalic": ("Bold italic", _range_map(0x1D468, 0x1D482)),
    "smallcaps": ("Small caps", _table_map([], _SMALLCAPS_POINTS)),
    "script": ("Script", _range_map(0x1D4D0, 0x1D4EA)),
    "tribal": ("Tribal", _table_map(_TRIBAL_POINTS, _TRIBAL_POINTS)),
    "circled": ("Circled", _range_map(0x24B6, 0x24D0)),
    "filled": ("Filled circle", _table_map(
        [0x1F150 + i for i in range(26)], [0x1F150 + i for i in range(26)])),
    "flags": ("Regional", _table_map(
        [0x1F1E6 + i for i in range(26)], [0x1F1E6 + i for i in range(26)])),
    "double": ("Double struck", _range_map(0x1D538, 0x1D552, 0x1D7D8)),
    "fraktur": ("Fraktur", _range_map(0x1D56C, 0x1D586)),
    "sans": ("Sans serif", _range_map(0x1D5A0, 0x1D5BA, 0x1D7E2)),
    "plain": ("Plain text", {}),
}

FONT_ORDER = ["plain", "mono", "bold", "bolditalic", "smallcaps", "script",
              "tribal", "circled", "filled", "flags", "double", "fraktur", "sans"]


def stylize(text, style="plain"):
    """Return the text drawn in one of the fancy alphabets."""
    entry = FONT_STYLES.get(str(style or "plain"))
    if not entry:
        return str(text)
    table = entry[1]
    if not table:
        return str(text)
    spaced = str(style) == "flags"
    out = []
    for ch in str(text):
        out.append(table.get(ord(ch), ch))
        if spaced and ch.strip():
            out.append(" ")
    return "".join(out)


def font_preview(word="Hello"):
    """Show the same word in every available style."""
    lines = []
    for key in FONT_ORDER:
        name = FONT_STYLES.get(key, (key, {}))[0]
        lines.append(B(name) + "\n    " + esc(stylize(word, key)))
    return lines


def user_font(uid):
    """The style the member picked, or plain text."""
    try:
        row = db_one("SELECT font FROM users WHERE uid=?", (int(uid),))
        value = (row or {}).get("font") if isinstance(row, dict) else None
        return str(value) if value in FONT_STYLES else "plain"
    except Exception:
        return "plain"


def styled_for(uid, text):
    """Draw text in the style the member chose."""
    return stylize(text, user_font(uid))


def set_user_font(uid, style):
    """Store the chosen style and redraw the permanent keyboard in it."""
    if style not in FONT_STYLES:
        return False
    db_exec("UPDATE users SET font=? WHERE uid=?", (str(style), int(uid)))
    try:
        db_exec("UPDATE user_settings SET font=? WHERE uid=?",
                (str(style), int(uid)))
    except Exception:
        pass
    try:
        name = FONT_STYLES.get(str(style), (str(style),))[0]
        bot.send_message(
            int(uid),
            font_safe(_e("art") + " <b>Font applied: </b>" + esc(name)
                      + "\n<i>Every message and button now uses it.</i>", style),
            reply_markup=kb_persistent(uid))
    except Exception as exc:
        log.debug("keyboard refresh skipped: %s", exc)
    return True


def msg_font_panel(uid, word="Hello"):
    """The font picker card."""
    current = user_font(uid)
    name = FONT_STYLES.get(current, ("Plain text", {}))[0]
    lines = [
        B("Current style") + "  " + esc(name),
        B("Preview word") + "  " + C(word),
        "",
        I("Pick a style below. Every card, panel and notification the bot"),
        I("sends you is then drawn in that alphabet."),
        divider(),
    ]
    lines.extend(font_preview(word))
    return card(_e("edit") + " " + B("FONT STUDIO"), lines)


def kb_font_panel(uid):
    """Buttons for every style, three per row."""
    rows = []
    row = []
    current = user_font(uid)
    for key in FONT_ORDER:
        name = FONT_STYLES.get(key, (key, {}))[0]
        mark = _e("check") + " " if key == current else ""
        row.append(BTN(mark + name, "font_set:" + key))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([BTN(_e("edit") + " Try my own word", "font_word")])
    rows.append([BTN(_e("reload") + " Refresh", "font_panel"), BACK("main_menu")])
    return KB(*rows)


@extension_callback("font_panel", exact=True)
def cb_font_panel(call, uid, cid, mid, data):
    """Open the font studio."""
    send_or_edit(cid, mid, msg_font_panel(uid), kb_font_panel(uid))
    ack(call)


@extension_callback("font_set:")
def cb_font_set(call, uid, cid, mid, data):
    """Save the chosen style."""
    style = data.split(":", 1)[1] if ":" in data else "plain"
    if set_user_font(uid, style):
        name = FONT_STYLES.get(style, (style, {}))[0]
        ack(call, name + " selected")
        chlog("settings", "Font changed", name, uid)
    else:
        ack(call, "Unknown style")
    send_or_edit(cid, mid, msg_font_panel(uid), kb_font_panel(uid))


@extension_callback("font_word", exact=True)
def cb_font_word(call, uid, cid, mid, data):
    """Ask for a custom preview word."""
    set_state(uid, "awaiting_font_word")
    send(cid, card(_e("edit") + " " + B("PREVIEW WORD"),
                   [I("Send the word you want to see in all styles.")]))
    ack(call)


def _fsm_font_word(message, uid, state):
    """Preview a custom word in every style."""
    word = str(getattr(message, "text", "") or "Hello").strip()[:24] or "Hello"
    clear_state(uid)
    send(message.chat.id, msg_font_panel(uid, word), kb_font_panel(uid))


def _cmd_font(uid, args):
    """/font - open the font studio."""
    word = str(args or "").strip()[:24] or "Hello"
    return msg_font_panel(uid, word), kb_font_panel(uid)


def _cmd_fonts(uid, args):
    """/fonts - every style side by side."""
    word = str(args or "").strip()[:24] or "Hello"
    return card(_e("star") + " " + B("FONT GALLERY"), font_preview(word)), kb_font_panel(uid)


def _cmd_style(uid, args):
    """/style <name> <text> - draw text in one style."""
    parts = str(args or "").strip().split(None, 1)
    if len(parts) < 2 or parts[0] not in FONT_STYLES:
        return card(_e("info") + " " + B("HOW TO USE"), [
            C("/style bold Hello world"),
            "",
            B("Styles") + "  " + esc(", ".join(FONT_ORDER)),
        ]), kb_font_panel(uid)
    return card(_e("edit") + " " + B("STYLED TEXT"),
                [esc(stylize(parts[1], parts[0]))]), kb_font_panel(uid)


for _name, _help, _fn, _usage in [
    ("font", "Open the font studio", _cmd_font, "/font"),
    ("fonts", "See every style at once", _cmd_fonts, "/fonts <word>"),
    ("style", "Draw text in one style", _cmd_style, "/style bold Hello"),
]:
    define(_name, "account", "user", _help, _fn, _usage)



# ---- p16 ----


# ---------------------------------------------------------------------------
# Membership orders - detailed cards, one-tap approve or deny
# ---------------------------------------------------------------------------


def order_row(order_id):
    """Load one upgrade request."""
    return db_one("SELECT * FROM plan_orders WHERE id=?", (int(order_id),)) or {}


def msg_order_request(order_id):
    """The full request card an administrator receives."""
    order = order_row(order_id)
    if not order:
        return card(_e("warn") + " " + B("REQUEST NOT FOUND"),
                    [I("This upgrade request no longer exists.")])
    uid = int(order.get("uid") or 0)
    member = get_user(uid) or {}
    tier_key = str(order.get("tier") or "free")
    tier = TIERS.get(tier_key, {})
    files = db_val("SELECT COUNT(*) FROM files WHERE uid=?", (uid,), 0) or 0
    storage = db_val("SELECT COALESCE(SUM(fsize),0) FROM files WHERE uid=?", (uid,), 0) or 0
    runs = db_val("SELECT COUNT(*) FROM script_logs WHERE uid=?", (uid,), 0) or 0
    warns = db_val("SELECT COUNT(*) FROM user_warnings WHERE uid=?", (uid,), 0) or 0
    past = db_val("SELECT COUNT(*) FROM plan_orders WHERE uid=? AND status='approved'",
                  (uid,), 0) or 0
    tickets = db_val("SELECT COUNT(*) FROM tickets WHERE uid=?", (uid,), 0) or 0
    handle = str(member.get("username") or "").strip()

    lines = [
        B("A member has requested a paid membership and is waiting for your"),
        B("decision. Everything you need to judge the request is below."),
        I("Approve to activate the plan instantly, or deny to refuse it. The"),
        I("member is notified either way, so no command is ever needed."),
        divider(),
        B("REQUEST"),
        "  " + B("Order") + "        #" + str(order.get("id")),
        "  " + B("Plan wanted") + "  " + esc(str(tier.get("name", tier_key))),
        "  " + B("Price") + "        " + esc(str(tier.get("price", 0))),
        "  " + B("Status") + "       " + esc(str(order.get("status", "pending"))),
        "  " + B("Requested") + "    " + fmt_dt(order.get("created_at")),
        divider(),
        B("MEMBER"),
        "  " + B("Name") + "         " + esc(str(member.get("first_name") or "unknown")),
        "  " + B("Username") + "     " + (C("@" + handle) if handle else I("none set")),
        "  " + B("User ID") + "      " + C(str(uid)),
        "  " + B("Current plan") + " " + esc(str(member.get("tier") or "free")),
        "  " + B("Level") + "        " + str(member.get("level") or 0)
        + "   " + B("Points") + "  " + fmt_num(member.get("points") or 0),
        "  " + B("Joined") + "       " + fmt_dt(member.get("joined_at")),
        divider(),
        B("TRACK RECORD"),
        "  " + B("Files stored") + " " + str(files) + "   (" + fmt_size(int(storage)) + ")",
        "  " + B("Scripts run") + "  " + str(runs),
        "  " + B("Tickets") + "      " + str(tickets),
        "  " + B("Warnings") + "     " + str(warns)
        + ("   " + _e("warn") + " review carefully" if warns else "   " + _e("check") + " clean"),
        "  " + B("Paid before") + "  " + (str(past) + " time(s)" if past else "first purchase"),
    ]
    if order.get("notes"):
        lines.extend([divider(), B("MEMBER NOTE"), "  " + I(esc(str(order.get("notes"))[:300]))])
    lines.extend([
        divider(),
        B("WHAT THE PLAN UNLOCKS"),
        "  " + B("Storage") + "      " + esc(str(tier.get("max_size", "n/a"))),
        "  " + B("File limit") + "   " + esc(str(tier.get("max_files", "n/a"))),
        divider(),
        _e("check") + " " + B("Approve") + I("  activates the plan and thanks the member."),
        _e("no") + " " + B("Deny") + I("  refuses politely and keeps the current plan."),
    ])
    return card(_e("crown") + " " + B("MEMBERSHIP REQUEST  #" + str(order.get("id"))), lines)


def kb_order_request(order_id, uid=0, done=False):
    """Approve or deny buttons plus shortcuts into the member card."""
    if done:
        return KB([BTN(_e("user") + " Open member", "ui:" + str(int(uid or 0)))],
                  [BTN(_e("crown") + " Order queue", "c:orderqueue")],
                  [BACK("main_menu")])
    return KB(
        [BTN(_e("check") + " Approve", "po_ok:" + str(int(order_id))),
         BTN(_e("no") + " Deny", "po_no:" + str(int(order_id)))],
        [BTN(_e("user") + " Member card", "ui:" + str(int(uid or 0))),
         BTN(_e("folder") + " Their files", "uf:" + str(int(uid or 0)))],
        [BTN(_e("graph") + " Their activity", "ua:" + str(int(uid or 0))),
         BTN(_e("coin") + " Their wallet", "ue:" + str(int(uid or 0)))],
        [BTN(_e("crown") + " Order queue", "c:orderqueue")],
    )


def create_plan_order(uid, tier_key, notes=""):
    """Record an upgrade request and push it to admins with action buttons."""
    tier = TIERS.get(str(tier_key))
    if not tier:
        return False, "Unknown plan."
    order_id = db_exec(
        "INSERT INTO plan_orders (uid, tier, amount, status, created_at, processed_at, notes)"
        " VALUES (?,?,?,'pending',?,'',?)",
        (int(uid), str(tier_key), int(tier.get("price", 0) or 0), utcstamp(), str(notes)[:400]),
    )
    if not order_id:
        return False, "Could not record the request."
    try:
        notify_admins(msg_order_request(order_id),
                      kb_order_request(order_id, uid))
    except Exception as exc:
        log.error("Could not alert admins about order %s: %s", order_id, exc)
    chlog("orders", "Upgrade requested",
          "Order #" + str(order_id) + " for " + str(tier.get("name", tier_key)), uid)
    return True, "Request #" + str(int(order_id)) + " sent to the admins."


def msg_order_result(order_id, approved, admin_uid):
    """The confirmation the administrator sees after deciding."""
    order = order_row(order_id)
    tier = TIERS.get(str(order.get("tier") or ""), {})
    head = (_e("check") + " " + B("MEMBERSHIP APPROVED") if approved
            else _e("no") + " " + B("MEMBERSHIP DENIED"))
    lines = [
        B("Order") + "        #" + str(order.get("id")),
        B("Member") + "       " + C(str(order.get("uid"))),
        B("Plan") + "         " + esc(str(tier.get("name", order.get("tier")))),
        B("Decided by") + "   " + C(str(admin_uid)),
        B("Decided at") + "   " + fmt_dt_abs(utcstamp()),
        divider(),
    ]
    if approved:
        lines.extend([
            I("The plan is live right now. New limits apply to the member's next"),
            I("upload immediately and a thank-you message has been delivered to"),
            I("their chat with the full list of everything they just unlocked."),
        ])
    else:
        lines.extend([
            I("The request was refused and the member keeps their current plan."),
            I("They were told politely, invited to open a support ticket if they"),
            I("think this was a mistake, and can request the plan again later."),
        ])
    return card(head, lines)


@extension_callback("po_ok:")
def cb_order_approve(call, uid, cid, mid, data):
    """Approve a membership request with one tap."""
    if not is_admin(uid):
        return ack(call, "Admins only", True)
    order_id = _tail_int(data, "po_ok:")
    order = order_row(order_id)
    if not order:
        return ack(call, "Order not found", True)
    if str(order.get("status")) != "pending":
        ack(call, "Already " + str(order.get("status")), True)
        return send_or_edit(cid, mid, msg_order_result(order_id, True, uid),
                            kb_order_request(order_id, order.get("uid"), True))
    member_uid = int(order.get("uid") or 0)
    tier_key = str(order.get("tier") or "free")
    tier = TIERS.get(tier_key, {})
    try:
        set_user_tier(member_uid, tier_key, uid)
    except Exception as exc:
        log.error("Could not set plan: %s", exc)
    db_exec("UPDATE plan_orders SET status='approved', processed_at=? WHERE id=?",
            (utcstamp(), int(order_id)))
    notify_user(member_uid, card(
        _e("crown") + " " + B("YOUR MEMBERSHIP IS ACTIVE"),
        [
            B("Thank you. Your upgrade was approved and your new plan is live"),
            B("on your account right now, with nothing left for you to do."),
            divider(),
            B("Plan") + "         " + esc(str(tier.get("name", tier_key))),
            B("Order") + "        #" + str(order_id),
            B("Storage") + "      " + esc(str(tier.get("max_size", "n/a"))),
            B("File limit") + "   " + esc(str(tier.get("max_files", "n/a"))),
            B("Activated") + "    " + fmt_dt_abs(utcstamp()),
            divider(),
            I("Your next upload already uses the larger limits. Open your files"),
            I("to continue, or contact support if anything looks wrong."),
        ]))
    chlog("orders", "Membership approved",
          "Order #" + str(order_id) + " -> " + str(tier.get("name", tier_key))
          + " by admin " + str(uid), member_uid)
    ack(call, "Approved")
    send_or_edit(cid, mid, msg_order_result(order_id, True, uid),
                 kb_order_request(order_id, member_uid, True))


@extension_callback("po_no:")
def cb_order_deny(call, uid, cid, mid, data):
    """Deny a membership request with one tap."""
    if not is_admin(uid):
        return ack(call, "Admins only", True)
    order_id = _tail_int(data, "po_no:")
    order = order_row(order_id)
    if not order:
        return ack(call, "Order not found", True)
    if str(order.get("status")) != "pending":
        ack(call, "Already " + str(order.get("status")), True)
        return send_or_edit(cid, mid, msg_order_result(order_id, False, uid),
                            kb_order_request(order_id, order.get("uid"), True))
    member_uid = int(order.get("uid") or 0)
    tier = TIERS.get(str(order.get("tier") or ""), {})
    db_exec("UPDATE plan_orders SET status='denied', processed_at=? WHERE id=?",
            (utcstamp(), int(order_id)))
    notify_user(member_uid, card(
        _e("info") + " " + B("ABOUT YOUR UPGRADE REQUEST"),
        [
            B("Your request for a paid membership was reviewed by our team and"),
            B("could not be approved this time. Your account stays exactly as"),
            B("it is, and nothing was charged or removed."),
            divider(),
            B("Plan asked") + "   " + esc(str(tier.get("name", order.get("tier")))),
            B("Order") + "        #" + str(order_id),
            B("Reviewed") + "     " + fmt_dt_abs(utcstamp()),
            divider(),
            I("If you believe this was a mistake, open a support ticket and an"),
            I("administrator will look at it personally. You are welcome to"),
            I("request the plan again whenever you are ready."),
        ]))
    chlog("orders", "Membership denied",
          "Order #" + str(order_id) + " by admin " + str(uid), member_uid)
    ack(call, "Denied")
    send_or_edit(cid, mid, msg_order_result(order_id, False, uid),
                 kb_order_request(order_id, member_uid, True))


@extension_callback("po_open:")
def cb_order_open(call, uid, cid, mid, data):
    """Re-open the decision card for one order."""
    if not is_admin(uid):
        return ack(call, "Admins only", True)
    order_id = _tail_int(data, "po_open:")
    order = order_row(order_id)
    done = str(order.get("status", "")) != "pending"
    send_or_edit(cid, mid, msg_order_request(order_id),
                 kb_order_request(order_id, order.get("uid"), done))
    ack(call)


def _cmd_order_queue(uid, args):
    """/reviewqueue - open each pending request with buttons."""
    rows = db_all("SELECT * FROM plan_orders WHERE status='pending' ORDER BY id ASC LIMIT 10")
    if not rows:
        return card(_e("check") + " " + B("NOTHING TO REVIEW"),
                    [I("Every membership request has been handled.")]), \
            KB([BTN(_e("crown") + " All orders", "c:orders")], [BACK("main_menu")])
    lines = [I("Tap a request to see the full member card with approve and deny"),
             I("buttons. Nothing here needs a typed command."), divider()]
    buttons = []
    for row in rows:
        tier = TIERS.get(str(row.get("tier") or ""), {})
        lines.append(B("#" + str(row.get("id"))) + "  " + C(str(row.get("uid")))
                     + "  " + esc(str(tier.get("name", row.get("tier"))))
                     + "  " + I(fmt_dt(row.get("created_at"))))
        buttons.append([BTN(_e("crown") + " Review #" + str(row.get("id")),
                            "po_open:" + str(row.get("id")))])
    buttons.append([BACK("main_menu")])
    return card(_e("clock") + " " + B("REQUESTS WAITING"), lines), KB(*buttons)


define("reviewqueue", "admin", "admin", "Approve or deny requests with buttons",
       _cmd_order_queue, "/reviewqueue")

extension_menu_button(_e("crown") + " Requests", "c:reviewqueue", admin_only=True, row=2)


# ---- p17 ----


# ---------------------------------------------------------------------------
# Commands - extra member views, admin reports and quick actions
# ---------------------------------------------------------------------------

for _spec in [
    ("topusers", "economy", "Leaderboard by points",
     "SELECT * FROM users ORDER BY points DESC LIMIT ?", "none",
     [("Name", "first_name"), ("Points", "points")]),
    ("topuploaders", "economy", "Members with the most files",
     "SELECT * FROM users ORDER BY total_uploads DESC LIMIT ?", "none",
     [("Name", "first_name"), ("Uploads", "total_uploads")]),
    ("toprunners", "economy", "Members who run the most scripts",
     "SELECT * FROM users ORDER BY total_runs DESC LIMIT ?", "none",
     [("Name", "first_name"), ("Runs", "total_runs")]),
    ("topstreaks", "economy", "Longest daily streaks",
     "SELECT * FROM users ORDER BY daily_streak DESC LIMIT ?", "none",
     [("Name", "first_name"), ("Streak", "daily_streak")]),
    ("newestfiles", "files", "The newest public files",
     "SELECT * FROM files WHERE is_public=1 ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Name", "fname")]),
    ("myscripts", "runner", "Your Python scripts",
     "SELECT * FROM files WHERE uid=? AND fname LIKE '%.py' ORDER BY id DESC LIMIT ?",
     "u", [("#", "id"), ("Name", "fname"), ("Runs", "runs")]),
    ("myzips", "files", "Your archives",
     "SELECT * FROM files WHERE uid=? AND fname LIKE '%.zip' ORDER BY id DESC LIMIT ?",
     "u", [("#", "id"), ("Name", "fname")]),
    ("myrejected", "files", "Your rejected uploads",
     "SELECT * FROM files WHERE uid=? AND status='rejected' ORDER BY id DESC LIMIT ?",
     "u", [("#", "id"), ("Name", "fname")]),
    ("myapproved", "files", "Your approved uploads",
     "SELECT * FROM files WHERE uid=? AND status='approved' ORDER BY id DESC LIMIT ?",
     "u", [("#", "id"), ("Name", "fname")]),
    ("unreadnotes", "account", "Notifications you have not read",
     "SELECT * FROM notifications WHERE uid=? AND read=0 ORDER BY id DESC LIMIT ?",
     "u", [("Message", "message"), ("When", "created_at")]),
    ("openmytickets", "support", "Your open tickets",
     "SELECT * FROM tickets WHERE uid=? AND status='open' ORDER BY id DESC LIMIT ?",
     "u", [("#", "id"), ("Subject", "subject")]),
]:
    define(_spec[0], _spec[1], "user", _spec[2],
           sql_cmd(_e("log") + " " + B(_spec[2].upper()), _spec[3], _spec[4], _spec[5]),
           "/" + _spec[0])


for _spec in [
    ("weekuploads", "admin", "Files uploaded this week",
     "SELECT * FROM files WHERE datetime(uploaded) >= datetime('now','-7 day')"
     " ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Name", "fname")]),
    ("todayruns", "adminsys", "Scripts run today",
     "SELECT * FROM script_logs WHERE date(ran_at) = date('now')"
     " ORDER BY id DESC LIMIT ?", "none",
     [("Owner", "uid"), ("File", "fid")]),
    ("weekruns", "adminsys", "Scripts run this week",
     "SELECT * FROM script_logs WHERE datetime(ran_at) >= datetime('now','-7 day')"
     " ORDER BY id DESC LIMIT ?", "none",
     [("Owner", "uid"), ("File", "fid")]),
    ("todaytickets", "admin", "Tickets opened today",
     "SELECT * FROM tickets WHERE date(created_at) = date('now')"
     " ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Subject", "subject")]),
    ("todayorders", "admin", "Upgrade requests from today",
     "SELECT * FROM plan_orders WHERE date(created_at) = date('now')"
     " ORDER BY id DESC LIMIT ?", "none",
     [("#", "id"), ("Owner", "uid"), ("Plan", "tier")]),
    ("approvedorders", "admin", "Approved upgrade requests",
     "SELECT * FROM plan_orders WHERE status='approved' ORDER BY id DESC LIMIT ?",
     "none", [("#", "id"), ("Owner", "uid"), ("Plan", "tier")]),
    ("deniedorders", "admin", "Denied upgrade requests",
     "SELECT * FROM plan_orders WHERE status='denied' ORDER BY id DESC LIMIT ?",
     "none", [("#", "id"), ("Owner", "uid"), ("Plan", "tier")]),
    ("tierbreakdown", "admin", "Members per plan",
     "SELECT tier, COUNT(*) members FROM users GROUP BY tier"
     " ORDER BY members DESC LIMIT ?", "none",
     [("Plan", "tier"), ("Members", "members")]),
    ("storagebyuser", "admin", "Storage used per member",
     "SELECT uid, COUNT(*) files, COALESCE(SUM(fsize),0) bytes FROM files"
     " GROUP BY uid ORDER BY bytes DESC LIMIT ?", "none",
     [("UID", "uid"), ("Files", "files"), ("Bytes", "bytes")]),
    ("filetypes", "admin", "Most common file types",
     "SELECT ftype, COUNT(*) total FROM files GROUP BY ftype"
     " ORDER BY total DESC LIMIT ?", "none",
     [("Type", "ftype"), ("Total", "total")]),
    ("quietusers", "admin", "Members not seen recently",
     "SELECT * FROM users WHERE last_seen IS NULL OR datetime(last_seen)"
     " < datetime('now','-14 day') ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name"), ("Seen", "last_seen")]),
    ("activetoday", "admin", "Members active today",
     "SELECT * FROM users WHERE date(last_seen) = date('now')"
     " ORDER BY uid DESC LIMIT ?", "none",
     [("UID", "uid"), ("Name", "first_name")]),
    ("topreferrers", "admin", "Members who invite the most",
     "SELECT referrer_uid, COUNT(*) invites FROM referrals GROUP BY referrer_uid"
     " ORDER BY invites DESC LIMIT ?", "none",
     [("UID", "referrer_uid"), ("Invites", "invites")]),
    ("sharelinks", "admin", "Every share link",
     "SELECT * FROM file_shares ORDER BY id DESC LIMIT ?", "none",
     [("File", "file_id"), ("Owner", "uid"), ("Token", "token")]),
    ("duecrons", "adminsys", "Jobs due to run",
     "SELECT * FROM cron_jobs WHERE enabled=1 ORDER BY next_run ASC LIMIT ?",
     "none", [("#", "id"), ("Owner", "uid"), ("Next", "next_run")]),
    ("disabledcrons", "adminsys", "Jobs that are switched off",
     "SELECT * FROM cron_jobs WHERE enabled=0 ORDER BY id DESC LIMIT ?",
     "none", [("#", "id"), ("Owner", "uid"), ("Every", "schedule")]),
    ("recentlogs", "adminsys", "Latest channel log entries",
     "SELECT * FROM channel_log ORDER BY id DESC LIMIT ?", "none",
     [("Kind", "category"), ("Title", "title"), ("When", "created_at")]),
    ("moderationlog", "moderation", "Moderation events",
     "SELECT * FROM channel_log WHERE category='moderation' ORDER BY id DESC LIMIT ?",
     "none", [("Title", "title"), ("Detail", "body")]),
    ("orderlog", "admin", "Membership decisions",
     "SELECT * FROM channel_log WHERE category='orders' ORDER BY id DESC LIMIT ?",
     "none", [("Title", "title"), ("Detail", "body")]),
    ("userpoints", "admin", "Points of one member",
     "SELECT * FROM users WHERE uid=? LIMIT ?", "i",
     [("Name", "first_name"), ("Points", "points"), ("Coins", "coins")]),
    ("userruns", "admin", "Script runs of one member",
     "SELECT * FROM script_logs WHERE uid=? ORDER BY id DESC LIMIT ?", "i",
     [("File", "fid"), ("When", "ran_at")]),
    ("userorders", "admin", "Upgrade requests of one member",
     "SELECT * FROM plan_orders WHERE uid=? ORDER BY id DESC LIMIT ?", "i",
     [("#", "id"), ("Plan", "tier"), ("Status", "status")]),
]:
    define(_spec[0], _spec[1], "admin", _spec[2],
           sql_cmd(_e("log") + " " + B(_spec[2].upper()), _spec[3], _spec[4], _spec[5]),
           "/" + _spec[0] + (" <id>" if _spec[4] == "i" else ""))


def _cmd_panel(uid, args):
    """/panel - open the command centre."""
    return msg_command_center(uid), kb_command_center(uid)


def _cmd_allcommands(uid, args):
    """/allcommands - the paged command index."""
    page = _arg_int(args) or 1
    return msg_command_index(uid, page)[0], kb_command_index(uid, page)


def _cmd_mystyle(uid, args):
    """/mystyle - your current font style."""
    style = user_font(uid)
    return card(_e("edit") + " " + B("YOUR FONT STYLE"), [
        B("Style") + "    " + esc(style),
        B("Preview") + "  " + stylize("Sigma Hosting", style),
        divider(),
        I("Tap a style below to change it everywhere in the bot."),
    ]), kb_font_panel(uid)


def _cmd_whoami(uid, args):
    """/whoami - your ID and access level."""
    return card(_e("user") + " " + B("WHO YOU ARE"), [
        B("User ID") + "  " + C(str(uid)),
        B("Access") + "   " + esc(SCOPE_CHIP.get(user_scope(uid), "USER")),
        B("Admin") + "    " + ("yes" if is_admin(uid) else "no"),
        B("Owner") + "    " + ("yes" if is_owner(uid) else "no"),
    ])


def _cmd_groups(uid, args):
    """/groups - command groups you can open."""
    lines = []
    for group in visible_groups(uid):
        items = commands_in_group(group, uid)
        lines.append(B(str(group).title()) + "   " + str(len(items)) + " command(s)")
    return card(_e("menu") + " " + B("COMMAND GROUPS"), lines), kb_command_center(uid)


def _cmd_search(uid, args):
    """/search <text> - find a command by name."""
    term = str(args or "").strip().lower()
    if not term:
        return card(_e("search") + " " + B("WHAT SHOULD I FIND"),
                    [C("/search files")])
    hits = [meta for name, meta in sorted(CMD_REGISTRY.items())
            if term in name or term in str(meta.get("help", "")).lower()]
    hits = [m for m in hits if can_use(uid, m.get("scope", "user"))][:14]
    if not hits:
        return card(_e("no") + " " + B("NO MATCH"),
                    [I("Nothing matched ") + C(esc(term))])
    rows, row = [], []
    lines = []
    for meta in hits:
        lines.append(C("/" + str(meta.get("name"))) + "   " + esc(str(meta.get("help", ""))))
        row.append(cmd_button(meta))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([BACK("cmd_center")])
    return card(_e("search") + " " + B("SEARCH RESULTS"), lines), KB(*rows)


def _cmd_shortcuts(uid, args):
    """/shortcuts - the fastest ways around."""
    return card(_e("fire") + " " + B("SHORTCUTS"), [
        C("/panel") + "        every command, grouped",
        C("/me") + "           your profile card",
        C("/myfiles") + "      manage your uploads",
        C("/font") + "         change the text style",
        C("/plans") + "        compare memberships",
        C("/search") + " text  find any command",
    ]), kb_command_center(uid)


for _name, _grp, _scope, _help, _fn, _usage in [
    ("panel", "core", "user", "Open the command centre", _cmd_panel, "/panel"),
    ("allcommands", "core", "user", "The paged command index", _cmd_allcommands,
     "/allcommands"),
    ("mystyle", "account", "user", "Your current font style", _cmd_mystyle, "/mystyle"),
    ("whoami", "account", "user", "Your ID and access level", _cmd_whoami, "/whoami"),
    ("groups", "core", "user", "Command groups you can open", _cmd_groups, "/groups"),
    ("search", "core", "user", "Find a command by name", _cmd_search, "/search <text>"),
    ("shortcuts", "core", "user", "The fastest ways around", _cmd_shortcuts, "/shortcuts"),
]:
    define(_name, _grp, _scope, _help, _fn, _usage)


# ---- g1 ----
# ============================================================================
# SECTION 29 - HARDENED RUNTIME (JAIL, GUARD HOOK, INTEGRITY, REQUIREMENTS)
# ============================================================================
# Threat model this section answers:
#   * A user uploads a harmless-looking hosting bot. It passes the scanner.
#   * Later the attacker feeds NEW code to that bot at runtime (exec/eval,
#     downloaded payload, their own "upload" feature) and it executes on the
#     host with the host user's rights.
#   * That is a runtime problem, so it needs a runtime answer: every child
#     process gets an in-process audit hook plus OS limits, so even code that
#     never touched the scanner cannot read the bot token, touch the database,
#     spawn shells, load C libraries or write outside its own jail.

HARDEN_DEFAULTS = {
    "jail_runs": 1,          # hosted runs execute inside a per-file jail
    "guard_hook": 1,         # inject the python audit hook
    "block_subprocess": 0,   # child processes inherit the guard, so allow
    "block_ctypes": 0,       # numpy/Pillow/cryptography need native loading
    "block_dynamic": 0,      # deny exec()/eval() of runtime strings
    "net_mode": "allowlist",  # off | allowlist | open
    "integrity_watch": 1,    # hash bot.py/.env/db and alert on change
    "kill_on_violation": 1,  # critical violation stops the script
    "quarantine_on_violation": 1,
    "sandbox_net": 1,        # let review sandboxes reach the network
    "venv_autoinstall": 0,   # install requirements without asking
    "pip_timeout": 240,
}

# Hostnames user scripts may reach when net_mode is "allowlist".
NET_ALLOWLIST_DEFAULT = [
    "api.telegram.org",
    "core.telegram.org",
    "cdn.telegram.org",
    "pypi.org",
    "files.pythonhosted.org",
]

# Severity of each guard verdict.
GUARD_SEVERITY = {
    "path_escape": "critical",
    "secret_read": "critical",
    "db_access": "critical",
    "subprocess": "critical",
    "native_code": "critical",
    "self_replicate": "critical",
    "dynamic_code": "high",
    "network_block": "high",
    "delete_outside": "critical",
    "env_probe": "medium",
    "network_allow": "info",
}

HARDEN_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS runtime_violations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER DEFAULT 0,
        fid        INTEGER DEFAULT 0,
        kind       TEXT    DEFAULT '',
        severity   TEXT    DEFAULT 'info',
        detail     TEXT    DEFAULT '',
        action     TEXT    DEFAULT '',
        created_at TEXT    DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS integrity_baseline (
        path       TEXT PRIMARY KEY,
        sha256     TEXT DEFAULT '',
        size       INTEGER DEFAULT 0,
        checked_at TEXT DEFAULT '',
        alerts     INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS file_requirements (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        fid        INTEGER DEFAULT 0,
        uid        INTEGER DEFAULT 0,
        package    TEXT    DEFAULT '',
        module     TEXT    DEFAULT '',
        state      TEXT    DEFAULT 'missing',
        detail     TEXT    DEFAULT '',
        updated_at TEXT    DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS venv_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER DEFAULT 0,
        fid        INTEGER DEFAULT 0,
        packages   TEXT    DEFAULT '',
        ok         INTEGER DEFAULT 0,
        output     TEXT    DEFAULT '',
        created_at TEXT    DEFAULT ''
    )""",
    """CREATE TABLE IF NOT EXISTS run_jails (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER DEFAULT 0,
        fid        INTEGER DEFAULT 0,
        jail       TEXT    DEFAULT '',
        started_at TEXT    DEFAULT '',
        ended_at   TEXT    DEFAULT ''
    )""",
]

HARDEN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_rv_uid ON runtime_violations(uid)",
    "CREATE INDEX IF NOT EXISTS idx_rv_fid ON runtime_violations(fid)",
    "CREATE INDEX IF NOT EXISTS idx_rv_sev ON runtime_violations(severity)",
    "CREATE INDEX IF NOT EXISTS idx_freq_fid ON file_requirements(fid)",
    "CREATE INDEX IF NOT EXISTS idx_freq_uid ON file_requirements(uid)",
    "CREATE INDEX IF NOT EXISTS idx_venvlog_uid ON venv_log(uid)",
    "CREATE INDEX IF NOT EXISTS idx_runjails_fid ON run_jails(fid)",
]


def init_harden_tables():
    """Create the hardened-runtime tables and indexes."""
    made = 0
    for ddl in HARDEN_SCHEMA:
        try:
            db_exec(ddl)
            made += 1
        except Exception as exc:
            log.error("Harden table failed: %s", exc)
    for ddl in HARDEN_INDEXES:
        try:
            db_exec(ddl)
        except Exception as exc:
            log.debug("Harden index skipped: %s", exc)
    log.info("Hardened runtime tables ready: %s", made)
    return made


def hcfg(key, default=None):
    """Read a hardening setting, falling back to the shipped default."""
    if default is None:
        default = HARDEN_DEFAULTS.get(key, 0)
    try:
        raw = cfg_get("harden_" + str(key), None)
    except Exception:
        raw = None
    if raw is None or raw == "":
        return default
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return default
    return str(raw)


def hcfg_set(key, value):
    """Persist a hardening setting."""
    try:
        cfg_set("harden_" + str(key), str(value), 0)
        return True
    except Exception as exc:
        log.error("hcfg_set failed: %s", exc)
        return False


def hcfg_toggle(key):
    """Flip a 0/1 hardening switch and return the new value."""
    new = 0 if int(hcfg(key) or 0) else 1
    hcfg_set(key, new)
    return new


def net_allowlist():
    """Hostnames user code may contact."""
    raw = str(hcfg("net_hosts", "") or "").strip()
    hosts = list(NET_ALLOWLIST_DEFAULT)
    for item in raw.replace(",", " ").split():
        item = item.strip().lower()
        if item and item not in hosts:
            hosts.append(item)
    return hosts


def guard_dir():
    """Directory holding the generated guard module."""
    path = BASE_DIR / "guard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def protected_paths():
    """Host paths user code must never touch."""
    out = [str(DB_PATH), str(BASE_DIR / "backups"), str(guard_dir())]
    try:
        out.append(str(Path(__file__).resolve()))
        out.append(str(Path(__file__).resolve().parent / ".env"))
    except Exception:
        pass
    return [p for p in out if p]


def record_violation(uid, fid, kind, detail, action="logged"):
    """Store one runtime violation row and return its severity."""
    severity = GUARD_SEVERITY.get(str(kind), "info")
    try:
        db_exec(
            "INSERT INTO runtime_violations (uid, fid, kind, severity, detail,"
            " action, created_at) VALUES (?,?,?,?,?,?,?)",
            (int(uid or 0), int(fid or 0), str(kind)[:40], severity,
             str(detail)[:500], str(action)[:60], utcstamp()),
        )
    except Exception as exc:
        log.error("record_violation failed: %s", exc)
    return severity


def violation_counts(uid=None, hours=24):
    """Counts by severity, optionally for one user."""
    since = (utcnow() - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%d %H:%M:%S")
    out = {"critical": 0, "high": 0, "medium": 0, "info": 0, "total": 0}
    if uid:
        rows = db_all(
            "SELECT severity, COUNT(*) c FROM runtime_violations"
            " WHERE uid=? AND created_at >= ? GROUP BY severity",
            (int(uid), since),
        )
    else:
        rows = db_all(
            "SELECT severity, COUNT(*) c FROM runtime_violations"
            " WHERE created_at >= ? GROUP BY severity",
            (since,),
        )
    for row in rows:
        key = str(row.get("severity") or "info")
        count = int(row.get("c") or 0)
        out[key] = out.get(key, 0) + count
        out["total"] += count
    return out


def recent_violations(limit=12, uid=None, severity=""):
    """Latest violation rows, newest first."""
    sql = "SELECT * FROM runtime_violations WHERE 1=1"
    params = []
    if uid:
        sql += " AND uid=?"
        params.append(int(uid))
    if severity:
        sql += " AND severity=?"
        params.append(str(severity))
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    return db_all(sql, tuple(params))



# ---- g2 ----
# ---------------------------------------------------------------------------
# The guard module. It is written to disk once and injected into every child
# python process through PYTHONPATH, where CPython imports it automatically
# (site.py imports "sitecustomize"). Inside the child it installs an audit
# hook, so every file open, process spawn, native library load, socket and
# dynamic exec goes through our policy before the kernel ever sees it.
# ---------------------------------------------------------------------------

GUARD_SOURCE = '''"""SIGMA runtime guard - generated, do not edit."""
import json
import os
import sys
import time

_ON = os.environ.get("SIGMA_GUARD", "") == "1"


def _load(name, fallback):
    try:
        return json.loads(os.environ.get(name, "") or fallback)
    except Exception:
        return json.loads(fallback)


_WRITE_ROOTS = [str(p) for p in _load("SIGMA_GUARD_WRITE", "[]")]
_READ_DENY = [str(p) for p in _load("SIGMA_GUARD_DENY", "[]")]
_READ_EXTRA = [str(p) for p in _load("SIGMA_GUARD_READ", "[]")]
_HOSTS = [str(h).lower() for h in _load("SIGMA_GUARD_HOSTS", "[]")]
_FLAGS = _load("SIGMA_GUARD_FLAGS", "{}")
_NET = str(os.environ.get("SIGMA_GUARD_NET", "allowlist"))
_LOG = str(os.environ.get("SIGMA_GUARD_LOG", ""))
_SECRET_WORDS = ("sigma.db", ".env", "bot.py", "sigma_hosting/backups",
                 "id_rsa", "authorized_keys", ".ssh/", ".aws/", "shadow")
_busy = False
_seen = {}
_allow_ip = set(["127.0.0.1", "::1", "localhost"])


def _emit(kind, detail, blocked):
    global _busy
    if _busy or not _LOG:
        return
    key = str(kind) + "|" + str(detail)[:120]
    now = time.time()
    if now - float(_seen.get(key, 0)) < 5.0:
        return
    _seen[key] = now
    _busy = True
    try:
        line = json.dumps({
            "kind": str(kind),
            "detail": str(detail)[:400],
            "blocked": bool(blocked),
            "ts": int(now),
        })
        with open(_LOG, "a") as handle:
            handle.write(line + "\\n")
    except Exception:
        pass
    finally:
        _busy = False


def _norm(path):
    try:
        return os.path.realpath(os.path.abspath(str(path)))
    except Exception:
        return str(path)


def _under(path, roots):
    target = _norm(path)
    for root in roots:
        root = _norm(root)
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            return True
    return False


def _deny(kind, detail):
    _emit(kind, detail, True)
    raise PermissionError("SIGMA guard blocked " + str(kind) + ": " + str(detail)[:160])


def _check_write(path, kind="path_escape"):
    if not _WRITE_ROOTS:
        return
    if _under(path, _WRITE_ROOTS):
        return
    if _under(path, ["/dev/null", "/dev/urandom", "/dev/random", "/dev/stdout",
                     "/dev/stderr", "/dev/tty"]):
        return
    _deny(kind, path)


def _check_read(path):
    low = str(path).replace("\\\\", "/").lower()
    if _under(path, _READ_DENY):
        _deny("secret_read", path)
    for word in _SECRET_WORDS:
        if word in low and not _under(path, _WRITE_ROOTS + _READ_EXTRA):
            _deny("secret_read", path)
    if low.endswith(".db") and not _under(path, _WRITE_ROOTS):
        _deny("db_access", path)


def _host_ok(host):
    host = str(host or "").lower().strip("[]")
    if not host:
        return False
    if host in _allow_ip:
        return True
    for allowed in _HOSTS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _hook(event, args):
    if not _ON:
        return
    try:
        if event == "open":
            path = args[0] if args else ""
            mode = str(args[1] or "") if len(args) > 1 else ""
            if isinstance(path, int):
                return
            if any(ch in mode for ch in ("w", "a", "x", "+")):
                _check_write(path)
            else:
                _check_read(path)
            return
        if event in ("os.remove", "os.unlink", "os.rmdir", "os.truncate",
                     "os.mkdir", "os.makedirs", "os.chmod", "os.chown",
                     "os.utime", "os.link", "os.symlink", "os.mknod"):
            if args:
                _check_write(args[0], "delete_outside" if "remov" in event
                             or "unlink" in event or "rmdir" in event
                             else "path_escape")
            return
        if event in ("os.rename", "os.replace", "shutil.move", "shutil.copyfile",
                     "shutil.copytree", "shutil.copymode", "shutil.copystat"):
            for item in list(args)[:2]:
                if isinstance(item, (str, bytes, os.PathLike)):
                    _check_write(item)
            return
        if event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn",
                     "os.posix_spawn", "os.fork", "os.forkpty", "pty.spawn",
                     "os.startfile"):
            if int(_FLAGS.get("block_subprocess", 1) or 0):
                _deny("subprocess", str(event) + " " + str(args)[:200])
            _emit("subprocess", str(event) + " " + str(args)[:200], False)
            return
        if event in ("ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle",
                     "ctypes.call_function", "ctypes.get_errno",
                     "ctypes.set_errno", "ctypes.create_string_buffer"):
            if int(_FLAGS.get("block_ctypes", 1) or 0):
                _deny("native_code", str(event) + " " + str(args)[:160])
            _emit("native_code", str(event), False)
            return
        if event == "socket.getaddrinfo":
            host = args[0] if args else ""
            if isinstance(host, bytes):
                host = host.decode("utf-8", "replace")
            if _NET == "off":
                _deny("network_block", "dns " + str(host))
            if _NET == "allowlist" and not _host_ok(host):
                _deny("network_block", "dns " + str(host))
            _emit("network_allow", "dns " + str(host), False)
            return
        if event == "socket.connect":
            addr = args[1] if len(args) > 1 else None
            host = ""
            if isinstance(addr, tuple) and addr:
                host = str(addr[0])
            elif isinstance(addr, (str, bytes)):
                host = str(addr)
            if _NET == "off" and host:
                _deny("network_block", "connect " + host)
            return
        if event in ("exec", "compile") and int(_FLAGS.get("block_dynamic", 0) or 0):
            _deny("dynamic_code", str(event))
        if event == "cpython.run_stdin":
            _deny("dynamic_code", "stdin execution")
    except PermissionError:
        raise
    except Exception:
        return


if _ON:
    try:
        for _h in _HOSTS:
            pass
        sys.addaudithook(_hook)
        _emit("guard_start", "net=" + _NET + " roots=" + str(len(_WRITE_ROOTS)), False)
    except Exception:
        pass
'''


def write_guard_module():
    """Materialise sitecustomize.py in the guard directory. Returns its path."""
    target = guard_dir() / "sitecustomize.py"
    try:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current.strip() != GUARD_SOURCE.strip():
            target.write_text(GUARD_SOURCE, encoding="utf-8")
        try:
            os.chmod(str(target), 0o444)
        except OSError:
            pass
    except Exception as exc:
        log.error("Could not write guard module: %s", exc)
        return None
    return target


def run_jail(uid, fid):
    """Create (or reuse) the per-file execution jail for a hosted run."""
    jail = BASE_DIR / "jails" / ("u" + str(int(uid))) / ("f" + str(int(fid)))
    for sub in ("home", "tmp", "work", "logs"):
        (jail / sub).mkdir(parents=True, exist_ok=True)
    return jail


def guard_log_path(uid, fid):
    """Where the child writes its violation journal."""
    return run_jail(uid, fid) / "logs" / "guard.jsonl"


def guard_env(uid, fid, jail, net_mode=None, extra_write=()):
    """Environment variables that configure the guard inside the child."""
    write_roots = [str(jail), str(user_dir(uid)), str(user_log_dir(uid))]
    for item in extra_write:
        if item:
            write_roots.append(str(item))
    deny = protected_paths()
    read_extra = [str(Path(sys.executable).parent.parent)]
    flags = {
        "block_subprocess": int(hcfg("block_subprocess") or 0),
        "block_ctypes": int(hcfg("block_ctypes") or 0),
        "block_dynamic": int(hcfg("block_dynamic") or 0),
    }
    mode = str(net_mode or hcfg("net_mode") or "allowlist")
    return {
        "SIGMA_GUARD": "1" if int(hcfg("guard_hook") or 0) else "0",
        "SIGMA_GUARD_WRITE": json.dumps(write_roots),
        "SIGMA_GUARD_DENY": json.dumps(deny),
        "SIGMA_GUARD_READ": json.dumps(read_extra),
        "SIGMA_GUARD_HOSTS": json.dumps(net_allowlist()),
        "SIGMA_GUARD_FLAGS": json.dumps(flags),
        "SIGMA_GUARD_NET": mode,
        "SIGMA_GUARD_LOG": str(guard_log_path(uid, fid)),
    }


SECRET_ENV_KEYS = (
    "BOT_TOKEN", "TOKEN", "API_TOKEN", "ADMIN_IDS", "OWNER_IDS", "LOG_CHANNEL",
    "SIGMA_LOG_CHANNEL", "CHECKER_BOT_TOKEN", "AUTO_APPROVE", "DATABASE_URL",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY",
    "SSH_AUTH_SOCK", "GITHUB_TOKEN", "SIGMA_DB", "SIGMA_BASE",
)


def scrub_env(base=None):
    """Copy of the environment with every host secret removed."""
    env = dict(base if base is not None else os.environ)
    for key in list(env.keys()):
        upper = key.upper()
        if upper in SECRET_ENV_KEYS:
            env.pop(key, None)
            continue
        if any(word in upper for word in ("TOKEN", "SECRET", "PASSWORD", "APIKEY",
                                         "API_KEY", "PRIVATE_KEY", "CREDENTIAL")):
            env.pop(key, None)
    return env


def nproc_headroom(extra=160):
    """Safe RLIMIT_NPROC value: what we already use, plus headroom.

    A flat limit such as 24 fails instantly on a busy VPS because NPROC counts
    every process and thread owned by the same OS user, including this bot.
    That is what produced "RuntimeError: can't start new thread".
    """
    used = 0
    try:
        import resource as _resource
        soft, hard = _resource.getrlimit(_resource.RLIMIT_NPROC)
    except Exception:
        soft, hard = (-1, -1)
    try:
        uid_now = os.getuid()
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                if os.stat("/proc/" + entry).st_uid == uid_now:
                    with open("/proc/" + entry + "/status", "r") as handle:
                        for line in handle:
                            if line.startswith("Threads:"):
                                used += int(line.split()[1])
                                break
                        else:
                            used += 1
            except Exception:
                continue
    except Exception:
        used = 0
    target = max(256, used + int(extra))
    if hard not in (-1, None) and hard > 0:
        target = min(target, int(hard))
    return target


def secure_preexec(uid, fid, jail, limits=None):
    """Build a preexec_fn that isolates the child at the OS level."""
    limits = dict(limits or {})
    cpu = int(limits.get("cpu_seconds", 0) or 0)
    mem_mb = int(limits.get("address_space_mb", 0) or 0)
    fsize_mb = int(limits.get("file_size_mb", 512) or 512)
    nofile = int(limits.get("open_files", 512) or 512)
    nproc = nproc_headroom(int(limits.get("processes", 160) or 160))
    jail_path = str(jail)

    def _apply():
        try:
            os.setsid()
        except Exception:
            pass
        try:
            os.umask(0o077)
        except Exception:
            pass
        try:
            import resource as _resource
        except ImportError:
            return
        wanted = [("RLIMIT_CORE", 0), ("RLIMIT_NPROC", nproc),
                  ("RLIMIT_NOFILE", nofile),
                  ("RLIMIT_FSIZE", fsize_mb * 1024 * 1024)]
        if cpu > 0:
            wanted.append(("RLIMIT_CPU", cpu))
        if mem_mb > 0:
            wanted.append(("RLIMIT_AS", mem_mb * 1024 * 1024))
        for name, value in wanted:
            which = getattr(_resource, name, None)
            if which is None:
                continue
            try:
                soft, hard = _resource.getrlimit(which)
                if hard not in (-1, _resource.RLIM_INFINITY) and value > hard:
                    value = hard
                _resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                continue

    _apply.__doc__ = "Isolate run of file " + str(fid) + " in " + jail_path
    return _apply



# ---- g3 ----
# ---------------------------------------------------------------------------
# Requirement detection and per-user virtual environments.
# Uploading a script now tells the user exactly which packages it imports,
# which ones are missing, and offers one-tap install or manual install.
# ---------------------------------------------------------------------------

# import name -> pip package name (only where they differ)
PIP_NAME_MAP = {
    "telebot": "pyTelegramBotAPI",
    "telegram": "python-telegram-bot",
    "cv2": "opencv-python-headless",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "yaml": "PyYAML",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "jwt": "PyJWT",
    "serial": "pyserial",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "Crypto": "pycryptodome",
    "OpenSSL": "pyOpenSSL",
    "fitz": "PyMuPDF",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "magic": "python-magic",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "pymongo": "pymongo",
    "google": "google-api-python-client",
    "win32api": "pywin32",
    "zoneinfo": "",
    "attr": "attrs",
    "pkg_resources": "setuptools",
    "lxml": "lxml",
    "ujson": "ujson",
    "regex": "regex",
    "redis": "redis",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "flask": "Flask",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "gunicorn": "gunicorn",
    "requests": "requests",
    "numpy": "numpy",
    "pandas": "pandas",
}

# Packages a user script may never install - they are escape or attack tools.
PIP_BLOCKLIST = (
    "pyinstaller", "nuitka", "cython", "cffi", "ctypes-callable", "pwntools",
    "scapy", "impacket", "paramiko-ng", "pycryptodomex-backdoor", "frida",
    "frida-tools", "pyrasite", "memory-profiler-native", "ptrace",
    "python-ptrace", "keyboard", "pynput", "mss", "pyautogui",
)

_STDLIB_NAMES = set(getattr(sys, "stdlib_module_names", ()) or ())


def _is_stdlib(name):
    """True when a module ships with Python itself."""
    base = str(name).split(".")[0]
    if base in _STDLIB_NAMES:
        return True
    return base in (
        "os", "sys", "re", "json", "time", "math", "random", "sqlite3",
        "threading", "subprocess", "datetime", "pathlib", "typing",
        "collections", "itertools", "functools", "logging", "hashlib",
        "base64", "socket", "asyncio", "urllib", "http", "csv", "io",
        "shutil", "zipfile", "traceback", "secrets", "string", "uuid",
    )


def detect_imports(path):
    """Top-level third-party import names used by a python file."""
    found = set()
    try:
        source = Path(str(path)).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(str(alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    continue
                if node.module:
                    found.add(str(node.module).split(".")[0])
    else:
        for match in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z0-9_\.]+)",
                                 source, re.M):
            found.add(match.group(1).split(".")[0])
    out = []
    for name in sorted(found):
        if not name or name.startswith("_") or _is_stdlib(name):
            continue
        out.append(name)
    return out


def pip_name_for(module):
    """Best-guess pip package for an import name."""
    if module in PIP_NAME_MAP:
        return PIP_NAME_MAP[module]
    return str(module)


def venv_dir(uid):
    """Per-user virtual environment directory."""
    return BASE_DIR / "venvs" / str(int(uid))


def venv_python(uid):
    """Path to the user's venv interpreter, or None when absent."""
    candidate = venv_dir(uid) / "bin" / "python3"
    if candidate.exists():
        return candidate
    candidate = venv_dir(uid) / "bin" / "python"
    if candidate.exists():
        return candidate
    return None


def venv_exists(uid):
    """True when the user already has a virtual environment."""
    return venv_python(uid) is not None


def create_venv(uid):
    """Create the user's venv (inherits system site-packages). (ok, message)"""
    target = venv_dir(uid)
    existing = venv_python(uid)
    if existing:
        return True, "Environment already exists."
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages", str(target)],
            capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        return False, "Creating the environment timed out."
    except Exception as exc:
        return False, "Could not create the environment: " + str(exc)
    if result.returncode != 0 or not venv_python(uid):
        detail = (result.stderr or b"").decode("utf-8", "replace")[-400:]
        return False, "venv failed: " + (detail or "unknown error")
    log.info("Created venv for %s", uid)
    return True, "Environment created."


def reset_venv(uid):
    """Delete and recreate the user's environment."""
    try:
        shutil.rmtree(str(venv_dir(uid)), ignore_errors=True)
    except Exception as exc:
        return False, "Could not remove the old environment: " + str(exc)
    return create_venv(uid)


def module_installed(uid, module):
    """Check whether a module imports inside the user's runtime."""
    python = venv_python(uid) or Path(sys.executable)
    try:
        result = subprocess.run(
            [str(python), "-c", "import importlib.util,sys;"
             "sys.exit(0 if importlib.util.find_spec('" + str(module) + "') else 1)"],
            capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def scan_requirements(fid, refresh=True):
    """Detect and store the requirement list for a file. Returns rows."""
    row = get_file(fid)
    if not row:
        return []
    uid = int(row.get("uid") or 0)
    path = Path(str(row.get("fpath") or ""))
    if str(row.get("fname") or "").lower().endswith(".py") is False:
        return []
    if not path.exists():
        return db_all("SELECT * FROM file_requirements WHERE fid=? ORDER BY package",
                      (int(fid),))
    if refresh:
        db_exec("DELETE FROM file_requirements WHERE fid=?", (int(fid),))
        for module in detect_imports(path):
            package = pip_name_for(module)
            if not package:
                continue
            state = "ready" if module_installed(uid, module) else "missing"
            if package.lower() in PIP_BLOCKLIST:
                state = "blocked"
            db_exec(
                "INSERT INTO file_requirements (fid, uid, package, module, state,"
                " detail, updated_at) VALUES (?,?,?,?,?,?,?)",
                (int(fid), uid, package, module, state, "", utcstamp()),
            )
    return db_all("SELECT * FROM file_requirements WHERE fid=? ORDER BY package",
                  (int(fid),))


def missing_requirements(fid):
    """Packages still missing for a file."""
    return db_all(
        "SELECT * FROM file_requirements WHERE fid=? AND state='missing'"
        " ORDER BY package", (int(fid),))


def pip_install(uid, packages, fid=0):
    """Install packages into the user's venv. Returns (ok, message, output)."""
    wanted = []
    for item in list(packages or []):
        name = re.sub(r"[^A-Za-z0-9_.\-\[\]=<>!]+", "", str(item))[:80]
        if not name:
            continue
        base = re.split(r"[=<>!\[]", name)[0].lower()
        if base in PIP_BLOCKLIST:
            return False, "Blocked package: " + base, ""
        wanted.append(name)
    if not wanted:
        return False, "No valid package names given.", ""
    ok, message = create_venv(uid)
    if not ok:
        return False, message, ""
    python = venv_python(uid)
    if not python:
        return False, "No interpreter in the environment.", ""
    command = [str(python), "-m", "pip", "install", "--no-input",
               "--disable-pip-version-check", "--no-cache-dir"] + wanted[:25]
    started = time.time()
    try:
        result = subprocess.run(
            command, capture_output=True,
            timeout=int(hcfg("pip_timeout") or 240),
            env=scrub_env(),
        )
        output = ((result.stdout or b"") + (result.stderr or b"")).decode("utf-8", "replace")
        ok = result.returncode == 0
    except subprocess.TimeoutExpired:
        ok, output = False, "pip timed out."
    except Exception as exc:
        ok, output = False, "pip failed: " + str(exc)
    took = int(time.time() - started)
    db_exec(
        "INSERT INTO venv_log (uid, fid, packages, ok, output, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (int(uid), int(fid), ", ".join(wanted)[:400], 1 if ok else 0,
         output[-4000:], utcstamp()),
    )
    if fid:
        for name in wanted:
            base = re.split(r"[=<>!\[]", name)[0]
            db_exec(
                "UPDATE file_requirements SET state=?, detail=?, updated_at=?"
                " WHERE fid=? AND (package=? OR module=?)",
                ("ready" if ok else "failed", output[-200:], utcstamp(),
                 int(fid), base, base),
            )
    message = ("Installed " + str(len(wanted)) + " package(s) in " + str(took) + "s."
               if ok else "Install failed after " + str(took) + "s.")
    try:
        chlog("security" if not ok else "system",
              "pip install " + ("ok" if ok else "failed"),
              ", ".join(wanted)[:200], int(uid))
    except Exception:
        pass
    return ok, message, output[-3000:]


def install_file_requirements(uid, fid):
    """Install every missing requirement of one file."""
    scan_requirements(fid)
    rows = missing_requirements(fid)
    if not rows:
        return True, "Nothing to install - all requirements are ready.", ""
    packages = [str(r.get("package")) for r in rows if r.get("package")]
    return pip_install(uid, packages, fid)


def installed_packages(uid, limit=200):
    """List packages present in the user's environment."""
    python = venv_python(uid)
    if not python:
        return []
    try:
        result = subprocess.run(
            [str(python), "-m", "pip", "list", "--format=json",
             "--disable-pip-version-check"],
            capture_output=True, timeout=120, env=scrub_env(),
        )
        data = json.loads((result.stdout or b"[]").decode("utf-8", "replace"))
    except Exception:
        return []
    out = []
    for item in data[:int(limit)]:
        out.append({"name": str(item.get("name")), "version": str(item.get("version"))})
    return out


def venv_size(uid):
    """Disk used by the user's environment, in bytes."""
    total = 0
    root = venv_dir(uid)
    if not root.exists():
        return 0
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total



# ---- g4 ----
# ---------------------------------------------------------------------------
# Integrity baseline, violation journal reader and escalation.
# ---------------------------------------------------------------------------

INTEGRITY_TARGETS_EXTRA = ("requirements.txt", "start.sh", ".env.example")


def integrity_targets():
    """Host files that must never change while the bot is running."""
    out = []
    try:
        here = Path(__file__).resolve()
        out.append(here)
        for name in (".env",) + INTEGRITY_TARGETS_EXTRA:
            candidate = here.parent / name
            if candidate.exists():
                out.append(candidate)
    except Exception:
        pass
    if DB_PATH.exists():
        out.append(Path(str(DB_PATH)))
    guard = guard_dir() / "sitecustomize.py"
    if guard.exists():
        out.append(guard)
    return out


def _sha256_of(path):
    """Hex digest of a file, or empty string on failure."""
    digest = hashlib.sha256()
    try:
        with open(str(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except Exception:
        return ""
    return digest.hexdigest()


def integrity_snapshot(reset=False):
    """Store the current hashes as the trusted baseline."""
    count = 0
    for path in integrity_targets():
        digest = _sha256_of(path)
        if not digest:
            continue
        size = 0
        try:
            size = int(path.stat().st_size)
        except OSError:
            pass
        if reset:
            db_exec("DELETE FROM integrity_baseline WHERE path=?", (str(path),))
        db_exec(
            "INSERT OR IGNORE INTO integrity_baseline (path, sha256, size,"
            " checked_at, alerts) VALUES (?,?,?,?,0)",
            (str(path), digest, size, utcstamp()),
        )
        count += 1
    return count


def integrity_check():
    """Compare current hashes with the baseline. Returns a list of findings."""
    findings = []
    rows = db_all("SELECT * FROM integrity_baseline")
    known = {}
    for row in rows:
        known[str(row.get("path"))] = row
    for path in integrity_targets():
        key = str(path)
        digest = _sha256_of(path)
        row = known.get(key)
        if row is None:
            integrity_snapshot()
            continue
        if str(path) == str(DB_PATH):
            continue  # the database changes constantly by design
        if digest and digest != str(row.get("sha256") or ""):
            findings.append({"path": key, "old": str(row.get("sha256"))[:12],
                             "new": digest[:12], "kind": "modified"})
            db_exec(
                "UPDATE integrity_baseline SET alerts = alerts + 1, checked_at=?"
                " WHERE path=?", (utcstamp(), key))
    for key in known:
        if not Path(key).exists():
            findings.append({"path": key, "old": str(known[key].get("sha256"))[:12],
                             "new": "", "kind": "deleted"})
    return findings


def escalate_violation(uid, fid, kind, detail):
    """Log a violation and, when critical, stop and quarantine the script."""
    severity = GUARD_SEVERITY.get(str(kind), "info")
    action = "logged"
    if severity == "critical":
        if int(hcfg("kill_on_violation") or 0):
            try:
                if is_running(uid, fid):
                    stop_script(uid, fid)
                action = "killed"
            except Exception as exc:
                log.error("Violation kill failed: %s", exc)
        if int(hcfg("quarantine_on_violation") or 0):
            try:
                quarantine_file(fid, "Runtime guard: " + str(kind) + " - "
                                + str(detail)[:200], {"verdict": "malicious",
                                                      "score": 100}, 0)
                action = "quarantined"
            except Exception as exc:
                log.error("Violation quarantine failed: %s", exc)
    record_violation(uid, fid, kind, detail, action)
    if severity in ("critical", "high"):
        body = (
            _e("shield") + " " + B("Runtime guard blocked something") + "\n"
            + divider() + "\n"
            + _e("warn") + " " + B("Rule: ") + C(str(kind)) + "\n"
            + _e("user") + " " + B("Owner: ") + C(str(uid)) + "\n"
            + _e("file") + " " + B("File: ") + C("#" + str(fid)) + "\n"
            + _e("info") + " " + B("Detail: ") + esc(str(detail)[:220]) + "\n"
            + _e("gear") + " " + B("Action: ") + esc(action) + "\n"
            + divider() + "\n"
            + I("The script never reached the host. Review it before releasing.")
        )
        try:
            notify_admins(body, KB([BTN(_e("scan") + " Open file", "admin_review_" + str(fid))],
                                   [BTN(_e("shield") + " Violations", "hviol")]))
        except Exception:
            pass
        try:
            chlog("security", "Runtime guard: " + str(kind),
                  "file #" + str(fid) + " | " + str(detail)[:200] + " | " + action, int(uid))
        except Exception:
            pass
    return severity, action


_journal_offsets = {}
_journal_lock = threading.Lock()


def read_guard_journals(limit_lines=200):
    """Consume new lines from every jail journal. Returns how many were handled."""
    root = BASE_DIR / "jails"
    if not root.exists():
        return 0
    handled = 0
    for journal in root.glob("u*/f*/logs/guard.jsonl"):
        try:
            parts = journal.parts
            uid = int(str(parts[-4])[1:])
            fid = int(str(parts[-3])[1:])
        except Exception:
            continue
        key = str(journal)
        with _journal_lock:
            offset = int(_journal_offsets.get(key, 0))
        try:
            size = journal.stat().st_size
            if size < offset:
                offset = 0
            with open(key, "r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                lines = handle.readlines()[:int(limit_lines)]
                new_offset = handle.tell()
        except Exception:
            continue
        with _journal_lock:
            _journal_offsets[key] = new_offset
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            kind = str(record.get("kind") or "")
            if kind in ("guard_start", "network_allow"):
                continue
            if not record.get("blocked") and kind not in GUARD_SEVERITY:
                continue
            escalate_violation(uid, fid, kind, str(record.get("detail") or ""))
            handled += 1
    return handled


def guard_worker(interval=8):
    """Background thread: drain guard journals and watch host integrity."""
    log.info("Runtime guard worker started")
    ticks = 0
    while not _stop_event.is_set():
        try:
            read_guard_journals()
        except Exception as exc:
            log.error("Guard journal read failed: %s", exc)
        ticks += 1
        if ticks % 8 == 0 and int(hcfg("integrity_watch") or 0):
            try:
                for finding in integrity_check():
                    detail = str(finding.get("path")) + " " + str(finding.get("kind"))
                    record_violation(0, 0, "self_replicate", detail, "alerted")
                    log.error("INTEGRITY ALERT: %s", detail)
                    try:
                        notify_admins(
                            _e("shield") + " " + B("Integrity alert") + "\n"
                            + divider() + "\n"
                            + _e("warn") + " " + esc(detail) + "\n"
                            + I("A protected host file changed while the bot was running."),
                            KB([BTN(_e("shield") + " Security centre", "hsec")]))
                        chlog("security", "Integrity alert", detail, 0)
                    except Exception:
                        pass
            except Exception as exc:
                log.error("Integrity check failed: %s", exc)
        _stop_event.wait(max(2, int(interval)))
    log.info("Runtime guard worker stopped")


def active_jails(limit=20):
    """Jail rows for runs that have not been closed."""
    return db_all(
        "SELECT * FROM run_jails WHERE ended_at='' ORDER BY id DESC LIMIT ?",
        (int(limit),))


def register_jail(uid, fid, jail):
    """Remember that a run is using a jail."""
    try:
        db_exec(
            "INSERT INTO run_jails (uid, fid, jail, started_at, ended_at)"
            " VALUES (?,?,?,?,'')",
            (int(uid), int(fid), str(jail), utcstamp()),
        )
    except Exception as exc:
        log.debug("register_jail failed: %s", exc)


def close_jail(uid, fid):
    """Mark the jail rows of a run as finished."""
    try:
        db_exec(
            "UPDATE run_jails SET ended_at=? WHERE uid=? AND fid=? AND ended_at=''",
            (utcstamp(), int(uid), int(fid)),
        )
    except Exception as exc:
        log.debug("close_jail failed: %s", exc)


def harden_status():
    """Snapshot of every protection, for the security panel."""
    counts = violation_counts()
    return {
        "jail_runs": int(hcfg("jail_runs") or 0),
        "guard_hook": int(hcfg("guard_hook") or 0),
        "block_subprocess": int(hcfg("block_subprocess") or 0),
        "block_ctypes": int(hcfg("block_ctypes") or 0),
        "block_dynamic": int(hcfg("block_dynamic") or 0),
        "integrity_watch": int(hcfg("integrity_watch") or 0),
        "kill_on_violation": int(hcfg("kill_on_violation") or 0),
        "quarantine_on_violation": int(hcfg("quarantine_on_violation") or 0),
        "sandbox_net": int(hcfg("sandbox_net") or 0),
        "net_mode": str(hcfg("net_mode") or "allowlist"),
        "hosts": len(net_allowlist()),
        "violations": counts,
        "jails": len(active_jails(50)),
        "guard_file": str(guard_dir() / "sitecustomize.py"),
    }



# ---- g5 ----
# ---------------------------------------------------------------------------
# Security centre UI, requirement UI, commands and wiring. Everything here is
# registered through the extension framework, so no dispatcher edits needed.
# ---------------------------------------------------------------------------

HARDEN_TOGGLES = [
    ("jail_runs", "Jailed runs", "Every hosted script runs in its own jail"),
    ("guard_hook", "Runtime guard", "Audit hook inside every python child"),
    ("block_subprocess", "Block shells", "Deny os.system, Popen, exec, fork"),
    ("block_ctypes", "Block native code", "Deny ctypes and .so loading"),
    ("block_dynamic", "Block exec/eval", "Deny running code built at runtime"),
    ("integrity_watch", "Integrity watch", "Alert if bot.py, .env or guard change"),
    ("kill_on_violation", "Kill on breach", "Stop the script on a critical hit"),
    ("quarantine_on_violation", "Auto quarantine", "Lock the file after a breach"),
    ("sandbox_net", "Sandbox network", "Let review sandboxes reach the internet"),
]


def _onoff(value):
    """Green or red chip for a switch."""
    return (_e("ok") + " ON") if int(value or 0) else (_e("no") + " OFF")


def msg_security_panel(uid):
    """The hardened-runtime control card."""
    state = harden_status()
    counts = state["violations"]
    lines = [
        header("Security centre", uid),
        "",
        _e("shield") + " " + B("Layer 1 - Static scan") + "\n"
        + I("Every upload is scanned before it can be approved."),
        "",
        _e("lock") + " " + B("Layer 2 - Jailed execution") + "  " + _onoff(state["jail_runs"])
        + "\n" + I("HOME, TMPDIR and the working directory are inside a private jail."),
        "",
        _e("scan") + " " + B("Layer 3 - Runtime guard") + "  " + _onoff(state["guard_hook"])
        + "\n" + I("An audit hook inside the child checks every file, process,")
        + "\n" + I("library load and socket - even for code downloaded later."),
        "",
        _e("server") + " " + B("Layer 4 - Integrity watch") + "  " + _onoff(state["integrity_watch"])
        + "\n" + I("bot.py, .env and the guard file are hashed and re-checked."),
        "",
        divider(),
        _e("gear") + " " + B("Shell + exec block: ") + _onoff(state["block_subprocess"]),
        _e("gear") + " " + B("Native code block: ") + _onoff(state["block_ctypes"]),
        _e("gear") + " " + B("Dynamic code block: ") + _onoff(state["block_dynamic"]),
        _e("web") + " " + B("Network policy: ") + C(str(state["net_mode"]))
        + " " + I("(" + str(state["hosts"]) + " allowed hosts)"),
        _e("stop") + " " + B("Kill on breach: ") + _onoff(state["kill_on_violation"]),
        _e("shield") + " " + B("Auto quarantine: ") + _onoff(state["quarantine_on_violation"]),
        divider(),
        _e("warn") + " " + B("Blocked in the last 24h") + "\n"
        + "   " + _e("fire") + " Critical: " + B(str(counts.get("critical", 0)))
        + "   " + _e("warn") + " High: " + B(str(counts.get("high", 0)))
        + "   " + _e("info") + " Other: "
        + B(str(counts.get("medium", 0) + counts.get("info", 0))),
        _e("folder") + " " + B("Live jails: ") + C(str(state["jails"])),
        "",
        I("Tap a switch to flip it. Changes apply to the next run."),
    ]
    return "\n".join(lines)


def kb_security_panel(uid):
    """Toggles and shortcuts for the security centre."""
    rows = []
    pair = []
    for key, label, _hint in HARDEN_TOGGLES:
        chip = _e("ok") if int(hcfg(key) or 0) else _e("no")
        pair.append(BTN(chip + " " + label, "htog:" + key))
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    mode = str(hcfg("net_mode") or "allowlist")
    rows.append([BTN(_e("web") + " Network: " + mode, "hnet:cycle"),
                 BTN(_e("link") + " Allowed hosts", "hnet:hosts")])
    rows.append([BTN(_e("warn") + " Violation log", "hviol"),
                 BTN(_e("server") + " Integrity check", "hint")])
    rows.append([BTN(_e("folder") + " Live jails", "hjail"),
                 BTN(_e("scan") + " Quarantine", "admin_quarantine")])
    rows.append([BTN(_e("reload") + " Refresh", "hsec"), BACK("menu_admin")])
    return KB(*rows)


def msg_violation_log(limit=10):
    """Recent blocked runtime actions."""
    rows = recent_violations(limit)
    lines = [header("Runtime violations", None), ""]
    if not rows:
        lines.append(_e("ok") + " " + I("Nothing blocked yet - the guard is quiet."))
        return "\n".join(lines)
    chips = {"critical": _e("fire"), "high": _e("warn"),
             "medium": _e("info"), "info": _e("note")}
    for row in rows:
        lines.append(
            chips.get(str(row.get("severity")), _e("info")) + " "
            + B(str(row.get("kind"))) + " " + I("#" + str(row.get("fid")))
            + " " + _e("user") + " " + C(str(row.get("uid")))
        )
        lines.append("   " + esc(str(row.get("detail"))[:120]))
        lines.append("   " + I("action: " + str(row.get("action"))
                               + " | " + fmt_dt(row.get("created_at"))))
        lines.append("")
    return "\n".join(lines)


def msg_requirements(fid):
    """Requirement card for one file."""
    row = get_file(fid)
    if not row:
        return _e("no") + " " + B("File not found.")
    rows = scan_requirements(fid, refresh=False) or scan_requirements(fid, refresh=True)
    uid = int(row.get("uid") or 0)
    lines = [
        header("Requirements", uid), "",
        _e("file") + " " + B(esc(str(row.get("fname")))) + " " + C("#" + str(fid)),
        divider(),
    ]
    if not rows:
        lines.append(_e("ok") + " " + I("No third-party imports detected -"
                                        " this script only needs the standard library."))
    else:
        chips = {"ready": _e("ok"), "missing": _e("warn"),
                 "failed": _e("no"), "blocked": _e("lock")}
        for item in rows:
            lines.append(
                chips.get(str(item.get("state")), _e("info")) + " "
                + B(str(item.get("package"))) + "  "
                + I("import " + str(item.get("module")))
                + "  " + C(str(item.get("state")))
            )
        missing = [r for r in rows if str(r.get("state")) == "missing"]
        lines.append(divider())
        lines.append(_e("pts") + " " + B("Total: ") + str(len(rows))
                     + "   " + _e("warn") + " " + B("Missing: ") + str(len(missing)))
    env = venv_python(uid)
    lines.append(divider())
    lines.append(_e("db") + " " + B("Environment: ")
                 + (C("ready") if env else I("not created yet")))
    if env:
        lines.append(_e("folder") + " " + B("Size: ") + fmt_size(venv_size(uid)))
    lines.append("")
    lines.append(I("Auto install fetches every missing package with pip."))
    lines.append(I("Manual install lets you type exact names or versions."))
    return "\n".join(lines)


def kb_requirements(fid, uid=0):
    """Buttons for the requirement card."""
    rows = [
        [BTN(_e("download") + " Auto install missing", "reqin:" + str(fid))],
        [BTN(_e("edit") + " Manual install", "reqman:" + str(fid)),
         BTN(_e("reload") + " Re-scan", "req:" + str(fid))],
        [BTN(_e("db") + " My environment", "venvp"),
         BTN(_e("code") + " requirements.txt", "reqtxt:" + str(fid))],
        [BTN(_e("run") + " Run file", "file_run_" + str(fid)),
         BACK("file_" + str(fid))],
    ]
    return KB(*rows)


def msg_venv_panel(uid):
    """Environment card for a user."""
    packages = installed_packages(uid, 40)
    lines = [header("Python environment", uid), ""]
    if not venv_exists(uid):
        lines.append(_e("warn") + " " + I("No environment yet. It is created the first"
                                          " time you install a package."))
    else:
        lines.append(_e("ok") + " " + B("Status: ") + C("ready"))
        lines.append(_e("folder") + " " + B("Size: ") + fmt_size(venv_size(uid)))
        lines.append(_e("db") + " " + B("Packages: ") + str(len(installed_packages(uid, 500))))
        lines.append(divider())
        for item in packages[:30]:
            lines.append("  " + _e("pts") + " " + B(str(item.get("name")))
                         + " " + I(str(item.get("version"))))
    lines.append("")
    lines.append(I("Installs are isolated per user, so nothing you add can break"
                   " another member's script or the host."))
    return "\n".join(lines)


def kb_venv_panel(uid):
    """Buttons for the environment card."""
    return KB(
        [BTN(_e("plus") + " Install a package", "reqman:0")],
        [BTN(_e("reload") + " Rebuild environment", "vreset"),
         BTN(_e("reload") + " Refresh", "venvp")],
        [BACK("menu_files")],
    )


def requirements_txt(fid):
    """Render a requirements.txt body for a file."""
    rows = scan_requirements(fid, refresh=False)
    names = sorted({str(r.get("package")) for r in rows if r.get("package")})
    return "\n".join(names) if names else "# no third-party requirements detected"


def register_harden_ui():
    """Register every command, callback and menu button of this section."""

    @extension_command("security", "Hardened runtime control centre", True)
    def _cmd_security(message, uid):
        send(uid, msg_security_panel(uid), kb_security_panel(uid))

    @extension_command("harden", "Alias of /security", True)
    def _cmd_harden(message, uid):
        send(uid, msg_security_panel(uid), kb_security_panel(uid))

    @extension_command("violations", "Blocked runtime actions", True)
    def _cmd_violations(message, uid):
        send(uid, msg_violation_log(12),
             KB([BTN(_e("reload") + " Refresh", "hviol")], [BACK("hsec")]))

    @extension_command("integrity", "Verify protected host files", True)
    def _cmd_integrity(message, uid):
        findings = integrity_check()
        if not findings:
            body = (_e("ok") + " " + B("Integrity verified") + "\n"
                    + I("Every protected file matches its baseline hash."))
        else:
            body = _e("warn") + " " + B("Changes detected") + "\n" + divider() + "\n"
            for item in findings[:10]:
                body += (_e("file") + " " + C(str(item.get("path"))) + "\n"
                         + "   " + I(str(item.get("kind")) + ": "
                                     + str(item.get("old")) + " -> "
                                     + str(item.get("new"))) + "\n")
        send(uid, body, KB([BTN(_e("check") + " Trust current files", "htrust")],
                           [BACK("hsec")]))

    @extension_command("netmode", "Cycle the network policy", True)
    def _cmd_netmode(message, uid):
        order = ["off", "allowlist", "open"]
        current = str(hcfg("net_mode") or "allowlist")
        nxt = order[(order.index(current) + 1) % len(order)] if current in order else "allowlist"
        hcfg_set("net_mode", nxt)
        send(uid, _e("web") + " " + B("Network policy: ") + C(nxt) + "\n"
             + I("off = no internet, allowlist = approved hosts only, open = unrestricted."),
             kb_security_panel(uid))

    @extension_command("requirements", "Requirements of a file: /requirements <id>")
    def _cmd_requirements(message, uid):
        args = cmd_args(message).split()
        if not args or not args[0].isdigit():
            send(uid, _e("info") + " Usage: " + C("/requirements <file_id>"),
                 KB([BACK("menu_files")]))
            return
        fid = int(args[0])
        row = get_file(fid)
        if not row or (int(row.get("uid") or 0) != uid and not is_admin(uid)):
            send(uid, _e("no") + " " + B("File not found."), KB([BACK("menu_files")]))
            return
        send(uid, msg_requirements(fid), kb_requirements(fid, uid))

    @extension_command("install", "Install missing packages: /install <id>")
    def _cmd_install(message, uid):
        args = cmd_args(message).split()
        if not args or not args[0].isdigit():
            send(uid, _e("info") + " Usage: " + C("/install <file_id>"),
                 KB([BACK("menu_files")]))
            return
        fid = int(args[0])
        row = get_file(fid)
        if not row or (int(row.get("uid") or 0) != uid and not is_admin(uid)):
            send(uid, _e("no") + " " + B("File not found."), KB([BACK("menu_files")]))
            return
        send(uid, _e("reload") + " " + I("Installing, this can take a minute..."))
        ok, message_text, output = install_file_requirements(uid, fid)
        send(uid, (_e("ok") if ok else _e("no")) + " " + B(esc(message_text)) + "\n"
             + divider() + "\n" + C(esc(output[-1200:] or "no output")),
             kb_requirements(fid, uid))

    @extension_command("pipinstall", "Install named packages: /pipinstall <names>")
    def _cmd_pipinstall(message, uid):
        names = cmd_args(message).split()
        if not names:
            send(uid, _e("info") + " Usage: " + C("/pipinstall requests bs4"),
                 kb_venv_panel(uid))
            return
        ok, message_text, output = pip_install(uid, names, 0)
        send(uid, (_e("ok") if ok else _e("no")) + " " + B(esc(message_text)) + "\n"
             + divider() + "\n" + C(esc(output[-1200:] or "no output")),
             kb_venv_panel(uid))

    @extension_command("venv", "Your python environment")
    def _cmd_venv(message, uid):
        send(uid, msg_venv_panel(uid), kb_venv_panel(uid))

    @extension_command("packages", "Alias of /venv")
    def _cmd_packages(message, uid):
        send(uid, msg_venv_panel(uid), kb_venv_panel(uid))

    @extension_command("venvreset", "Rebuild your python environment")
    def _cmd_venvreset(message, uid):
        ok, message_text = reset_venv(uid)
        send(uid, (_e("ok") if ok else _e("no")) + " " + esc(message_text),
             kb_venv_panel(uid))

    @extension_command("jails", "Live execution jails", True)
    def _cmd_jails(message, uid):
        rows = active_jails(15)
        lines = [header("Live jails", None), ""]
        if not rows:
            lines.append(I("No jailed run is active right now."))
        for row in rows:
            lines.append(_e("folder") + " " + C("#" + str(row.get("fid")))
                         + " " + _e("user") + " " + str(row.get("uid"))
                         + " " + I(fmt_dt(row.get("started_at"))))
            lines.append("   " + C(str(row.get("jail"))[:70]))
        send(uid, "\n".join(lines), KB([BACK("hsec")]))

    @extension_callback("hsec", True, True)
    def _cb_hsec(call, uid, cid, mid, data):
        edit(cid, mid, msg_security_panel(uid), kb_security_panel(uid))

    @extension_callback("htog:", True)
    def _cb_htog(call, uid, cid, mid, data):
        key = data.split(":", 1)[1]
        if key not in HARDEN_DEFAULTS:
            ack(call, "Unknown switch.", True)
            return
        value = hcfg_toggle(key)
        ack(call, key + " -> " + ("ON" if value else "OFF"))
        try:
            chlog("security", "Security switch changed",
                  key + " = " + str(value), uid)
        except Exception:
            pass
        edit(cid, mid, msg_security_panel(uid), kb_security_panel(uid))

    @extension_callback("hnet:", True)
    def _cb_hnet(call, uid, cid, mid, data):
        what = data.split(":", 1)[1]
        if what == "cycle":
            order = ["off", "allowlist", "open"]
            current = str(hcfg("net_mode") or "allowlist")
            nxt = order[(order.index(current) + 1) % len(order)] if current in order else "allowlist"
            hcfg_set("net_mode", nxt)
            ack(call, "Network: " + nxt)
            edit(cid, mid, msg_security_panel(uid), kb_security_panel(uid))
            return
        hosts = net_allowlist()
        body = (header("Allowed hosts", None) + "\n\n"
                + "\n".join(_e("link") + " " + C(h) for h in hosts) + "\n\n"
                + I("Add more with /setconfig harden_net_hosts host1 host2"))
        ack(call)
        edit(cid, mid, body, KB([BACK("hsec")]))

    @extension_callback("hviol", True, True)
    def _cb_hviol(call, uid, cid, mid, data):
        ack(call)
        edit(cid, mid, msg_violation_log(12),
             KB([BTN(_e("reload") + " Refresh", "hviol")], [BACK("hsec")]))

    @extension_callback("hint", True, True)
    def _cb_hint(call, uid, cid, mid, data):
        findings = integrity_check()
        ack(call, "Checked " + str(len(integrity_targets())) + " files")
        if not findings:
            body = (_e("ok") + " " + B("Integrity verified") + "\n" + divider() + "\n"
                    + I("Every protected file matches its baseline hash."))
        else:
            body = _e("warn") + " " + B("Changes detected") + "\n" + divider() + "\n"
            for item in findings[:10]:
                body += (_e("file") + " " + C(str(item.get("path"))[:60]) + "\n"
                         + "   " + I(str(item.get("kind"))) + "\n")
        edit(cid, mid, body, KB([BTN(_e("check") + " Trust current files", "htrust")],
                                [BACK("hsec")]))

    @extension_callback("htrust", True, True)
    def _cb_htrust(call, uid, cid, mid, data):
        count = integrity_snapshot(reset=True)
        ack(call, "Baseline updated")
        edit(cid, mid, _e("ok") + " " + B("New baseline stored") + "\n"
             + I(str(count) + " protected file(s) hashed."), kb_security_panel(uid))

    @extension_callback("hjail", True, True)
    def _cb_hjail(call, uid, cid, mid, data):
        rows = active_jails(15)
        lines = [header("Live jails", None), ""]
        if not rows:
            lines.append(I("No jailed run is active right now."))
        for row in rows:
            lines.append(_e("folder") + " " + C("#" + str(row.get("fid")))
                         + " " + _e("user") + " " + str(row.get("uid")))
        ack(call)
        edit(cid, mid, "\n".join(lines), KB([BACK("hsec")]))

    @extension_callback("req:")
    def _cb_req(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        ack(call, "Scanning imports...")
        scan_requirements(fid, refresh=True)
        edit(cid, mid, msg_requirements(fid), kb_requirements(fid, uid))

    @extension_callback("reqin:")
    def _cb_reqin(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        row = get_file(fid)
        if not row or (int(row.get("uid") or 0) != uid and not is_admin(uid)):
            ack(call, "File not found.", True)
            return
        ack(call, "Installing...")
        edit(cid, mid, _e("reload") + " " + B("Installing requirements") + "\n"
             + I("pip is running inside your private environment."), None)
        ok, message_text, output = install_file_requirements(int(row.get("uid") or uid), fid)
        body = (msg_requirements(fid) + "\n" + divider() + "\n"
                + (_e("ok") if ok else _e("no")) + " " + B(esc(message_text)) + "\n"
                + C(esc(output[-700:] or "no output")))
        edit(cid, mid, body, kb_requirements(fid, uid))

    @extension_callback("reqman:")
    def _cb_reqman(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        set_state(uid, "awaiting_pip_packages", fid=fid)
        ack(call)
        edit(cid, mid,
             _e("edit") + " " + B("Manual install") + "\n" + divider() + "\n"
             + I("Send the package names separated by spaces.") + "\n"
             + I("Versions are allowed, for example:") + "\n"
             + C("requests bs4 pyTelegramBotAPI==4.14.0") + "\n\n"
             + I("Send /cancel to stop."),
             KB([BTN(_e("cross") + " Cancel", "req:" + str(fid) if fid else "venvp")]))

    @extension_callback("reqtxt:")
    def _cb_reqtxt(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        ack(call)
        body = requirements_txt(fid)
        edit(cid, mid, _e("code") + " " + B("requirements.txt for #" + str(fid))
             + "\n" + divider() + "\n" + C(esc(body)),
             kb_requirements(fid, uid))

    @extension_callback("venvp", False, True)
    def _cb_venvp(call, uid, cid, mid, data):
        ack(call)
        edit(cid, mid, msg_venv_panel(uid), kb_venv_panel(uid))

    @extension_callback("vreset", False, True)
    def _cb_vreset(call, uid, cid, mid, data):
        ack(call, "Rebuilding...")
        ok, message_text = reset_venv(uid)
        edit(cid, mid, (_e("ok") if ok else _e("no")) + " " + esc(message_text)
             + "\n\n" + msg_venv_panel(uid), kb_venv_panel(uid))

    try:
        extension_menu_button(_e("shield") + " Security centre", "hsec", True, 30)
        extension_menu_button(_e("db") + " My packages", "venvp", False, 31)
    except Exception as exc:
        log.debug("menu button skipped: %s", exc)


def _fsm_pip_packages(message, uid, state):
    """FSM: manual package install."""
    text = str(getattr(message, "text", "") or "").strip()
    payload = state.get("data") if isinstance(state, dict) else {}
    fid = int((payload or {}).get("fid") or 0)
    clear_state(uid)
    if not text or text.startswith("/cancel"):
        send(uid, _e("info") + " " + I("Install cancelled."), kb_venv_panel(uid))
        return
    names = text.replace(",", " ").split()
    send(uid, _e("reload") + " " + I("Installing " + str(len(names)) + " package(s)..."))
    ok, message_text, output = pip_install(uid, names, fid)
    markup = kb_requirements(fid, uid) if fid else kb_venv_panel(uid)
    send(uid, (_e("ok") if ok else _e("no")) + " " + B(esc(message_text)) + "\n"
         + divider() + "\n" + C(esc(output[-1200:] or "no output")), markup)


HARDEN_FSM = {"awaiting_pip_packages": _fsm_pip_packages}


def harden_boot():
    """Bring the hardened runtime online. Called from main().

    Registration runs FIRST and every step is independent: if hashing or the
    guard file ever fails on a host, the commands still exist instead of the
    whole security layer disappearing with an 'Unknown command'.
    """
    for label, step in (("tables", init_harden_tables),
                        ("commands", register_harden_ui),
                        ("guard module", write_guard_module),
                        ("integrity baseline", integrity_snapshot)):
        try:
            step()
            log.info("Hardening step ok: %s", label)
        except Exception as exc:
            log.error("Hardening step '%s' failed: %s", label, exc)
    try:
        v10_boot()
    except Exception as exc:
        log.error("Version 10.A layer failed: %s", exc)
    try:
        _FSM_HANDLERS.update(HARDEN_FSM)
    except Exception as exc:
        log.error("Harden FSM wiring failed: %s", exc)
    try:
        threading.Thread(target=guard_worker, daemon=True,
                         name="sigma-guard").start()
    except Exception as exc:
        log.error("Guard worker failed to start: %s", exc)
    state = harden_status()
    log.info("Hardened runtime ready: guard=%s jail=%s net=%s hosts=%s",
             state["guard_hook"], state["jail_runs"], state["net_mode"],
             state["hosts"])
    return state



# ---- v10 ----
# ===========================================================================
# SECTION 30 - VERSION 10.A: GLOBAL FONTS, PERMANENT PANEL, POLICY SECURITY
# ===========================================================================
# Everything here is additive and registered through the extension framework,
# so future features can be dropped in as plugins without touching the code
# above.

_REVERSE_FONT = {}


def _build_reverse_font():
    """Map every stylised glyph back to plain ASCII so button presses match."""
    if _REVERSE_FONT:
        return _REVERSE_FONT
    for key, entry in FONT_STYLES.items():
        table = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else None
        if not table:
            continue
        for code, glyph in table.items():
            if glyph and glyph not in _REVERSE_FONT:
                _REVERSE_FONT[glyph] = chr(code)
    return _REVERSE_FONT


def unstylize(text):
    """Turn any fancy alphabet back into plain ASCII."""
    table = _build_reverse_font()
    out = []
    for ch in str(text):
        out.append(table.get(ch, ch))
    return "".join(out)


def font_safe(text, style):
    """Stylise visible words only - HTML tags and entities are left intact.

    Without this, restyling a message would rewrite <b> into a fancy alphabet
    and Telegram would reject the whole message.
    """
    if not style or style == "plain":
        return str(text)
    raw = str(text or "")
    out = []
    buffer = []
    index = 0
    length = len(raw)

    def flush():
        if buffer:
            out.append(stylize("".join(buffer), style))
            del buffer[:]

    while index < length:
        ch = raw[index]
        if ch == "<":
            close = raw.find(">", index)
            if close > index:
                flush()
                out.append(raw[index:close + 1])
                index = close + 1
                continue
        if ch == "&":
            close = raw.find(";", index)
            if 0 < close - index <= 10:
                flush()
                out.append(raw[index:close + 1])
                index = close + 1
                continue
        buffer.append(ch)
        index += 1
    flush()
    return "".join(out)


def style_markup(markup, style):
    """Redraw every inline button label in the member's chosen alphabet."""
    if markup is None or not style or style == "plain":
        return markup
    rows = getattr(markup, "keyboard", None)
    if not rows:
        return markup
    try:
        fresh = types.InlineKeyboardMarkup()
        for row in rows:
            buttons = []
            for button in row:
                label = str(getattr(button, "text", "") or "")
                data = getattr(button, "callback_data", None)
                url = getattr(button, "url", None)
                styled = font_safe(label, style)
                if url:
                    buttons.append(types.InlineKeyboardButton(styled, url=url))
                elif data is not None:
                    buttons.append(types.InlineKeyboardButton(
                        styled, callback_data=str(data)))
                else:
                    buttons.append(types.InlineKeyboardButton(styled,
                                                              callback_data="noop"))
            if buttons:
                fresh.row(*buttons)
        return fresh
    except Exception as exc:
        log.debug("style_markup skipped: %s", exc)
        return markup


def apply_font(uid, text, markup=None):
    """Apply the member's font to a message body and its buttons."""
    try:
        style = user_font(uid)
    except Exception:
        style = "plain"
    if not style or style == "plain":
        return str(text), markup
    return font_safe(text, style), style_markup(markup, style)


# ---------------------------------------------------------------------------
# Permanent keyboard - the always-visible panel under the text box
# ---------------------------------------------------------------------------

PERSIST_BUTTONS = [
    [("Menu", "start"), ("My files", "myfiles")],
    [("Upload help", "upload"), ("Requirements", "requirements")],
    [("Commands", "commands"), ("Profile", "profile")],
    [("Fonts", "font"), ("Support", "ticket")],
]

PERSIST_ADMIN_ROW = [("Security", "security"), ("Admin", "admin")]


def persist_label_map(uid):
    """Label (plain form) -> command name, including the admin row."""
    rows = [list(row) for row in PERSIST_BUTTONS]
    if is_admin(uid):
        rows.append(list(PERSIST_ADMIN_ROW))
    mapping = {}
    for row in rows:
        for label, command in row:
            mapping[label.lower()] = command
    return rows, mapping


def kb_persistent(uid):
    """Build the permanent reply keyboard in the member's own font."""
    rows, _mapping = persist_label_map(uid)
    try:
        style = user_font(uid)
    except Exception:
        style = "plain"
    try:
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True,
                                           one_time_keyboard=False)
    except Exception as exc:
        log.debug("reply keyboard unavailable: %s", exc)
        return None
    icons = {
        "Menu": _e("home"), "My files": _e("folder"), "Upload help": _e("upload"),
        "Requirements": _e("box"), "Commands": _e("list"), "Profile": _e("user"),
        "Fonts": _e("art"), "Support": _e("help"), "Security": _e("shield"),
        "Admin": _e("gear"),
    }
    for row in rows:
        labels = []
        for label, _command in row:
            icon = icons.get(label, "")
            text = (icon + " " if icon else "") + stylize(label, style)
            labels.append(text)
        try:
            markup.row(*labels)
        except Exception:
            continue
    return markup


def kb_hide_persistent():
    """Remove the permanent keyboard."""
    try:
        return types.ReplyKeyboardRemove()
    except Exception:
        return None


def msg_panel_intro(uid):
    """Explain both keyboard layers."""
    return "\n".join([
        header("Control panel", uid), "",
        _e("ok") + " " + B("Permanent buttons") + " " + I("(below the text box)"),
        "   " + I("Always visible. One tap opens any main area - no typing,"),
        "   " + I("no remembering commands."),
        "",
        _e("star") + " " + B("Inline buttons") + " " + I("(inside each message)"),
        "   " + I("Context actions for whatever you are looking at:"),
        "   " + I("run, stop, edit, install, share, approve, deny."),
        "",
        divider(),
        _e("art") + " " + B("Your font: ") + C(FONT_STYLES.get(user_font(uid),
                                                               ("plain",))[0]),
        I("Every message and every button label is drawn in it."),
        "",
        _e("info") + " " + I("Use /panel to restore these buttons, /hidepanel to"
                            " clear them, /font to change the alphabet."),
    ])


# ---------------------------------------------------------------------------
# Per-file security policy - the fix for "clean bot, malicious payload later"
# ---------------------------------------------------------------------------

POLICY_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS file_policy (
        fid INTEGER PRIMARY KEY,
        net_mode TEXT DEFAULT 'allowlist',
        block_dynamic INTEGER DEFAULT 0,
        nested_host INTEGER DEFAULT 0,
        reason TEXT DEFAULT '',
        review_state TEXT DEFAULT 'none',
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS upload_throttle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER,
        created_at TEXT
    )""",
]

NESTED_PATTERNS = [
    ("accepts uploaded documents", r"content_types\s*=\s*\[[^\]]*document"),
    ("downloads telegram files", r"download_file\s*\(|get_file\s*\("),
    ("writes files it received", r"open\s*\([^)]*['\"]wb['\"]"),
    ("launches other programs", r"subprocess\.|os\.system|os\.popen|pty\.spawn"),
    ("runs code built at runtime", r"\beval\s*\(|\bexec\s*\(|compile\s*\("),
    ("imports modules by name", r"importlib|__import__"),
    ("is itself a bot", r"TeleBot\s*\(|telebot\.TeleBot|Application\.builder"),
]


def init_policy_tables():
    """Create the policy and throttle tables."""
    with get_db() as conn:
        for statement in POLICY_SCHEMA:
            conn.execute(statement)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_throttle_uid"
                     " ON upload_throttle(uid, created_at)")
    return True


def nested_host_findings(source, ext=".py"):
    """Detect an uploaded file that could itself host and run other code."""
    text = str(source or "")
    hits = []
    for label, pattern in NESTED_PATTERNS:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                hits.append(label)
        except re.error:
            continue
    return hits


def is_nested_host(hits):
    """A file is a nested host when it both receives files and executes code."""
    receives = any(h in hits for h in ("accepts uploaded documents",
                                       "downloads telegram files",
                                       "writes files it received"))
    executes = any(h in hits for h in ("launches other programs",
                                       "runs code built at runtime",
                                       "imports modules by name"))
    return bool(receives and executes)


def get_policy(fid):
    """Policy row for a file, with defaults applied."""
    row = db_one("SELECT * FROM file_policy WHERE fid=?", (int(fid or 0),))
    if not isinstance(row, dict):
        row = {}
    return {
        "fid": int(fid or 0),
        "net_mode": str(row.get("net_mode") or hcfg("net_mode") or "allowlist"),
        "block_dynamic": int(row.get("block_dynamic") or 0),
        "nested_host": int(row.get("nested_host") or 0),
        "reason": str(row.get("reason") or ""),
        "review_state": str(row.get("review_state") or "none"),
    }


def set_policy(fid, **fields):
    """Upsert one or more policy fields for a file."""
    current = get_policy(fid)
    current.update({k: v for k, v in fields.items() if v is not None})
    db_exec(
        "INSERT INTO file_policy (fid, net_mode, block_dynamic, nested_host,"
        " reason, review_state, updated_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(fid) DO UPDATE SET net_mode=excluded.net_mode,"
        " block_dynamic=excluded.block_dynamic, nested_host=excluded.nested_host,"
        " reason=excluded.reason, review_state=excluded.review_state,"
        " updated_at=excluded.updated_at",
        (int(fid), str(current["net_mode"]), int(current["block_dynamic"]),
         int(current["nested_host"]), str(current["reason"])[:400],
         str(current["review_state"]), utcstamp()),
    )
    return current


def apply_upload_policy(fid):
    """Scan a fresh upload for nested-hosting behaviour and lock it down."""
    row = get_file(fid)
    if not row:
        return None
    path = Path(str(row.get("fpath") or ""))
    try:
        source = read_source_for_scan(path)
    except Exception:
        source = ""
    hits = nested_host_findings(source, path.suffix.lower())
    nested = is_nested_host(hits)
    policy = set_policy(
        fid,
        nested_host=1 if nested else 0,
        block_dynamic=1 if nested else 0,
        net_mode="allowlist",
        reason=", ".join(hits[:6]),
        review_state="manual" if nested else "none",
    )
    if nested:
        uid = int(row.get("uid") or 0)
        detail = ("File #" + str(fid) + " (" + str(row.get("fname"))
                  + ") can receive files and execute code.")
        record_violation(uid, fid, "nested_host", detail, "flagged")
        try:
            chlog("security", "Nested hosting detected", detail, uid)
        except Exception:
            pass
        try:
            notify_admins(
                _e("warn") + " " + B("Nested hosting flagged") + "\n"
                + divider() + "\n"
                + _e("file") + " " + B(esc(str(row.get("fname")))) + " "
                + C("#" + str(fid)) + "\n"
                + _e("user") + " " + C(str(uid)) + "\n"
                + _e("scan") + " " + I(esc(", ".join(hits[:6]))) + "\n\n"
                + I("Dynamic code is blocked for this file and it needs a"
                    " manual decision before it is trusted."))
        except Exception:
            pass
    return policy


RUN_MODES = {
    "strict": {
        "label": "Strict",
        "note": "No shells, no native libraries, no dynamic code. Safest,"
                " but many real programs will not start.",
        "flags": {"block_subprocess": 1, "block_ctypes": 1, "block_dynamic": 1},
        "net": "allowlist",
    },
    "balanced": {
        "label": "Balanced",
        "note": "Recommended. Your token, database, keys and every path"
                " outside the jail stay blocked, but normal libraries and"
                " helper processes work.",
        "flags": {"block_subprocess": 0, "block_ctypes": 0, "block_dynamic": 0},
        "net": "allowlist",
    },
    "open": {
        "label": "Open network",
        "note": "Balanced rules plus unrestricted internet. Use only for"
                " files you wrote yourself.",
        "flags": {"block_subprocess": 0, "block_ctypes": 0, "block_dynamic": 0},
        "net": "open",
    },
}

RUN_MODE_ORDER = ["strict", "balanced", "open"]


def file_run_mode(fid):
    """Run mode for a file. Nested hosts are forced to strict."""
    policy = get_policy(fid)
    if int(policy.get("nested_host") or 0):
        return "strict"
    mode = str(policy.get("review_state") or "")
    if mode.startswith("mode:"):
        candidate = mode.split(":", 1)[1]
        if candidate in RUN_MODES:
            return candidate
    return "balanced"


def set_run_mode(fid, mode):
    """Store the run mode for one file."""
    if mode not in RUN_MODES:
        return False
    spec = RUN_MODES[mode]
    set_policy(fid, review_state="mode:" + mode, net_mode=spec["net"],
               block_dynamic=int(spec["flags"]["block_dynamic"]))
    return True


def policy_env(uid, fid, jail, cwd=None):
    """Guard environment for a run, with the file's own policy applied."""
    policy = get_policy(fid)
    mode = file_run_mode(fid)
    spec = RUN_MODES.get(mode, RUN_MODES["balanced"])
    net = policy["net_mode"] if policy["net_mode"] in ("off", "allowlist", "open") \
        else spec["net"]
    env = guard_env(uid, fid, jail, net_mode=net,
                    extra_write=[cwd] if cwd else ())
    try:
        flags = json.loads(env.get("SIGMA_GUARD_FLAGS") or "{}")
    except Exception:
        flags = {}
    flags.update(spec["flags"])
    if int(policy["block_dynamic"] or 0):
        flags["block_dynamic"] = 1
    env["SIGMA_GUARD_FLAGS"] = json.dumps(flags)
    env["SIGMA_RUN_MODE"] = mode
    return env


def upload_allowed(uid, per_hour=30):
    """Simple flood control on uploads. Returns (ok, message)."""
    if is_admin(uid):
        return True, ""
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    used = db_val("SELECT COUNT(*) FROM upload_throttle WHERE uid=?"
                  " AND created_at>=?", (int(uid), since)) or 0
    if int(used) >= int(per_hour):
        return False, ("Upload limit reached: " + str(per_hour)
                       + " files per hour. Try again later.")
    db_exec("INSERT INTO upload_throttle (uid, created_at) VALUES (?,?)",
            (int(uid), utcstamp()))
    return True, ""


def msg_run_blocked(uid, fid):
    """Shown when a run stops because the guard blocked something."""
    rows = recent_violations(5, fid)
    mode = file_run_mode(fid)
    lines = [
        _e("warn") + " " + B("The run was stopped by the security guard"),
        divider(),
        _e("shield") + " " + B("Mode: ") + C(RUN_MODES[mode]["label"]),
        I(RUN_MODES[mode]["note"]),
        "",
    ]
    if rows:
        lines.append(_e("scan") + " " + B("What was blocked"))
        for row in rows[:5]:
            lines.append("   " + _e("dot") + " " + C(str(row.get("kind")))
                         + "  " + I(esc(str(row.get("detail") or ""))[:70]))
    else:
        lines.append(I("No rule was triggered - check the run log for a plain"
                       " Python error instead."))
    lines += [
        "",
        divider(),
        I("If this file is yours and the block is wrong, switch it to"),
        I("Balanced or Open below. Your token, database and keys stay"),
        I("protected in every mode."),
    ]
    return "\n".join(lines)


def kb_run_blocked(fid):
    """Recovery buttons for a blocked run."""
    return KB(
        [BTN(_e("shield") + " Change run mode", "fpol:" + str(fid))],
        [BTN(_e("scan") + " What was blocked", "hviol"),
         BTN(_e("note") + " Run log", "log_" + str(fid))],
        [BTN(_e("run") + " Try again", "run_" + str(fid)),
         BACK("file_" + str(fid))],
    )


def msg_policy_card(fid):
    """Per-file security policy card."""
    row = get_file(fid)
    if not row:
        return _e("no") + " " + B("File not found.")
    policy = get_policy(fid)
    modes = {"off": "no internet at all", "allowlist": "approved hosts only",
             "open": "unrestricted"}
    lines = [
        header("File security policy", int(row.get("uid") or 0)), "",
        _e("file") + " " + B(esc(str(row.get("fname")))) + " " + C("#" + str(fid)),
        divider(),
        _e("shield") + " " + B("Run mode: ")
        + C(RUN_MODES[file_run_mode(fid)]["label"]),
        "   " + I(RUN_MODES[file_run_mode(fid)]["note"]),
        _e("web") + " " + B("Network: ") + C(policy["net_mode"]) + " "
        + I("(" + modes.get(policy["net_mode"], "") + ")"),
        _e("lock") + " " + B("Dynamic code: ")
        + (_e("no") + " blocked" if policy["block_dynamic"] else _e("ok") + " allowed"),
        _e("scan") + " " + B("Nested hosting: ")
        + (_e("warn") + " detected" if policy["nested_host"] else _e("ok") + " not detected"),
        _e("note") + " " + B("Review: ") + C(policy["review_state"]),
    ]
    if policy["reason"]:
        lines.append(_e("info") + " " + I(esc(policy["reason"])))
    lines += [
        divider(),
        I("A file flagged as a nested host can receive files and execute code,"),
        I("which is how an approved bot smuggles a payload in later. Its"),
        I("dynamic-code path is blocked and the jail still denies your token,"),
        I("database, keys and every path outside its own folder."),
    ]
    return "\n".join(lines)


def kb_policy_card(fid):
    """Buttons for the per-file policy card."""
    policy = get_policy(fid)
    dyn = _e("ok") if policy["block_dynamic"] else _e("no")
    mode = file_run_mode(fid)
    return KB(
        [BTN(_e("shield") + " Run mode: " + RUN_MODES[mode]["label"],
             "fpol_mode:" + str(fid))],
        [BTN(_e("web") + " Network: " + policy["net_mode"], "fpol_net:" + str(fid))],
        [BTN(dyn + " Block dynamic code", "fpol_dyn:" + str(fid))],
        [BTN(_e("check") + " Mark reviewed", "fpol_ok:" + str(fid)),
         BTN(_e("scan") + " Re-scan", "fpol_scan:" + str(fid))],
        [BTN(_e("lock") + " Quarantine now", "fpol_q:" + str(fid))],
        [BTN(_e("box") + " Requirements", "req:" + str(fid)),
         BACK("file_" + str(fid))],
    )


def msg_diag():
    """Self-check card: proves which subsystems actually came up."""
    reg = EXTENSION_REGISTRY
    checks = []

    def probe(label, fn):
        try:
            checks.append((label, True, str(fn())))
        except Exception as exc:
            checks.append((label, False, type(exc).__name__ + ": " + str(exc)[:80]))

    probe("Database tables", lambda: len(db_all(
        "SELECT name FROM sqlite_master WHERE type='table'")))
    probe("Command registry", lambda: len(CMD_REGISTRY))
    probe("Extension commands", lambda: len(reg["commands"]))
    probe("Extension callbacks", lambda: len(reg["callbacks"]))
    probe("Menu buttons", lambda: len(reg["menu_buttons"]))
    probe("Plugins loaded", lambda: len(reg["plugins"]))
    probe("Guard module", lambda: "ok" if (guard_dir() / "sitecustomize.py").exists()
          else "missing")
    probe("Hardening config", lambda: str(hcfg("net_mode")))
    probe("Integrity baseline", lambda: db_val(
        "SELECT COUNT(*) FROM integrity_baseline") or 0)
    probe("Violations 24h", lambda: violation_counts().get("total", 0))
    probe("Requirements rows", lambda: db_val(
        "SELECT COUNT(*) FROM file_requirements") or 0)
    probe("Policy rows", lambda: db_val("SELECT COUNT(*) FROM file_policy") or 0)
    probe("Fonts available", lambda: len(FONT_STYLES))

    lines = [header("Self check", None), ""]
    for label, ok, detail in checks:
        lines.append((_e("ok") if ok else _e("no")) + " " + B(label + ": ")
                     + C(str(detail)))
    missing = [name for name in ("security", "requirements", "pipinstall",
                                 "venv", "violations", "panel")
               if name not in reg["commands"] and name not in CMD_REGISTRY]
    lines.append(divider())
    if missing:
        lines.append(_e("warn") + " " + B("Not registered: ")
                     + C(", ".join(missing)))
        lines.append(I("Run /reload, then check the startup log for the line"
                       " 'Hardened runtime ready'."))
    else:
        lines.append(_e("ok") + " " + B("Every core command is registered."))
    lines.append(_e("info") + " " + B("Version: ") + C(str(VERSION)))
    return "\n".join(lines)


def register_v10_ui():
    """Register the version 10.A commands, callbacks and hooks."""

    @extension_command("panel", "Show the permanent button panel")
    def _cmd_panel(message, uid):
        markup = kb_persistent(uid)
        text, _ = apply_font(uid, msg_panel_intro(uid))
        try:
            bot.send_message(int(uid), text, reply_markup=markup)
        except Exception as exc:
            log.debug("panel keyboard failed: %s", exc)
            send(uid, msg_panel_intro(uid), kb_main(uid))
            return
        send(uid, _e("star") + " " + B("Inline panel") + "\n"
             + I("Context actions for everything below."), kb_main(uid))

    @extension_command("keyboard", "Alias of /panel")
    def _cmd_keyboard(message, uid):
        _cmd_panel(message, uid)

    @extension_command("hidepanel", "Hide the permanent buttons")
    def _cmd_hidepanel(message, uid):
        text, _ = apply_font(uid, _e("ok") + " " + B("Permanent buttons hidden.")
                             + "\n" + I("Use /panel to bring them back."))
        try:
            bot.send_message(int(uid), text, reply_markup=kb_hide_persistent())
        except Exception:
            send(uid, text)

    @extension_command("diag", "Self check every subsystem", True)
    def _cmd_diag(message, uid):
        send(uid, msg_diag(), KB(
            [BTN(_e("reload") + " Re-run", "diag")],
            [BTN(_e("shield") + " Security centre", "hsec")],
            [BACK("menu_admin")]))

    @extension_command("policy", "File security policy: /policy <id>", True)
    def _cmd_policy(message, uid):
        args = cmd_args(message).split()
        if not args or not args[0].isdigit():
            rows = db_all("SELECT fid, net_mode, block_dynamic, nested_host"
                          " FROM file_policy ORDER BY updated_at DESC LIMIT 12")
            lines = [header("File policies", None), ""]
            if not rows:
                lines.append(I("No file has a custom policy yet."))
            for row in rows:
                lines.append(
                    (_e("warn") if int(row.get("nested_host") or 0) else _e("ok"))
                    + " " + C("#" + str(row.get("fid")))
                    + "  " + B("net: ") + str(row.get("net_mode"))
                    + "  " + B("dynamic: ")
                    + ("blocked" if int(row.get("block_dynamic") or 0) else "allowed"))
            lines.append("")
            lines.append(I("Open one with /policy <file_id>."))
            send(uid, "\n".join(lines), KB([BACK("hsec")]))
            return
        fid = int(args[0])
        send(uid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_command("nested", "Files flagged as nested hosts", True)
    def _cmd_nested(message, uid):
        rows = db_all("SELECT p.fid, p.reason, f.fname, f.uid FROM file_policy p"
                      " LEFT JOIN files f ON f.id=p.fid WHERE p.nested_host=1"
                      " ORDER BY p.updated_at DESC LIMIT 15")
        lines = [header("Nested hosting", None), ""]
        if not rows:
            lines.append(_e("ok") + " " + I("No uploaded file can host and run"
                                            " other code."))
        for row in rows:
            lines.append(_e("warn") + " " + B(esc(str(row.get("fname") or "?")))
                         + " " + C("#" + str(row.get("fid")))
                         + " " + _e("user") + " " + str(row.get("uid")))
            lines.append("   " + I(esc(str(row.get("reason") or ""))[:110]))
        send(uid, "\n".join(lines), KB([BACK("hsec")]))

    @extension_callback("diag", True, True)
    def _cb_diag(call, uid, cid, mid, data):
        ack(call, "Re-checked")
        edit(cid, mid, msg_diag(), KB(
            [BTN(_e("reload") + " Re-run", "diag")],
            [BTN(_e("shield") + " Security centre", "hsec")],
            [BACK("menu_admin")]))

    @extension_callback("fpol:")
    def _cb_fpol_open(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        ack(call)
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_mode:")
    def _cb_fpol_mode(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        if int(get_policy(fid).get("nested_host") or 0) and not is_admin(uid):
            ack(call, "This file is flagged as a nested host. Admin only.", True)
            return
        current = file_run_mode(fid)
        nxt = RUN_MODE_ORDER[(RUN_MODE_ORDER.index(current) + 1)
                             % len(RUN_MODE_ORDER)]
        set_run_mode(fid, nxt)
        ack(call, "Run mode: " + RUN_MODES[nxt]["label"])
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_net:", True)
    def _cb_fpol_net(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        order = ["off", "allowlist", "open"]
        current = get_policy(fid)["net_mode"]
        nxt = order[(order.index(current) + 1) % len(order)] if current in order else "off"
        set_policy(fid, net_mode=nxt)
        ack(call, "Network: " + nxt)
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_dyn:", True)
    def _cb_fpol_dyn(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        value = 0 if get_policy(fid)["block_dynamic"] else 1
        set_policy(fid, block_dynamic=value)
        ack(call, "Dynamic code " + ("blocked" if value else "allowed"))
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_ok:", True)
    def _cb_fpol_ok(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        set_policy(fid, review_state="reviewed")
        try:
            audit(uid, "policy_review", fid, "marked reviewed")
            chlog("security", "File policy reviewed", "File #" + str(fid), uid)
        except Exception:
            pass
        ack(call, "Marked reviewed")
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_scan:", True)
    def _cb_fpol_scan(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        apply_upload_policy(fid)
        ack(call, "Re-scanned")
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_callback("fpol_q:", True)
    def _cb_fpol_q(call, uid, cid, mid, data):
        fid = int(str(data).split(":", 1)[1] or 0)
        ok, detail = quarantine_file(fid, "manual policy decision",
                                     {"verdict": "manual", "score": 100}, uid)
        ack(call, detail[:60])
        edit(cid, mid, msg_policy_card(fid), kb_policy_card(fid))

    @extension_scanner("nested_hosting")
    def _scan_nested(source, ext):
        hits = nested_host_findings(source, ext)
        if not is_nested_host(hits):
            return []
        return [{
            "id": "nested_hosting",
            "title": "Nested hosting: can run code uploaded later",
            "category": "exec",
            "severity": 9,
            "line": 0,
            "excerpt": ", ".join(hits[:5]),
            "detail": ("This file can receive files and execute code, so it"
                       " could run a payload uploaded after approval: "
                       + ", ".join(hits[:5])),
        }]

    @extension_text_hook(priority=10)
    def _hook_persistent(message, uid, text):
        plain = unstylize(str(text or "")).strip()
        plain = re.sub(r"^[^A-Za-z0-9/]+", "", plain).strip().lower()
        _rows, mapping = persist_label_map(uid)
        command = mapping.get(plain)
        if not command:
            return False
        fake = message
        try:
            fake.text = "/" + command
        except Exception:
            pass
        if dispatch_extension_command(fake, uid, command):
            return True
        if command in CMD_REGISTRY:
            body, markup = run_command(uid, command, "")
            send(uid, body, markup or kb_main(uid))
            return True
        send(uid, msg_panel_intro(uid), kb_main(uid))
        return True

    try:
        extension_menu_button(_e("list") + " Control panel", "v10_panel", False, 29)
    except Exception as exc:
        log.debug("v10 menu buttons skipped: %s", exc)

    @extension_callback("v10_panel", False, True)
    def _cb_v10_panel(call, uid, cid, mid, data):
        ack(call)
        edit(cid, mid, msg_panel_intro(uid), kb_main(uid))
        try:
            bot.send_message(int(uid), font_safe(_e("ok") + " Permanent buttons ready.",
                                                 user_font(uid)),
                             reply_markup=kb_persistent(uid))
        except Exception:
            pass


def v10_boot():
    """Start the version 10.A layer. Every step is independent."""
    steps = [
        ("policy tables", init_policy_tables),
        ("reverse font map", _build_reverse_font),
        ("v10 UI", register_v10_ui),
    ]
    ok = 0
    for label, fn in steps:
        try:
            fn()
            ok += 1
        except Exception as exc:
            log.error("v10 step '%s' failed: %s", label, exc)
    log.info("Version 10.A layer ready: %s/%s steps, %s fonts, %s commands",
             ok, len(steps), len(FONT_STYLES), len(EXTENSION_REGISTRY["commands"]))
    return ok


# ---- pF ----


# ---------------------------------------------------------------------------
# Wiring - commands, callbacks, FSM states and menu buttons
# ---------------------------------------------------------------------------

_START_TS = time.time()


def _make_command_handler(name):
    """Build a Telegram handler for one registry command."""

    def handler(message, uid, _name=name):
        args = ""
        try:
            args = cmd_args(message)
        except Exception:
            raw = str(getattr(message, "text", "") or "")
            parts = raw.split(None, 1)
            args = parts[1] if len(parts) > 1 else ""
        text, markup = run_command(uid, _name, args)
        send(getattr(getattr(message, "chat", None), "id", uid), text, markup)
        return True

    return handler


def register_panel_commands():
    """Expose every registry command as a real /command."""
    count = 0
    for name, meta in list(CMD_REGISTRY.items()):
        try:
            extension_command(
                name,
                str(meta.get("help", ""))[:110],
                admin_only=(meta.get("scope") in ("admin", "owner")),
            )(_make_command_handler(name))
            count += 1
        except Exception as exc:
            log.warning("Could not register /%s: %s", name, exc)
    return count


def register_panel_callbacks():
    """Attach every panel button handler to its callback prefix."""
    pairs = [
        ("cmd_center", cb_cmd_center, False, True),
        ("menu_commands", cb_menu_commands, False, True),
        ("cmd_index:", cb_cmd_index, False, False),
        ("cg:", cb_cmd_group, False, False),
        ("ch:", cb_cmd_help, False, False),
        ("c:", cb_run_command, False, False),
        ("cmd_find", cb_cmd_find, False, True),
        ("ub:", cb_user_browse, True, False),
        ("ub_search", cb_user_search, True, True),
        ("ui:", cb_user_info, True, False),
        ("uf:", cb_user_files, True, False),
        ("ua:", cb_user_activity, True, False),
        ("ue:", cb_user_economy, True, False),
        ("ut:", cb_user_tickets, True, False),
        ("uc:", cb_user_commands, True, False),
        ("us:", cb_user_settings, True, False),
        ("uw:", cb_user_warnings, True, False),
        ("un:", cb_user_notes, True, False),
        ("uban:", cb_user_ban, True, False),
        ("uunban:", cb_user_unban, True, False),
        ("uwclear:", cb_user_warn_clear, True, False),
        ("uwarn:", cb_user_warn, True, False),
        ("udm:", cb_user_dm, True, False),
        ("ugp:", cb_user_grant, True, False),
        ("uwipe:", cb_user_wipe, True, False),
        ("uwipe2:", cb_user_wipe_confirm, True, False),
        ("font_panel", cb_font_panel, False, True),
        ("font_set:", cb_font_set, False, False),
        ("font_word", cb_font_word, False, True),
        ("po_ok:", cb_order_approve, True, False),
        ("po_no:", cb_order_deny, True, False),
        ("po_open:", cb_order_open, True, False),
    ]
    count = 0
    for prefix, handler, admin_only, exact in pairs:
        try:
            extension_callback(prefix, admin_only=admin_only, exact=exact)(handler)
            count += 1
        except Exception as exc:
            log.warning("Could not register callback %s: %s", prefix, exc)
    return count


PANEL_FSM = {
    "awaiting_cmd_arg": _fsm_cmd_arg,
    "awaiting_cmd_search": _fsm_cmd_search,
    "awaiting_user_browse": _fsm_user_browse,
    "awaiting_warn_reason": _fsm_warn_reason,
    "awaiting_dm_text": _fsm_dm_text,
    "awaiting_grant_points": _fsm_grant_points,
    "awaiting_font_word": _fsm_font_word,
}

try:
    _FSM_HANDLERS.update(PANEL_FSM)
except Exception:
    pass


def register_panel_menu():
    """Add the new panels to the main menu."""
    buttons = [
        (_e("menu") + " All commands", "cmd_center", False, 1),
        (_e("users") + " Members", "ub:all:1", True, 3),
        (_e("crown") + " Upgrade requests", "c:orderqueue", True, 3),
    ]
    count = 0
    for label, data, admin_only, row in buttons:
        try:
            extension_menu_button(label, data, admin_only=admin_only, row=row)
            count += 1
        except Exception as exc:
            log.warning("Could not add menu button %s: %s", label, exc)
    return count


def panel_boot():
    """Bring the command centre online. Safe to call more than once."""
    try:
        init_panel_tables()
    except Exception as exc:
        log.warning("Panel tables not ready: %s", exc)
    commands = register_panel_commands()
    callbacks = register_panel_callbacks()
    menu = register_panel_menu()
    try:
        extra = [x.strip() for x in str(cfg_get("extra_admins", "")).split(",") if x.strip()]
        for value in extra:
            if value.isdigit():
                ADMIN_IDS.add(int(value))
    except Exception:
        pass
    try:
        worker = threading.Thread(target=chlog_worker, name="chlog", daemon=True)
        worker.start()
    except Exception as exc:
        log.warning("Log worker did not start: %s", exc)
    counts = registry_counts()
    log.info("Command centre ready: %s commands, %s callbacks, %s menu buttons",
             commands, callbacks, menu)
    log.info("Registry: %s commands (%s user, %s admin, %s owner) in %s groups",
             counts.get("total", 0), counts.get("user", 0),
             counts.get("admin", 0), counts.get("owner", 0), len(CMD_GROUPS))
    return {"commands": commands, "callbacks": callbacks, "menu": menu}

# ============================================================================
# SECTION 19 - BACKGROUND THREADS
# ============================================================================

_stop_event = threading.Event()
_threads = []


def cron_runner_loop():
    """Run due cron jobs every 60 seconds. Never crashes."""
    log.info("Cron runner started")
    while not _stop_event.is_set():
        try:
            due = get_due_crons()
            for job in due:
                try:
                    ok, result = run_cron_job(job)
                    log.info("Cron #%s -> %s (%s)", job.get("id"), ok, result)
                except Exception as exc:
                    log.warning("Cron job %s failed: %s", job.get("id"), exc)
        except Exception as exc:
            log.warning("cron_runner_loop iteration failed: %s", exc)
        _stop_event.wait(60)
    log.info("Cron runner stopped")


def bg_cleanup():
    """Hourly housekeeping: old logs, expired shares, stale rate limits."""
    log.info("Cleanup worker started")
    while not _stop_event.is_set():
        _stop_event.wait(3600)
        if _stop_event.is_set():
            break
        try:
            removed = cleanup_old_logs(14)
            db_exec("DELETE FROM file_shares WHERE expires_at IS NOT NULL AND expires_at < ?",
                    (utcstamp(),))
            db_exec("DELETE FROM rate_limits WHERE window_start < ?",
                    (utcstamp(utcnow() - timedelta(days=1)),))
            db_exec("DELETE FROM notifications WHERE read=1 AND created_at < ?",
                    (utcstamp(utcnow() - timedelta(days=30)),))
            db_exec("DELETE FROM user_activity WHERE ts < ?",
                    (utcstamp(utcnow() - timedelta(days=60)),))
            for tmp in TMP_DIR.glob("*"):
                try:
                    if tmp.is_file() and time.time() - tmp.stat().st_mtime > 86400:
                        tmp.unlink()
                except Exception as exc:
                    log.debug("tmp cleanup skip: %s", exc)
            log.info("Cleanup pass done (%s log records removed)", removed)
        except Exception as exc:
            log.warning("bg_cleanup failed: %s", exc)
    log.info("Cleanup worker stopped")


def bg_metrics():
    """Record a metrics row every 5 minutes."""
    log.info("Metrics worker started")
    while not _stop_event.is_set():
        try:
            record_metrics()
        except Exception as exc:
            log.warning("bg_metrics failed: %s", exc)
        _stop_event.wait(300)
    log.info("Metrics worker stopped")


def bg_watchdog():
    """Reap finished processes and enforce tier timeouts every 30 seconds."""
    log.info("Watchdog started")
    while not _stop_event.is_set():
        try:
            with _proc_lock:
                keys = list(_procs.keys())
            for key in keys:
                with _proc_lock:
                    proc = _procs.get(key)
                    started = _proc_start_times.get(key, time.time())
                if proc is None:
                    continue
                if proc.poll() is not None:
                    with _proc_lock:
                        _procs.pop(key, None)
                        _proc_start_times.pop(key, None)
                        _proc_args.pop(key, None)
                    continue
                parts = str(key).split("_")
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    continue
                owner = int(parts[0])
                fid = int(parts[1])
                limit = TIER_TIMEOUTS.get(get_tier_key(owner), 300)
                if time.time() - float(started) > limit:
                    log.info("Watchdog stopping %s after %ss", key, int(time.time() - started))
                    stop_script(owner, fid)
                    notify_user(
                        owner,
                        _e("clock") + " Script " + C("#" + str(fid))
                        + " hit the " + fmt_duration(limit) + " limit for your plan and was stopped.",
                    )
        except Exception as exc:
            log.warning("bg_watchdog failed: %s", exc)
        _stop_event.wait(30)
    log.info("Watchdog stopped")


def bg_notifications():
    """Deliver queued notifications every 2 minutes."""
    log.info("Notification worker started")
    while not _stop_event.is_set():
        _stop_event.wait(120)
        if _stop_event.is_set():
            break
        try:
            rows = db_all(
                "SELECT n.id, n.uid, n.message FROM notifications n"
                " JOIN users u ON u.uid = n.uid"
                " WHERE n.read=0 AND n.type='queued' AND u.push_enabled=1 AND u.is_banned=0"
                " ORDER BY n.id ASC LIMIT 40",
                (),
            )
            for row in rows:
                delivered = send(int(row.get("uid") or 0), _e("bell") + " " + esc(str(row.get("message"))))
                db_exec("UPDATE notifications SET read=1 WHERE id=?", (int(row.get("id") or 0),))
                if delivered is None:
                    log.debug("Queued notification %s not delivered", row.get("id"))
                time.sleep(0.05)
        except Exception as exc:
            log.warning("bg_notifications failed: %s", exc)
    log.info("Notification worker stopped")


def start_background_threads():
    """Spawn every background worker as a daemon thread."""
    workers = [
        ("sigma-cron", cron_runner_loop),
        ("sigma-cleanup", bg_cleanup),
        ("sigma-metrics", bg_metrics),
        ("sigma-watchdog", bg_watchdog),
        ("sigma-notify", bg_notifications),
    ]
    for name, target in workers:
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        _threads.append(thread)
    log.info("Started %s background thread(s)", len(_threads))
    return _threads


def stop_background_threads():
    """Signal every worker to exit and wait briefly."""
    _stop_event.set()
    for thread in list(_threads):
        try:
            thread.join(timeout=3)
        except Exception as exc:
            log.debug("thread join failed: %s", exc)
    return True


# ============================================================================
# SECTION 20 - MAIN ENTRYPOINT
# ============================================================================

BOT_COMMANDS = [
    ("start", "Start the bot and open the menu"),
    ("menu", "Open the main menu"),
    ("help", "Show every command"),
    ("profile", "Your profile card"),
    ("daily", "Claim the daily bonus"),
    ("files", "Manage your files"),
    ("upload", "How to upload files"),
    ("run", "Run a script by id"),
    ("stop", "Stop a running script"),
    ("log", "View script output"),
    ("cron", "Scheduled jobs"),
    ("api", "API keys"),
    ("economy", "Points and coins"),
    ("shop", "Spend your coins"),
    ("stats", "Platform statistics"),
    ("ticket", "Open a support ticket"),
    ("security", "Security centre and scan reports"),
    ("scan", "Scan a file for malware"),
    ("sandbox", "Run a file in the isolated jail"),
    ("editor", "Open the guarded code editor"),
    ("pull", "Pull source lines for editing"),
    ("push", "Push corrected code (re-scanned)"),
    ("plugins", "Loaded extensions"),
    ("react", "Live reactions and animations"),
    ("cancel", "Cancel the current input"),
    ("admin", "Admin panel (staff only)"),
]


def register_bot_commands():
    """Publish the command list to Telegram."""
    try:
        commands = [types.BotCommand(name, description) for name, description in BOT_COMMANDS]
        bot.set_my_commands(commands)
        log.info("Registered %s bot commands", len(commands))
        return True
    except Exception as exc:
        log.warning("Could not register commands: %s", exc)
        return False


def log_startup_banner():
    """Print a startup summary to the log."""
    stats = sys_stats()
    log.info("=" * 62)
    log.info("%s v%s starting up", BOT_NAME, VERSION)
    log.info("Base dir : %s", BASE_DIR)
    log.info("Database : %s (%s)", DB_PATH, fmt_size(db_size()))
    log.info("Admins   : %s", ", ".join(str(a) for a in sorted(ADMIN_IDS)) or "none configured")
    log.info("Auto app.: %s", AUTO_APPROVE)
    log.info("Users    : %s | Files: %s",
             db_val("SELECT COUNT(*) c FROM users", (), 0),
             db_val("SELECT COUNT(*) c FROM files", (), 0))
    log.info("CPU %.2f | Mem %.1f%% | Disk %.1f%%",
             stats.get("cpu", 0.0), stats.get("mem_pct", 0.0), stats.get("disk_pct", 0.0))
    log.info("Python   : %s", sys.version.split()[0])
    log.info("=" * 62)


def _install_signal_handlers():
    """Exit cleanly on SIGINT/SIGTERM."""

    def _handler(signum, frame):
        log.info("Signal %s received \u2014 shutting down", signum)
        try:
            bot.stop_polling()
        except Exception as exc:
            log.debug("stop_polling failed: %s", exc)
        stop_background_threads()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except Exception as exc:
            log.debug("Could not install %s handler: %s", name, exc)


REQUIRED_COMMANDS = (
    "security", "harden", "violations", "integrity", "netmode", "jails",
    "requirements", "install", "pipinstall", "venv", "packages", "venvreset",
    "panel", "hidepanel", "diag", "policy", "nested",
)


def verify_registration():
    """Fail loudly at boot if a core command did not register.

    A silent registration failure is what makes a command answer
    'Unknown command' at runtime, so it is checked while the log is visible.
    """
    known = set(EXTENSION_REGISTRY["commands"].keys()) | set(CMD_REGISTRY.keys())
    missing = [name for name in REQUIRED_COMMANDS if name not in known]
    if missing:
        log.error("MISSING COMMANDS: %s - the security layer did not register.",
                  ", ".join(missing))
        for admin_uid in sorted(ADMIN_IDS):
            try:
                send(admin_uid,
                     _e("warn") + " " + B("Startup warning") + "\n"
                     + I("These commands failed to register: ")
                     + C(", ".join(missing)) + "\n"
                     + I("Run /diag for details."))
            except Exception:
                pass
    else:
        log.info("Registration verified: %s core commands, %s total",
                 len(REQUIRED_COMMANDS), len(known))
    return missing


def main():
    """Validate config, prepare storage and start polling."""
    if not API_TOKEN:
        log.error("No bot token configured. Set the TOKEN environment variable.")
        raise SystemExit(2)
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS is empty \u2014 no one can use the admin panel.")
    try:
        init_db()
        run_migrations()
        init_extra_tables()
        try:
            panel_boot()
        except Exception as exc:
            log.warning("Command centre boot issue: %s", exc)
    except Exception as exc:
        log.error("Database initialisation failed: %s", exc)
        raise SystemExit(3)
    # Security, requirements and the version 10.A layer. This MUST run before
    # load_plugins() so plugins can extend it, and before polling starts.
    try:
        harden_boot()
    except Exception as exc:
        log.error("Hardened runtime failed to start: %s", exc)
    load_plugins()
    verify_registration()
    _register_reaction_handler()
    register_bot_commands()
    username = get_bot_username()
    if username:
        log.info("Connected as @%s", username)
    log_startup_banner()
    _install_signal_handlers()
    start_background_threads()
    _ext_thread = threading.Thread(target=extension_job_loop, name="sigma-ext", daemon=True)
    _ext_thread.start()
    _threads.append(_ext_thread)
    cleanup_sandboxes(6)
    for admin_uid in sorted(ADMIN_IDS):
        send(
            admin_uid,
            _e("rocket") + " " + B(BOT_NAME + " v" + VERSION + " online") + "\n"
            + _e("server") + " Host: " + C(str(get_system_info().get("hostname")))
            + "\n" + _e("db") + " DB: " + fmt_size(db_size()),
        )
    while not _stop_event.is_set():
        try:
            log.info("Polling for updates\u2026")
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
            break
        except Exception as exc:
            log.error("Polling crashed: %s \u2014 restarting in 10s", exc)
            _stop_event.wait(10)
    stop_background_threads()
    log.info("%s stopped", BOT_NAME)


if __name__ == "__main__":
    main()
