"""
IIS Debate Club: Telegram Mini App + bot, in one file (beginner friendly).

  * Web server  : serves the Mini App (web/index.html) and a small JSON API.
  * Telegram bot: /start and /admin show a button that opens the Mini App. Team announcements go out as private messages.
  * Database    : SQLite file (data stays after restarts). Backups are made automatically.
  * Security    : every API call is checked with Telegram's signed "initData" (HMAC). Admins are recognised by numeric ID.

Needs Python 3.9+ and one library: pip install pyTelegramBotAPI
Secrets (token, admin IDs) are read from the file ".env" or from environment variables. Never put them in the code.
"""
import functools
import hashlib
import hmac
import html
import json
import os
import re
import sqlite3
import sys
import threading
import time
import traceback
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

import telebot
from telebot import types

BASE = os.path.dirname(os.path.abspath(__file__))


# ============================================================================= settings
def load_env():
    """Read the file .env (KEY=value per line) so secrets are never written inside the code."""
    path = os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def env(*names, default=""):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return default


TOKEN = env("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")
ADMIN_IDS = [int(x) for x in re.findall(r"\d+", env("ADMIN_IDS", "ADMIN_CHAT_ID")) if int(x) > 0]
WEBAPP_URL = env("WEBAPP_URL").rstrip("/")
CHANNEL_LINK_DEFAULT = env("CHANNEL_LINK", default="https://t.me/debate_IIS")
PORT = int(env("PORT", default="8080"))
DB_FILE = env("DB_FILE", default=os.path.join(BASE, "debate_club.db"))
BACKUP_DIR = env("BACKUP_DIR", default=os.path.join(os.path.dirname(os.path.abspath(DB_FILE)), "backups"))
DEV_MODE = env("DEV_MODE") == "1"            # ONLY for testing on your own computer, never on the server
SESSION_START_HOUR = int(env("SESSION_START_HOUR", default="10"))  # Saturday sessions start about this hour
INIT_DATA_MAX_AGE = 24 * 3600                 # signed Telegram data older than this is refused
KEEP_BACKUPS = 14

TASHKENT = timezone(timedelta(hours=5))       # Asia/Tashkent is UTC+5 all year
RUN_ASYNC = True                              # tests switch this off


def NOW():
    return datetime.now(TASHKENT)


try:
    from telebot import apihelper
    apihelper.CONNECT_TIMEOUT = 20
    apihelper.READ_TIMEOUT = 60
    apihelper.RETRY_ON_ERROR = True
except Exception:
    pass

bot = telebot.TeleBot(TOKEN or "000000:missing-token", parse_mode="HTML")


def log(*args):
    print(NOW().strftime("%Y-%m-%d %H:%M:%S"), *args, flush=True)


def esc(text):
    return html.escape(str(text if text is not None else ""), quote=False)


def is_admin(user_id):
    return user_id in ADMIN_IDS


def background(fn, *args):
    """Run slow work (Telegram messages) without making the person wait."""
    if RUN_ASYNC:
        threading.Thread(target=fn, args=args, daemon=True).start()
    else:
        fn(*args)


