#!/usr/bin/env python3
"""SIGMA-HOSTING repair patch.

Fixes the four live errors:
  1. notifications.title NOT NULL  -> rebuilds the stale notifications table
  2. admin_user_files callback     -> unpacks the paginated result properly
  3. menu_security callback        -> get_theme now accepts a row or a user id
  4. "Cannot deliver to 0"         -> notifications to id 0 are skipped

Run it from the folder that holds bot.py, or pass the path:
    python3 sigma_patch.py [/path/to/bot.py]
"""
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


def find_bot():
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser()
    here = Path.cwd() / "bot.py"
    if here.exists():
        return here
    home = Path.home()
    for candidate in sorted(home.glob("**/bot.py")):
        if "sigma" in str(candidate).lower():
            return candidate
    raise SystemExit("Could not find bot.py - pass the path as an argument.")


bot_path = find_bot()
print("Patching:", bot_path)
src = bot_path.read_text(encoding="utf-8")
backup = bot_path.with_suffix(".py.bak")
if not backup.exists():
    shutil.copy2(str(bot_path), str(backup))
    print("Backup saved:", backup)

done = []

# --- fix 1: notifications insert survives an older table layout --------------
old_add = (
    'def add_notification(uid, message, ntype="info"):\n'
    '    """Queue an in-bot notification row."""\n'
    '    return db_exec(\n'
    '        "INSERT INTO notifications (uid, message, type, read, created_at)"\n'
    '        " VALUES (?,?,?,0,?)",\n'
    '        (int(uid), str(message)[:1000], str(ntype)[:24], utcstamp()),\n'
    '    )'
)
new_add = (
    'def add_notification(uid, message, ntype="info"):\n'
    '    """Queue an in-bot notification row.\n'
    '\n'
    '    Older databases shipped a NOT NULL "title" column, so the insert is\n'
    '    built from the columns that actually exist right now.\n'
    '    """\n'
    '    uid = int(uid or 0)\n'
    '    if uid <= 0:\n'
    '        return False\n'
    '    text = str(message)[:1000]\n'
    '    columns = table_columns("notifications")\n'
    '    values = {\n'
    '        "uid": uid,\n'
    '        "message": text,\n'
    '        "type": str(ntype)[:24],\n'
    '        "read": 0,\n'
    '        "created_at": utcstamp(),\n'
    '    }\n'
    '    if "title" in columns:\n'
    '        values["title"] = text[:80] or "Notification"\n'
    '    if "body" in columns:\n'
    '        values["body"] = text\n'
    '    if "text" in columns:\n'
    '        values["text"] = text\n'
    '    if "is_read" in columns:\n'
    '        values["is_read"] = 0\n'
    '    if "seen" in columns:\n'
    '        values["seen"] = 0\n'
    '    use = [key for key in values if not columns or key in columns]\n'
    '    if not use:\n'
    '        return False\n'
    '    sql = ("INSERT INTO notifications (" + ", ".join(use) + ")"\n'
    '           " VALUES (" + ", ".join(["?"] * len(use)) + ")")\n'
    '    return db_exec(sql, tuple(values[key] for key in use))'
)
if old_add in src:
    src = src.replace(old_add, new_add, 1)
    done.append("notifications insert")

# --- fix 2: admin_user_files got a 4-tuple, not a list of rows ---------------
old_rows = "        rows = get_admin_user_files(target, 20)"
new_rows = "        rows = get_admin_user_files(target, 1, 20)[0]"
if old_rows in src:
    src = src.replace(old_rows, new_rows, 1)
    done.append("admin_user_files unpack")

# --- fix 3: get_theme was handed a user row instead of a user id ------------
old_theme = (
    'def get_theme(uid):\n'
    '    """Theme dict for the user."""\n'
    '    key = str(get_user(uid).get("theme") or "sigma")\n'
    '    return THEMES.get(key, THEMES["sigma"])'
)
new_theme = (
    'def get_theme(uid):\n'
    '    """Theme dict for the user. Accepts a user id or a loaded user row."""\n'
    '    row = uid if isinstance(uid, dict) else None\n'
    '    if row is None:\n'
    '        try:\n'
    '            row = get_user(int(uid)) or {}\n'
    '        except (TypeError, ValueError):\n'
    '            row = {}\n'
    '    key = str(row.get("theme") or "sigma")\n'
    '    return THEMES.get(key, THEMES["sigma"])'
)
if old_theme in src:
    src = src.replace(old_theme, new_theme, 1)
    done.append("get_theme accepts a row")

# --- fix 4: never try to message chat id 0 ----------------------------------
lines = src.split("\n")
for i, line in enumerate(lines):
    if line.startswith("def notify_user(uid, text):"):
        indent_at = i + 1
        while indent_at < len(lines) and lines[indent_at].lstrip().startswith('"""'):
            indent_at += 1
        guard = "    if int(uid or 0) <= 0:\n        return False"
        if "int(uid or 0) <= 0" not in "\n".join(lines[i:i + 8]):
            lines.insert(indent_at, guard)
            done.append("notify_user guard")
        break
src = "\n".join(lines)

bot_path.write_text(src, encoding="utf-8")

# --- fix 5: rebuild the stale notifications table in the live database ------
base = Path(os.environ.get("HOME", str(Path.home()))) / "sigma_hosting"
db_file = base / "sigma.db"
if db_file.exists():
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notifications)")]
    notnull = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(notifications)")}
    if cols and (notnull.get("title") or "message" not in cols):
        print("Rebuilding notifications table in", db_file)
        conn.executescript(
            "PRAGMA foreign_keys=OFF;\n"
            "BEGIN;\n"
            "CREATE TABLE IF NOT EXISTS notifications_new (\n"
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
            "  uid INTEGER NOT NULL,\n"
            "  message TEXT DEFAULT '',\n"
            "  type TEXT DEFAULT 'info',\n"
            "  read INTEGER DEFAULT 0,\n"
            "  created_at TEXT DEFAULT ''\n"
            ");\n"
            "COMMIT;"
        )
        msg_col = "message" if "message" in cols else (
            "body" if "body" in cols else ("title" if "title" in cols else "''"))
        type_col = "type" if "type" in cols else "'info'"
        read_col = "read" if "read" in cols else ("is_read" if "is_read" in cols else "0")
        made_col = "created_at" if "created_at" in cols else "''"
        conn.execute(
            "INSERT INTO notifications_new (uid, message, type, read, created_at) "
            "SELECT uid, " + msg_col + ", " + type_col + ", " + read_col
            + ", " + made_col + " FROM notifications")
        conn.execute("DROP TABLE notifications")
        conn.execute("ALTER TABLE notifications_new RENAME TO notifications")
        conn.commit()
        done.append("notifications table rebuilt")
    else:
        print("Notifications table already correct.")
    conn.close()
else:
    print("Database not found at", db_file, "- skipping the table rebuild.")

proc = subprocess.run([sys.executable, "-m", "py_compile", str(bot_path)],
                      capture_output=True, text=True)
if proc.returncode != 0:
    shutil.copy2(str(backup), str(bot_path))
    raise SystemExit("Patch failed to compile, original restored:\n" + proc.stderr[:1500])

print("\nApplied:", ", ".join(done) if done else "nothing (already patched)")
print("bot.py compiles cleanly. Restart the bot now.")