# ============================================================================= database (SQLite)
def connect():
    c = sqlite3.connect(DB_FILE, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=20000")
    return c


def q(sql, args=(), one=False):
    """Run one SQL command. Returns rows (or one row)."""
    with closing(connect()) as c:
        cur = c.execute(sql, args)
        rows = cur.fetchall() if cur.description else []
        c.commit()
    if one:
        return rows[0] if rows else None
    return rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS participants (
    tg_id INTEGER PRIMARY KEY,
    full_name TEXT NOT NULL,
    phone TEXT NOT NULL UNIQUE,
    username TEXT,
    registered_at TEXT NOT NULL,
    updated_at TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by INTEGER,
    attendance_open INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS attendance (
    session_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    response TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (session_id, tg_id));
CREATE TABLE IF NOT EXISTS teams (
    session_id INTEGER NOT NULL,
    tg_id INTEGER NOT NULL,
    team INTEGER NOT NULL CHECK (team IN (1, 2)),
    announced INTEGER NOT NULL DEFAULT 0,
    announced_at TEXT,
    PRIMARY KEY (session_id, tg_id));
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    admin_id INTEGER,
    sent INTEGER,
    failed INTEGER,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, admin_id INTEGER, action TEXT NOT NULL, details TEXT);
"""


def init_db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_FILE)), exist_ok=True)
    with closing(connect()) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        c.commit()


def get_setting(key, default=None):
    r = q("SELECT v FROM settings WHERE k=?", (key,), one=True)
    return r["v"] if r else default


def set_setting(key, value):
    q("INSERT INTO settings(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))


def stamp():
    return NOW().strftime("%Y-%m-%d %H:%M:%S")


def admin_log(admin_id, action, details=""):
    q("INSERT INTO admin_log(ts, admin_id, action, details) VALUES(?, ?, ?, ?)", (stamp(), admin_id, action, str(details)))
    log("ADMIN", admin_id, action, details)


# ============================================================================= backups
def make_backup(tag="auto"):
    """Safe copy of the database (works while the app is running). Returns the file path."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, "debate-%s-%s.db" % (NOW().strftime("%Y%m%d-%H%M%S"), tag))
    with closing(connect()) as src, closing(sqlite3.connect(path)) as dst:
        src.backup(dst)
    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith("debate-") and f.endswith(".db"))
    for old in files[:-KEEP_BACKUPS]:
        try:
            os.remove(os.path.join(BACKUP_DIR, old))
        except OSError:
            pass
    return path


def restore_backup(path):
    """Replace the live database with a backup file. A safety copy of the current data is made first."""
    try:
        with closing(sqlite3.connect(path)) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"participants", "sessions", "attendance", "teams"} <= tables:
                return False, "This file is not a Debate Club backup."
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False, "The backup file is damaged."
    except sqlite3.Error:
        return False, "This file is not a valid database."
    make_backup("before-restore")
    with closing(sqlite3.connect(path)) as src, closing(connect()) as dst:
        src.backup(dst)
    init_db()  # adds any table the old backup did not have
    return True, "Restored."


# ============================================================================= validation + dates
def valid_name(raw):
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not (3 <= len(s) <= 60):
        return None
    if not s[0].isalpha() or not all(ch.isalpha() or ch in " '’`.-" for ch in s):
        return None
    if len([w for w in s.split(" ") if any(ch.isalpha() for ch in w)]) < 2:
        return None  # first name AND surname
    return s


def normalize_phone(raw):
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) == 9:
        return "+998" + digits
    if len(digits) == 10 and digits.startswith("0"):
        return "+998" + digits[1:]
    if 10 <= len(digits) <= 15:
        return "+" + digits
    return None


def today_str():
    return NOW().strftime("%Y-%m-%d")


def default_saturday(now=None):
    """The Saturday a NEW session gets. Monday-Friday: the coming Saturday.
    On a Saturday: today if it is before SESSION_START_HOUR (not started yet), otherwise next Saturday."""
    now = now or NOW()
    days = (5 - now.weekday()) % 7
    if days == 0 and now.hour >= SESSION_START_HOUR:
        days = 7
    return (now + timedelta(days=days)).strftime("%Y-%m-%d")


def pretty_day(day):
    d = datetime.strptime(day, "%Y-%m-%d")
    return d.strftime("%A, %d %B").replace(", 0", ", ")


def weekday_name(day):
    return datetime.strptime(day, "%Y-%m-%d").strftime("%A")


def greeting(now=None):
    h = (now or NOW()).hour
    if 5 <= h < 12:
        return "Good morning! Ready for another debate?"
    if 12 <= h < 18:
        return "Good afternoon! We have another debate session coming up."
    return "Good evening! Are you ready for this Saturday's debate?"


def first_name_of(full_name):
    return (full_name or "").split(" ")[0] or "friend"


# ============================================================================= Telegram identity (initData)
def verify_init_data(init_data, token=None, max_age=INIT_DATA_MAX_AGE, now=None):
    """Checks the signature Telegram puts on the Mini App data. Returns the user dict, or None if fake or too old."""
    token = token or TOKEN
    if not init_data or not token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join("%s=%s" % (k, v) for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date", "0")) > max_age:
            return None
        user = json.loads(pairs.get("user", ""))
        user["id"] = int(user["id"])
    except (ValueError, KeyError, TypeError):
        return None
    return user


# ============================================================================= Telegram messages (never crash)
def send(chat_id, text, **kwargs):
    """Send a Telegram message. Returns True/False; a blocked user must never stop the rest."""
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except Exception as e:
        log("Could not send to", chat_id, "-", e)
        return False


def notify_admins(text):
    def job():
        for a in ADMIN_IDS:
            send(a, text)
    background(job)


def send_file(chat_id, path, caption):
    try:
        with open(path, "rb") as f:
            bot.send_document(chat_id, f, caption=caption)
        return True
    except Exception as e:
        log("Could not send the file to", chat_id, "-", e)
        return False


# ============================================================================= business logic
class ApiError(Exception):
    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def current_session():
    return q("SELECT * FROM sessions WHERE day >= ? AND status='active' ORDER BY day LIMIT 1", (today_str(),), one=True)


def session_json(s):
    if not s:
        return None
    return {"id": s["id"], "day": s["day"], "pretty": pretty_day(s["day"]), "weekday": weekday_name(s["day"]),
            "attendance_open": bool(s["attendance_open"]), "status": s["status"]}


def channel_link():
    return get_setting("channel_link") or CHANNEL_LINK_DEFAULT


def attendance_of(session_id, tg_id):
    r = q("SELECT status FROM attendance WHERE session_id=? AND tg_id=?", (session_id, tg_id), one=True)
    return r["status"] if r else None


def me_payload(user):
    uid = user["id"]
    if is_admin(uid):
        return {"ok": True, "role": "admin", "name": user.get("first_name", "Admin")}
    p = q("SELECT * FROM participants WHERE tg_id=?", (uid,), one=True)
    s = current_session()
    out = {"ok": True, "role": "participant", "registered": bool(p), "session": session_json(s),
           "channel_link": channel_link(), "greeting": greeting(), "attendance": None, "name": None}
    if p:
        uname = user.get("username") or ""
        if uname != (p["username"] or ""):
            q("UPDATE participants SET username=?, updated_at=? WHERE tg_id=?", (uname, stamp(), uid))
        out["name"] = first_name_of(p["full_name"])
        out["full_name"] = p["full_name"]
        if s:
            out["attendance"] = attendance_of(s["id"], uid) or "no_response"
    return out


def api_me(user, body):
    return me_payload(user)


def api_register(user, body):
    uid = user["id"]
    if is_admin(uid):
        raise ApiError("admin", "Administrators do not need to register.", 403)
    name = valid_name(body.get("full_name"))
    if not name:
        raise ApiError("bad_name", "Please write your real first name and surname (letters only).")
    phone = normalize_phone(body.get("phone"))
    if not phone:
        raise ApiError("bad_phone", "This phone number does not look right. Example: +998 90 123 45 67")
    try:
        with closing(connect()) as c:
            if c.execute("SELECT 1 FROM participants WHERE tg_id=?", (uid,)).fetchone():
                raise ApiError("already_registered", "You are already registered.", 409)
            if c.execute("SELECT 1 FROM participants WHERE phone=?", (phone,)).fetchone():
                raise ApiError("phone_taken", "This phone number is already registered.", 409)
            c.execute("INSERT INTO participants(tg_id, full_name, phone, username, registered_at) VALUES(?, ?, ?, ?, ?)",
                      (uid, name, phone, user.get("username") or "", stamp()))
            c.commit()
    except sqlite3.IntegrityError:
        raise ApiError("already_registered", "You are already registered.", 409)
    log("REGISTERED", uid, name)
    notify_admins("🆕 <b>New participant</b>\n%s\n%s%s" % (
        esc(name), esc(phone), ("\n@" + esc(user["username"])) if user.get("username") else ""))
    out = me_payload(user)
    out["just_registered"] = True
    return out


def api_attendance(user, body):
    uid = user["id"]
    if is_admin(uid):
        raise ApiError("admin", "Administrators do not answer attendance.", 403)
    p = q("SELECT * FROM participants WHERE tg_id=?", (uid,), one=True)
    if not p:
        raise ApiError("not_registered", "Please register first.", 403)
    s = current_session()
    if not s:
        raise ApiError("no_session", "There is no upcoming session yet.", 409)
    answer = body.get("answer")
    if answer not in ("yes", "no", "cancel"):
        raise ApiError("bad_answer", "Unknown answer.")
    old = attendance_of(s["id"], uid)
    if answer == "yes":
        if old == "confirmed":
            return me_payload(user)
        if not s["attendance_open"]:
            raise ApiError("closed", "Attendance confirmation for this session is closed.", 409)
        new_status, response = "confirmed", "yes"
    else:
        if old == "confirmed":
            new_status, response = "cancelled", "cancel"
        elif old in ("not_attending", "cancelled"):
            return me_payload(user)
        else:
            new_status, response = "not_attending", "no"
    with closing(connect()) as c:
        c.execute("INSERT INTO attendance(session_id, tg_id, response, status, updated_at) VALUES(?, ?, ?, ?, ?) "
                  "ON CONFLICT(session_id, tg_id) DO UPDATE SET response=excluded.response, status=excluded.status, "
                  "updated_at=excluded.updated_at", (s["id"], uid, response, new_status, stamp()))
        was_team = None
        if new_status != "confirmed":  # a person who cannot come leaves their team
            r = c.execute("SELECT team FROM teams WHERE session_id=? AND tg_id=?", (s["id"], uid)).fetchone()
            was_team = r["team"] if r else None
            c.execute("DELETE FROM teams WHERE session_id=? AND tg_id=?", (s["id"], uid))
        c.commit()
    label = {"confirmed": "✅ <b>Will come</b>", "not_attending": "❌ <b>Cannot come</b>", "cancelled": "🚫 <b>Cancelled attendance</b>"}[new_status]
    notify_admins("%s: %s (%s)%s" % (label, esc(p["full_name"]), esc(pretty_day(s["day"])),
                                      " · was in Team %d" % was_team if was_team else ""))
    return me_payload(user)


# ----------------------------------------------------------------------------- admin
STATUS_ORDER = {"confirmed": 0, "no_response": 1, "not_attending": 2, "cancelled": 3}


def admin_state(user_id):
    s = current_session()
    people = []
    counts = {"registered": 0, "confirmed": 0, "cannot": 0, "no_response": 0}
    announced = {"done": False}
    if s:
        rows = q("SELECT p.tg_id, p.full_name, p.phone, p.username, a.status AS att, a.updated_at AS att_at, t.team, t.announced "
                 "FROM participants p "
                 "LEFT JOIN attendance a ON a.tg_id=p.tg_id AND a.session_id=? "
                 "LEFT JOIN teams t ON t.tg_id=p.tg_id AND t.session_id=? ORDER BY p.full_name COLLATE NOCASE", (s["id"], s["id"]))
        for r in rows:
            status = r["att"] or "no_response"
            people.append({"tg_id": r["tg_id"], "full_name": r["full_name"], "phone": r["phone"],
                           "username": r["username"] or "", "status": status, "updated_at": r["att_at"],
                           "team": r["team"] if status == "confirmed" else None})
            counts["registered"] += 1
            if status == "confirmed":
                counts["confirmed"] += 1
            elif status == "no_response":
                counts["no_response"] += 1
            else:
                counts["cannot"] += 1
        last = q("SELECT * FROM announcements WHERE session_id=? ORDER BY id DESC LIMIT 1", (s["id"],), one=True)
        if last:
            announced = {"done": True, "at": last["created_at"], "sent": last["sent"], "failed": last["failed"]}
    else:
        counts["registered"] = q("SELECT COUNT(*) n FROM participants", one=True)["n"]
    people.sort(key=lambda p: (STATUS_ORDER[p["status"]], p["full_name"].lower()))
    sessions = [{"id": r["id"], "day": r["day"], "pretty": pretty_day(r["day"]), "status": r["status"],
                 "attendance_open": bool(r["attendance_open"]),
                 "confirmed": q("SELECT COUNT(*) n FROM attendance WHERE session_id=? AND status='confirmed'", (r["id"],), one=True)["n"]}
                for r in q("SELECT * FROM sessions ORDER BY day DESC LIMIT 12")]
    backups = sorted((f for f in os.listdir(BACKUP_DIR) if f.endswith(".db")), reverse=True)[:5] if os.path.isdir(BACKUP_DIR) else []
    return {"ok": True, "role": "admin", "session": session_json(s), "counts": counts, "participants": people,
            "announced": announced, "sessions": sessions, "channel_link": channel_link(), "backups": backups,
            "default_day": default_saturday(), "me": user_id}


def api_admin_state(user, body):
    return admin_state(user["id"])


def api_admin_session_create(user, body):
    day = (body.get("day") or "").strip() or default_saturday()
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        raise ApiError("bad_date", "Please choose a valid date.")
    if day < today_str():
        raise ApiError("past_date", "This date is already in the past.")
    if q("SELECT 1 FROM sessions WHERE day=?", (day,), one=True):
        raise ApiError("exists", "A session on this date already exists.", 409)
    q("INSERT INTO sessions(day, created_at, created_by) VALUES(?, ?, ?)", (day, stamp(), user["id"]))
    admin_log(user["id"], "create_session", day)
    out = admin_state(user["id"])
    out["warning"] = None if d.weekday() == 5 else "That date is not a Saturday."
    return out


def api_admin_session_update(user, body):
    s = q("SELECT * FROM sessions WHERE id=?", (body.get("id"),), one=True)
    if not s:
        raise ApiError("not_found", "Session not found.", 404)
    changes = []
    if "day" in body and body["day"] != s["day"]:
        day = str(body["day"])
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ApiError("bad_date", "Please choose a valid date.")
        if day < today_str():
            raise ApiError("past_date", "This date is already in the past.")
        if q("SELECT 1 FROM sessions WHERE day=? AND id<>?", (day, s["id"]), one=True):
            raise ApiError("exists", "A session on this date already exists.", 409)
        q("UPDATE sessions SET day=? WHERE id=?", (day, s["id"]))
        changes.append("day %s -> %s" % (s["day"], day))
    if "attendance_open" in body:
        q("UPDATE sessions SET attendance_open=? WHERE id=?", (1 if body["attendance_open"] else 0, s["id"]))
        changes.append("attendance_open=%s" % bool(body["attendance_open"]))
    if "status" in body and body["status"] in ("active", "cancelled"):
        q("UPDATE sessions SET status=? WHERE id=?", (body["status"], s["id"]))
        changes.append("status=%s" % body["status"])
    admin_log(user["id"], "update_session", "%s: %s" % (s["id"], "; ".join(changes) or "no change"))
    return admin_state(user["id"])


def api_admin_team_set(user, body):
    s = current_session()
    if not s:
        raise ApiError("no_session", "There is no upcoming session.", 409)
    ids = body.get("tg_ids")
    team = body.get("team")
    if not isinstance(ids, list) or not ids or team not in (1, 2, None):
        raise ApiError("bad_request", "Choose people and a team.")
    try:
        ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ApiError("bad_request", "Bad participant list.")
    marks = ",".join("?" * len(ids))
    ok_ids = {r["tg_id"] for r in q("SELECT tg_id FROM attendance WHERE session_id=? AND status='confirmed' AND tg_id IN (%s)" % marks,
                                    [s["id"]] + ids)}
    if set(ids) - ok_ids:
        raise ApiError("not_confirmed", "Only people who confirmed attendance can be put in a team.", 409)
    with closing(connect()) as c:
        for i in ids:
            if team is None:
                c.execute("DELETE FROM teams WHERE session_id=? AND tg_id=?", (s["id"], i))
            else:  # one row per person per session: nobody can be in both teams
                c.execute("INSERT INTO teams(session_id, tg_id, team) VALUES(?, ?, ?) "
                          "ON CONFLICT(session_id, tg_id) DO UPDATE SET team=excluded.team, announced=0, announced_at=NULL",
                          (s["id"], i, team))
        c.commit()
    admin_log(user["id"], "team_set", "%s -> %s" % (ids, team))
    return admin_state(user["id"])


def team_message(person, team, mates, s, link):
    lines = "\n".join("- " + esc(m) for m in mates) or "You have no teammates yet."
    return ("Hello, %s!\n\nThe teams for our upcoming %s debate have officially been announced!\n\n"
            "<b>YOUR TEAM: TEAM %d</b>\n\n<b>YOUR TEAMMATES:</b>\n%s\n\n"
            "Prepare your arguments, organize your ideas, and get ready for an exciting debate!\n\n"
            "See you on %s!\n\nYou can find all additional information in our Debate Club channel:\n%s" % (
                esc(first_name_of(person)), esc(s["weekday"]), team, lines, esc(s["weekday"]), esc(link)))


ANNOUNCE_LOCK = threading.Lock()


def api_admin_announce(user, body):
    with ANNOUNCE_LOCK:  # two admins pressing together can never send it twice
        s = current_session()
        if not s:
            raise ApiError("no_session", "There is no upcoming session.", 409)
        sj = session_json(s)
        rows = q("SELECT p.tg_id, p.full_name, t.team FROM attendance a JOIN participants p ON p.tg_id=a.tg_id "
                 "LEFT JOIN teams t ON t.tg_id=a.tg_id AND t.session_id=a.session_id "
                 "WHERE a.session_id=? AND a.status='confirmed' ORDER BY p.full_name COLLATE NOCASE", (s["id"],))
        assigned = [r for r in rows if r["team"] in (1, 2)]
        left_out = [r["full_name"] for r in rows if r["team"] not in (1, 2)]
        if not assigned:
            raise ApiError("nobody_assigned", "Nobody is in a team yet.", 409)
        if left_out and not body.get("allow_unassigned"):
            raise ApiError("unassigned", "Some confirmed people have no team.", 409, names=left_out)
        if q("SELECT 1 FROM announcements WHERE session_id=?", (s["id"],), one=True) and not body.get("force"):
            raise ApiError("already_announced", "Teams were already announced for this session.", 409)
        link = channel_link()
        sent, failed = 0, []
        for r in assigned:
            mates = [x["full_name"] for x in assigned if x["team"] == r["team"] and x["tg_id"] != r["tg_id"]]
            if send(r["tg_id"], team_message(r["full_name"], r["team"], mates, sj, link)):
                sent += 1
                q("UPDATE teams SET announced=1, announced_at=? WHERE session_id=? AND tg_id=?", (stamp(), s["id"], r["tg_id"]))
            else:
                failed.append(r["full_name"])
            time.sleep(0.05)
        q("INSERT INTO announcements(session_id, admin_id, sent, failed, created_at) VALUES(?, ?, ?, ?, ?)",
          (s["id"], user["id"], sent, len(failed), stamp()))
        admin_log(user["id"], "announce_teams", "sent=%d failed=%d %s" % (sent, len(failed), failed))
        out = admin_state(user["id"])
        out["result"] = {"sent": sent, "failed": failed, "left_out": left_out}
        return out


def api_admin_settings(user, body):
    link = str(body.get("channel_link", "")).strip()
    if not re.fullmatch(r"https?://[^\s<>\"']{3,200}", link):
        raise ApiError("bad_link", "Please write a full link, for example https://t.me/debate_IIS")
    set_setting("channel_link", link)
    admin_log(user["id"], "set_channel_link", link)
    return admin_state(user["id"])


def api_admin_backup(user, body):
    path = make_backup("manual")
    admin_log(user["id"], "backup", os.path.basename(path))
    ok = send_file(user["id"], path, "Debate Club database backup. Keep it safe: it contains phone numbers.")
    out = admin_state(user["id"])
    out["backup"] = {"name": os.path.basename(path), "sent": ok}
    return out


ROUTES = {  # path -> (function, admin_only)
    "/api/me": (api_me, False),
    "/api/register": (api_register, False),
    "/api/attendance": (api_attendance, False),
    "/api/admin/state": (api_admin_state, True),
    "/api/admin/session/create": (api_admin_session_create, True),
    "/api/admin/session/update": (api_admin_session_update, True),
    "/api/admin/team": (api_admin_team_set, True),
    "/api/admin/announce": (api_admin_announce, True),
    "/api/admin/settings": (api_admin_settings, True),
    "/api/admin/backup": (api_admin_backup, True),
}


def authenticate(headers):
    user = verify_init_data(headers.get("X-Init-Data", ""))
    if user:
        return user
    if DEV_MODE and headers.get("X-Dev-User", "").isdigit():  # testing on your own computer only
        return {"id": int(headers["X-Dev-User"]), "first_name": "Dev", "username": headers.get("X-Dev-Username", "")}
    return None


# ============================================================================= web server
RATE = defaultdict(lambda: deque(maxlen=200))


def rate_limited(uid):
    now = time.time()
    d = RATE[uid]
    while d and now - d[0] > 60:
        d.popleft()
    if len(d) >= 120:
        return True
    d.append(now)
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "DebateClub"

    def log_message(self, fmt, *args):
        pass  # keep the log clean; important events are logged by the app itself

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, payload):
        self._send(status, json.dumps(payload, ensure_ascii=False))

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE, "web", "index.html"), "rb") as f:
                    page = f.read()
            except OSError:
                return self._send(500, "index.html is missing", "text/plain")
            csp = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; "
                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
            return self._send(200, page, "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        self._send(404, "Not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        route = ROUTES.get(path)
        if not route:
            return self._json(404, {"ok": False, "error": "not_found", "message": "Unknown address."})
        func, admin_only = route
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 20000:
                return self._json(413, {"ok": False, "error": "too_big", "message": "Request too big."})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "bad_json", "message": "Bad request."})
        user = authenticate(self.headers)
        if not user:
            return self._json(401, {"ok": False, "error": "unauthorized", "message": "Please open this app from Telegram."})
        if admin_only and not is_admin(user["id"]):  # checked on the server, every single time
            log("DENIED admin call", path, "from", user["id"])
            return self._json(403, {"ok": False, "error": "forbidden", "message": "Administrators only."})
        if rate_limited(user["id"]):
            return self._json(429, {"ok": False, "error": "slow_down", "message": "Too many requests. Wait a moment."})
        try:
            self._json(200, func(user, body))
        except ApiError as e:
            self._json(e.status, dict({"ok": False, "error": e.code, "message": e.message}, **e.extra))
        except sqlite3.Error:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": "database", "message": "Database problem. Please try again."})
        except Exception:
            traceback.print_exc()
            self._json(500, {"ok": False, "error": "server", "message": "Something went wrong. Please try again."})


def make_server(port):
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


# ============================================================================= bot handlers
def private(m):
    return m.chat.type == "private"


def safe(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            traceback.print_exc()
    return wrapper


def app_button(text):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(text, web_app=types.WebAppInfo(url=WEBAPP_URL)))
    return kb


def url_ready():
    return WEBAPP_URL.startswith("https://")


@bot.message_handler(commands=["start", "menu"], func=private)
@safe
def cmd_start(m):
    name = esc(m.from_user.first_name or "friend")
    if not url_ready():
        send(m.chat.id, "The Mini App address is not set up yet. Please try again later.")
        return
    if is_admin(m.from_user.id):
        send(m.chat.id, "Welcome back, <b>%s</b>! 👑\nYou are an administrator. Open the dashboard below." % name,
             reply_markup=app_button("OPEN ADMIN DASHBOARD"))
    else:
        send(m.chat.id, "Welcome to the <b>IIS Debate Club</b>, %s! 🎤\n\nTap the button below to register and to "
                        "confirm your place at our Saturday debates." % name, reply_markup=app_button("Open Debate Club"))


@bot.message_handler(commands=["admin"], func=private)
@safe
def cmd_admin(m):
    if not is_admin(m.from_user.id):
        send(m.chat.id, "This command is only for the club administrators.")
        return
    if not url_ready():
        send(m.chat.id, "Set WEBAPP_URL (an https:// address) first, then restart the app.")
        return
    send(m.chat.id, "Administrator panel 👑", reply_markup=app_button("OPEN ADMIN DASHBOARD"))


@bot.message_handler(commands=["myid"], func=private)
@safe
def cmd_myid(m):
    send(m.chat.id, "Your Telegram ID: <code>%d</code>" % m.from_user.id)


@bot.message_handler(commands=["backup"], func=private)
@safe
def cmd_backup(m):
    if not is_admin(m.from_user.id):
        return
    path = make_backup("manual")
    admin_log(m.from_user.id, "backup", os.path.basename(path))
    send_file(m.chat.id, path, "Database backup. To restore it later, send this file back with the caption /restore")


@bot.message_handler(content_types=["document"], func=lambda m: private(m) and is_admin(m.from_user.id) and (m.caption or "").startswith("/restore"))
@safe
def cmd_restore(m):
    info = bot.get_file(m.document.file_id)
    data = bot.download_file(info.file_path)
    tmp = os.path.join(os.path.dirname(os.path.abspath(DB_FILE)), "restore-upload.tmp.db")
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        ok, msg = restore_backup(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    admin_log(m.from_user.id, "restore", msg)
    send(m.chat.id, ("✅ " if ok else "❌ ") + msg)


@bot.message_handler(content_types=["text"], func=private)
@safe
def any_text(m):
    cmd_start(m)


# ============================================================================= background jobs
def maintenance():
    """Daily backup at 03:00 Tashkent time; every Sunday a copy is also sent to the admins."""
    now = NOW()
    if now.hour != 3 or get_setting("last_backup") == today_str():
        return False
    set_setting("last_backup", today_str())
    path = make_backup("daily")
    log("Daily backup made:", os.path.basename(path))
    if now.weekday() == 6:
        for a in ADMIN_IDS:
            send_file(a, path, "Weekly database backup.")
    return True


def scheduler_loop():
    while True:
        try:
            maintenance()
        except Exception:
            traceback.print_exc()
        time.sleep(60)


# ============================================================================= start
def main():
    if not TOKEN or "PASTE" in TOKEN.upper():
        print("\nSTOP: the bot token is missing.\nWrite it in the file .env after BOT_TOKEN= (or set it on the server).\n")
        sys.exit(1)
    if not ADMIN_IDS:
        print("\nSTOP: no administrator IDs.\nWrite them in .env after ADMIN_IDS= (numbers separated by a comma).\n")
        sys.exit(1)
    init_db()
    if DEV_MODE:
        log("WARNING: DEV_MODE is ON. Never use it on the real server.")
    server = make_server(PORT)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log("Web server listening on port", PORT)
    me = None
    for attempt in range(1, 7):
        try:
            me = bot.get_me()
            break
        except Exception as e:
            if "401" in str(e) or "Unauthorized" in str(e):
                print("\nSTOP: Telegram did not accept this token. Copy it again from @BotFather.\n")
                sys.exit(1)
            log("Cannot reach Telegram yet (try %d of 6). Waiting..." % attempt)
            time.sleep(10)
    if me is None:
        print("\nSTOP: cannot reach Telegram. Check the internet or VPN and run again.\n")
        sys.exit(1)
    try:
        bot.delete_webhook()
    except Exception:
        pass
    try:
        bot.set_my_commands([types.BotCommand("start", "Open the Debate Club"), types.BotCommand("admin", "Admin dashboard (admins only)")])
        if url_ready():
            bot.set_chat_menu_button(None, types.MenuButtonWebApp("Debate Club", types.WebAppInfo(url=WEBAPP_URL)))
    except Exception as e:
        log("Could not set the menu button:", e)
    log("Bot started: @%s" % me.username)
    log("Admins:", ", ".join(map(str, ADMIN_IDS)))
    if not url_ready():
        log("WARNING: WEBAPP_URL is not an https:// address. Telegram will not open the Mini App until it is set.")
    threading.Thread(target=scheduler_loop, daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
