import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl
from flask import Flask, g, jsonify, render_template_string, request
import requests

app = Flask(__name__)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", "9198533634:AAHGPh3weh5P-bLvT-qBZZiWNjxZhtfJA5U"
)
ADMIN_IDS = [8342484427, 8990367353]  # Muhammadsodiq & Sardor
DEFAULT_CHANNEL_LINK = "https://t.me/debate_IIS"
DB_NAME = "debate_club.db"
TASHKENT_TZ = timezone(timedelta(hours=5))


# --- DATABASE SETUP ---
def get_db():
  db = getattr(g, "_database", None)
  if db is None:
    db = g._database = sqlite3.connect(DB_NAME)
    db.row_factory = sqlite3.Row
  return db


@app.teardown_appcontext
def close_connection(exception):
  db = getattr(g, "_database", None)
  if db is not None:
    db.close()


def get_next_saturday_str():
  now = datetime.now(TASHKENT_TZ)
  # Saturday is weekday 5 (Monday=0 ... Sunday=6)
  days_ahead = (5 - now.weekday()) % 7
  # If it's Saturday after 20:00, schedule for next week's Saturday
  if days_ahead == 0 and now.hour >= 20:
    days_ahead = 7
  target = now + timedelta(days=days_ahead)
  return target.strftime("%B %d, %Y")


def init_db():
  conn = sqlite3.connect(DB_NAME)
  conn.row_factory = sqlite3.Row
  c = conn.cursor()

  c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            username TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_date TEXT NOT NULL,
            status TEXT DEFAULT 'OPEN',
            announced INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

  c.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            session_id INTEGER,
            user_id INTEGER,
            status TEXT,
            team TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, user_id),
            FOREIGN KEY(session_id) REFERENCES sessions(session_id),
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)

  c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

  c.execute(
      "INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_link', ?)",
      (DEFAULT_CHANNEL_LINK,),
  )

  # Ensure at least one active session exists
  active = c.execute(
      "SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC"
      " LIMIT 1"
  ).fetchone()
  if not active:
    c.execute(
        "INSERT INTO sessions (session_date, status) VALUES (?, 'OPEN')",
        (get_next_saturday_str(),),
    )

  conn.commit()
  conn.close()


init_db()


# --- TELEGRAM INITDATA VERIFICATION ---
def verify_telegram_user(init_data_raw):
  """Validates Telegram WebApp initData signature and returns user dict."""
  if not init_data_raw:
    return None
  try:
    parsed = dict(parse_qsl(init_data_raw, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
      return None

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(parsed.items())
    )
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if hmac.compare_digest(calculated_hash, received_hash):
      return json.loads(parsed.get("user", "{}"))
  except Exception:
    pass
  return None


def get_current_user(req_json):
  init_data = req_json.get("initData", "")
  verified = verify_telegram_user(init_data)
  if verified and "id" in verified:
    return verified
  # Fallback only if testing outside Telegram with explicit ID
  fallback_id = req_json.get("fallback_user_id")
  if fallback_id:
    return {"id": int(fallback_id), "username": ""}
  return None


def get_greeting():
  hour = datetime.now(TASHKENT_TZ).hour
  if 5 <= hour < 12:
    return "Good morning! Ready for another debate?"
  elif 12 <= hour < 18:
    return "Good afternoon! We have another debate session coming up."
  else:
    return "Good evening! Are you ready for this Saturday's debate?"


def send_telegram_message(chat_id, text, reply_markup=None):
  url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
  payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
  if reply_markup:
    payload["reply_markup"] = reply_markup
  try:
    res = requests.post(url, json=payload, timeout=10)
    return res.ok
  except Exception as e:
    print(f"Telegram send error to {chat_id}: {e}")
    return False


# --- API ENDPOINTS ---
@app.route("/")
def index():
  return render_template_string(MINI_APP_HTML)


@app.route("/api/state", methods=["POST"])
def api_state():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user:
    return jsonify({"error": "Invalid Telegram session"}), 401

  user_id = int(tg_user["id"])
  is_admin = user_id in ADMIN_IDS
  db = get_db()

  # Update username silently if changed
  if tg_user.get("username"):
    db.execute(
        "UPDATE users SET username = ? WHERE user_id = ?",
        (tg_user["username"], user_id),
    )
    db.commit()

  user = db.execute(
      "SELECT * FROM users WHERE user_id = ?", (user_id,)
  ).fetchone()
  session = db.execute(
      "SELECT * FROM sessions ORDER BY session_id DESC LIMIT 1"
  ).fetchone()
  session_id = session["session_id"]

  att = db.execute(
      "SELECT * FROM attendance WHERE session_id = ? AND user_id = ?",
      (session_id, user_id),
  ).fetchone()

  # All registered users (excluding admins from participant counts if they didn't register)
  all_users = db.execute(
      "SELECT user_id, full_name, phone, username FROM users ORDER BY full_name"
      " ASC"
  ).fetchall()
  att_rows = db.execute(
      "SELECT user_id, status, team FROM attendance WHERE session_id = ?",
      (session_id,),
  ).fetchall()
  att_map = {r["user_id"]: dict(r) for r in att_rows}

  confirmed_list = []
  declined_list = []
  no_response_list = []
  full_roster = []

  for u in all_users:
    uid = u["user_id"]
    record = att_map.get(uid)
    status = record["status"] if record else "NO_RESPONSE"
    team = record["team"] if record else None

    public_entry = {
        "user_id": uid,
        "full_name": u["full_name"],
        "username": u["username"] if u["username"] else "No username",
        "status": status,
        "team": team,
    }
    if is_admin:
      public_entry["phone"] = u["phone"]

    full_roster.append(public_entry)

    if status == "CONFIRMED":
      confirmed_list.append(public_entry)
    elif status in ("NOT_ATTENDING", "CANCELLED"):
      declined_list.append(public_entry)
    else:
      no_response_list.append(public_entry)

  team1 = [p for p in confirmed_list if p["team"] == "TEAM 1"]
  team2 = [p for p in confirmed_list if p["team"] == "TEAM 2"]
  unassigned = [p for p in confirmed_list if not p["team"]]

  channel_row = db.execute(
      "SELECT value FROM settings WHERE key = 'channel_link'"
  ).fetchone()
  channel_link = channel_row["value"] if channel_row else DEFAULT_CHANNEL_LINK

  return jsonify({
      "user_id": user_id,
      "is_admin": is_admin,
      "registered": user is not None,
      "full_name": user["full_name"] if user else "",
      "greeting": get_greeting(),
      "session": {
          "session_id": session_id,
          "session_date": session["session_date"],
          "status": session["status"],
          "announced": bool(session["announced"]),
      },
      "my_attendance": {
          "status": att["status"] if att else "NO_RESPONSE",
          "team": att["team"] if att else None,
      },
      "stats": {
          "total_registered": len(all_users),
          "confirmed": len(confirmed_list),
          "declined": len(declined_list),
          "no_response": len(no_response_list),
      },
      "confirmed_list": confirmed_list,
      "team1": team1,
      "team2": team2,
      "unassigned": unassigned,
      "roster": full_roster if is_admin else [],
      "channel_link": channel_link,
  })


@app.route("/api/register", methods=["POST"])
def api_register():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user:
    return jsonify({"error": "Unauthorized"}), 401

  user_id = int(tg_user["id"])
  if user_id in ADMIN_IDS:
    return jsonify({"error": "Administrators do not register"}), 400

  full_name = (data.get("full_name") or "").strip()
  phone = (data.get("phone") or "").strip()
  username = tg_user.get("username") or "No username"

  if len(full_name) < 3 or len(phone) < 7:
    return (
        jsonify({"error": "Please enter a valid full name and phone number."}),
        400,
    )

  db = get_db()
  existing = db.execute(
      "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
  ).fetchone()
  if existing:
    return jsonify({"error": "You are already registered."}), 400

  db.execute(
      "INSERT INTO users (user_id, full_name, phone, username) VALUES (?, ?, ?,"
      " ?)",
      (user_id, full_name, phone, username),
  )
  db.commit()

  session = db.execute(
      "SELECT session_date FROM sessions ORDER BY session_id DESC LIMIT 1"
  ).fetchone()
  return jsonify({"success": True, "session_date": session["session_date"]})


@app.route("/api/rsvp", methods=["POST"])
def api_rsvp():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user:
    return jsonify({"error": "Unauthorized"}), 401

  user_id = int(tg_user["id"])
  status = data.get("status")  # CONFIRMED, NOT_ATTENDING, CANCELLED
  if status not in ("CONFIRMED", "NOT_ATTENDING", "CANCELLED"):
    return jsonify({"error": "Invalid status"}), 400

  db = get_db()
  user = db.execute(
      "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
  ).fetchone()
  if not user:
    return jsonify({"error": "Please register first."}), 400

  session = db.execute(
      "SELECT * FROM sessions ORDER BY session_id DESC LIMIT 1"
  ).fetchone()
  if session["status"] != "OPEN":
    return (
        jsonify({"error": "Registration for this session is currently closed."}),
        400,
    )

  # Clear team assignment if they cancel or decline
  db.execute(
      """
        INSERT INTO attendance (session_id, user_id, status, team, updated_at)
        VALUES (?, ?, ?, NULL, CURRENT_TIMESTAMP)
        ON CONFLICT(session_id, user_id) DO UPDATE SET
            status = excluded.status,
            team = CASE WHEN excluded.status = 'CONFIRMED' THEN attendance.team ELSE NULL END,
            updated_at = CURRENT_TIMESTAMP
    """,
      (session["session_id"], user_id, status),
  )
  db.commit()
  return jsonify({"success": True})


# --- ADMIN API ENDPOINTS ---
@app.route("/api/admin/assign", methods=["POST"])
def api_admin_assign():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user or int(tg_user["id"]) not in ADMIN_IDS:
    return jsonify({"error": "Forbidden"}), 403

  target_user_id = int(data.get("target_user_id"))
  team = data.get("team")  # 'TEAM 1', 'TEAM 2', or None
  if team not in ("TEAM 1", "TEAM 2", None, ""):
    return jsonify({"error": "Invalid team"}), 400

  team_val = team if team else None
  db = get_db()
  session = db.execute(
      "SELECT session_id FROM sessions ORDER BY session_id DESC LIMIT 1"
  ).fetchone()

  att = db.execute(
      "SELECT status FROM attendance WHERE session_id = ? AND user_id = ?",
      (session["session_id"], target_user_id),
  ).fetchone()
  if not att or att["status"] != "CONFIRMED":
    return (
        jsonify(
            {"error": "Only confirmed participants can be assigned to teams."}
        ),
        400,
    )

  db.execute(
      "UPDATE attendance SET team = ?, updated_at = CURRENT_TIMESTAMP WHERE"
      " session_id = ? AND user_id = ?",
      (team_val, session["session_id"], target_user_id),
  )
  db.commit()
  return jsonify({"success": True})


@app.route("/api/admin/announce", methods=["POST"])
def api_admin_announce():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user or int(tg_user["id"]) not in ADMIN_IDS:
    return jsonify({"error": "Forbidden"}), 403

  db = get_db()
  session = db.execute(
      "SELECT * FROM sessions ORDER BY session_id DESC LIMIT 1"
  ).fetchone()
  session_id = session["session_id"]

  if session["announced"]:
    return (
        jsonify({
            "error": (
                "Teams for this session have already been announced! Start a"
                " new session to announce again."
            )
        }),
        400,
    )

  rows = db.execute(
      """
        SELECT u.user_id, u.full_name, a.team
        FROM users u
        JOIN attendance a ON u.user_id = a.user_id
        WHERE a.session_id = ? AND a.status = 'CONFIRMED' AND a.team IN ('TEAM 1', 'TEAM 2')
    """,
      (session_id,),
  ).fetchall()

  if not rows:
    return (
        jsonify({
            "error": (
                "No confirmed participants have been assigned to Team 1 or Team"
                " 2 yet."
            )
        }),
        400,
    )

  team1_names = [r["full_name"] for r in rows if r["team"] == "TEAM 1"]
  team2_names = [r["full_name"] for r in rows if r["team"] == "TEAM 2"]

  channel_row = db.execute(
      "SELECT value FROM settings WHERE key = 'channel_link'"
  ).fetchone()
  channel_link = channel_row["value"] if channel_row else DEFAULT_CHANNEL_LINK

  sent_count = 0
  failed_count = 0

  for r in rows:
    uid = r["user_id"]
    first_name = r["full_name"].split()[0]
    my_team = r["team"]
    teammates = team1_names if my_team == "TEAM 1" else team2_names
    teammates_formatted = "\n".join(f"- {name}" for name in teammates)

    msg = (
        f"Hello, {first_name}!\n\n"
        "The teams for our upcoming Saturday debate have officially been"
        " announced!\n\n"
        f"<b>YOUR TEAM: {my_team}</b>\n\n"
        "<b>YOUR TEAMMATES:</b>\n"
        f"{teammates_formatted}\n\n"
        "Prepare your arguments, organize your ideas, and get ready for an"
        " exciting debate!\n\n"
        "See you on Saturday!\n\n"
        "You can find all additional information in our Debate Club"
        f" channel:\n{channel_link}"
    )
    if send_telegram_message(uid, msg):
      sent_count += 1
    else:
      failed_count += 1

  db.execute(
      "UPDATE sessions SET announced = 1 WHERE session_id = ?", (session_id,)
  )
  db.commit()

  return jsonify({"success": True, "sent": sent_count, "failed": failed_count})


@app.route("/api/admin/new_session", methods=["POST"])
def api_admin_new_session():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user or int(tg_user["id"]) not in ADMIN_IDS:
    return jsonify({"error": "Forbidden"}), 403

  custom_date = (data.get("session_date") or "").strip()
  new_date = custom_date if custom_date else get_next_saturday_str()

  db = get_db()
  db.execute("UPDATE sessions SET status = 'CLOSED'")
  db.execute(
      "INSERT INTO sessions (session_date, status, announced) VALUES (?,"
      " 'OPEN', 0)",
      (new_date,),
  )
  db.commit()
  return jsonify({"success": True, "session_date": new_date})


@app.route("/api/admin/update_channel", methods=["POST"])
def api_admin_update_channel():
  data = request.json or {}
  tg_user = get_current_user(data)
  if not tg_user or int(tg_user["id"]) not in ADMIN_IDS:
    return jsonify({"error": "Forbidden"}), 403

  new_link = (data.get("channel_link") or "").strip()
  if not new_link:
    return jsonify({"error": "Link cannot be empty"}), 400

  db = get_db()
  db.execute(
      "INSERT OR REPLACE INTO settings (key, value) VALUES ('channel_link', ?)",
      (new_link,),
  )
  db.commit()
  return jsonify({"success": True})


# --- BACKGROUND BOT POLLING (Handles /start & /admin inside the same service) ---
def bot_polling_worker():
  offset = None
  while True:
    try:
      render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
      url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
      params = {"timeout": 30}
      if offset:
        params["offset"] = offset
      resp = requests.get(url, params=params, timeout=35).json()

      for update in resp.get("result", []):
        offset = update["update_id"] + 1
        msg = update.get("message")
        if not msg:
          continue

        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = (msg.get("text") or "").strip()

        if not render_url:
          send_telegram_message(
              chat_id,
              "⚠️ Server URL is still initializing. Please try again in a"
              " moment.",
          )
          continue

        if text.startswith("/start"):
          keyboard = {
              "inline_keyboard": [[{
                  "text": "🚀 Open Debate Club",
                  "web_app": {"url": f"{render_url}/"},
              }]]
          }
          send_telegram_message(
              chat_id,
              "👋 <b>Welcome to the Debate Club!</b>\n\nTap the button below"
              " to open the interactive Mini App:",
              reply_markup=keyboard,
          )

        elif text.startswith("/admin"):
          if user_id in ADMIN_IDS:
            keyboard = {
                "inline_keyboard": [[{
                    "text": "👑 OPEN ADMIN DASHBOARD",
                    "web_app": {"url": f"{render_url}/?tab=admin"},
                }]]
            }
            send_telegram_message(
                chat_id,
                "🔐 <b>Administrator Access Verified</b>\n\nTap below to manage"
                " sessions, attendance, and teams:",
                reply_markup=keyboard,
            )
          else:
            send_telegram_message(
                chat_id, "⛔ Access denied. Administrators only."
            )

    except Exception as e:
      print(f"Polling error: {e}")
      time.sleep(3)


threading.Thread(target=bot_polling_worker, daemon=True).start()


# --- FRONTEND MINI APP (Matrix Dark Theme + Reference Layout) ---
MINI_APP_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Debate Club</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {
            --bg-dark: #070b07;
            --card-bg: #101810;
            --card-border: #1d2e1d;
            --neon-green: #00ff66;
            --gold-accent: #ffcc00;
            --danger-red: #ff4d4d;
            --text-muted: #849b84;
        }
        * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        body {
            background-color: var(--bg-dark);
            color: #ffffff;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 16px;
            padding-bottom: 40px;
            overflow-x: hidden;
        }
        canvas#matrix-bg {
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            z-index: -1;
            opacity: 0.14;
            pointer-events: none;
        }
        .header-card {
            background: rgba(16, 24, 16, 0.92);
            border: 1px solid var(--card-border);
            border-radius: 18px;
            padding: 20px;
            backdrop-filter: blur(8px);
        }
        .header-title {
            font-size: 24px;
            font-weight: 900;
            letter-spacing: 0.5px;
            margin: 0;
            color: #ffffff;
        }
        .header-title span { color: var(--gold-accent); }
        .header-sub {
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 700;
            margin-top: 6px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
        }
        .stats-row {
            display: flex;
            justify-content: space-between;
            margin-top: 22px;
            text-align: center;
        }
        .stat-box { flex: 1; }
        .stat-val { font-size: 26px; font-weight: 800; line-height: 1.1; }
        .stat-lbl {
            font-size: 10px;
            font-weight: 700;
            color: var(--text-muted);
            margin-top: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .tabs {
            display: flex;
            gap: 8px;
            margin-top: 16px;
            overflow-x: auto;
            padding-bottom: 4px;
        }
        .tabs::-webkit-scrollbar { display: none; }
        .tab-btn {
            background: #162216;
            color: var(--text-muted);
            border: 1px solid var(--card-border);
            padding: 9px 16px;
            border-radius: 20px;
            font-weight: 700;
            font-size: 12px;
            cursor: pointer;
            white-space: nowrap;
            transition: 0.2s;
        }
        .tab-btn.active {
            background: #ffffff;
            color: #000000;
            border-color: #ffffff;
        }
        .card {
            background: rgba(16, 24, 16, 0.92);
            border: 1px solid var(--card-border);
            border-radius: 18px;
            padding: 18px;
            margin-top: 14px;
            backdrop-filter: blur(8px);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
        }
        .card-title {
            font-size: 15px;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin: 0;
        }
        .badge-count {
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 600;
        }
        .pill-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .player-pill {
            background: rgba(0, 255, 102, 0.08);
            border: 1px solid rgba(0, 255, 102, 0.45);
            color: var(--neon-green);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
        }
        .team-card {
            border-radius: 18px;
            overflow: hidden;
            margin-top: 14px;
            background: rgba(16, 24, 16, 0.94);
            border: 1px solid var(--card-border);
        }
        .team-banner-1 {
            background: linear-gradient(135deg, #ffcc00 0%, #e6b800 100%);
            color: #000000;
            padding: 14px 18px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 900;
            font-size: 15px;
        }
        .team-banner-2 {
            background: linear-gradient(135deg, #00ff66 0%, #00cc52 100%);
            color: #000000;
            padding: 14px 18px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 900;
            font-size: 15px;
        }
        .team-body { padding: 8px 18px; }
        .member-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 11px 0;
            border-bottom: 1px solid #1a281a;
            font-size: 14px;
        }
        .member-row:last-child { border-bottom: none; }
        .avatar-circle {
            width: 26px; height: 26px;
            border-radius: 6px;
            background: #1f301f;
            color: #a3c2a3;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 12px;
            margin-right: 10px;
        }
        input, select {
            width: 100%;
            padding: 13px;
            margin-top: 10px;
            background: #0b120b;
            border: 1px solid #263d26;
            color: #ffffff;
            border-radius: 12px;
            font-size: 14px;
            outline: none;
        }
        input:focus { border-color: var(--neon-green); }
        .btn {
            width: 100%;
            padding: 14px;
            margin-top: 12px;
            border-radius: 12px;
            font-weight: 800;
            font-size: 14px;
            border: none;
            cursor: pointer;
            text-transform: uppercase;
            letter-spacing: 0.4px;
            transition: transform 0.1s;
        }
        .btn:active { transform: scale(0.98); }
        .btn-green { background: var(--neon-green); color: #000000; }
        .btn-gold { background: var(--gold-accent); color: #000000; }
        .btn-red {
            background: rgba(255, 77, 77, 0.14);
            color: var(--danger-red);
            border: 1px solid rgba(255, 77, 77, 0.4);
        }
        .btn-outline {
            background: transparent;
            color: #ffffff;
            border: 1px solid #2c442c;
        }
        .mini-btn {
            padding: 6px 10px;
            font-size: 11px;
            font-weight: 800;
            border-radius: 8px;
            border: none;
            cursor: pointer;
            margin-left: 4px;
        }
        .hidden { display: none !important; }
    </style>
</head>
<body>

<canvas id="matrix-bg"></canvas>

<!-- Top Header Card -->
<div class="header-card">
    <h1 class="header-title">DEBATE <span>CLUB</span></h1>
    <div class="header-sub" id="session-date-header">SATURDAY · LOADING...</div>

    <div class="stats-row">
        <div class="stat-box">
            <div class="stat-val" id="stat-going" style="color: var(--neon-green);">0</div>
            <div class="stat-lbl">CONFIRMED</div>
        </div>
        <div class="stat-box">
            <div class="stat-val" id="stat-declined" style="color: var(--danger-red);">0</div>
            <div class="stat-lbl">NOT ATTENDING</div>
        </div>
        <div class="stat-box">
            <div class="stat-val" id="stat-pending" style="color: #ffffff;">0</div>
            <div class="stat-lbl">NO RESPONSE</div>
        </div>
    </div>
</div>

<!-- Navigation Pills -->
<div class="tabs" id="nav-tabs">
    <button class="tab-btn active" id="tab-btn-session" onclick="switchTab('session')">SESSION</button>
    <button class="tab-btn" id="tab-btn-teams" onclick="switchTab('teams')">TEAMS</button>
    <button class="tab-btn hidden" id="tab-btn-admin" onclick="switchTab('admin')">ADMIN PANEL</button>
</div>

<!-- Loading State -->
<div id="loading-box" class="card" style="text-align:center; color: var(--text-muted);">
    Connecting to Debate Club...
</div>

<!-- VIEW 1: FIRST-TIME REGISTRATION -->
<div id="view-register" class="card hidden">
    <h2 class="card-title" style="color: var(--neon-green); font-size: 18px;">Welcome to the Debate Club</h2>
    <p style="font-size: 13px; color: var(--text-muted); line-height: 1.5;">
        Register once to join our weekly Saturday debate sessions. Your profile will be saved automatically for all future debates.
    </p>
    <input type="text" id="reg-name" placeholder="Full Name (Real First Name & Surname)">
    <input type="tel" id="reg-phone" placeholder="Phone Number (+998 90 123 45 67)">
    <button class="btn btn-green" onclick="submitRegistration()">SUBMIT / COMPLETE REGISTRATION</button>
</div>

<!-- VIEW 1B: REGISTRATION SUCCESS -->
<div id="view-reg-success" class="card hidden" style="border-color: var(--neon-green);">
    <h2 class="card-title" style="color: var(--neon-green); font-size: 18px;">CONGRATULATIONS!</h2>
    <p style="font-size: 14px; line-height: 1.6; margin-top: 10px;">
        You have successfully registered for our Debate Club.<br><br>
        Your next debate session will take place on <b>Saturday, <span id="success-date"></span></b>.<br><br>
        Stay tuned and get ready to debate! See you there!
    </p>
    <button class="btn btn-gold" onclick="loadState()">CONTINUE TO DASHBOARD</button>
</div>

<!-- VIEW 2: SESSION & ATTENDANCE (Returning Participants & Overview) -->
<div id="view-session" class="hidden">
    <!-- Participant RSVP Box (Hidden for Admins) -->
    <div id="rsvp-card" class="card hidden">
        <div id="greeting-text" style="font-size: 15px; font-weight: 700; color: var(--neon-green);"></div>
        <div style="margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--card-border);">
            <div style="font-size: 11px; color: var(--text-muted); font-weight: 700;">OUR NEXT DEBATE SESSION</div>
            <div style="font-size: 16px; font-weight: 800; margin-top: 2px;" id="rsvp-session-date">Saturday</div>
            <p style="font-size: 13px; color: #d0e0d0; margin-top: 8px;">Would you like to participate in this session?</p>
        </div>

        <div id="rsvp-buttons">
            <button class="btn btn-green" onclick="sendRsvp('CONFIRMED')">YES, I WILL PARTICIPATE</button>
            <button class="btn btn-red" onclick="sendRsvp('NOT_ATTENDING')">NO, I CANNOT PARTICIPATE</button>
        </div>

        <div id="rsvp-current-box" class="hidden" style="margin-top: 12px; text-align: center;">
            <div id="rsvp-status-msg" style="font-size: 14px; font-weight: 700; margin-bottom: 8px;"></div>
            <button id="btn-cancel-rsvp" class="btn btn-red hidden" onclick="sendRsvp('CANCELLED')">CANCEL MY ATTENDANCE</button>
            <button id="btn-reconfirm-rsvp" class="btn btn-green hidden" onclick="sendRsvp('CONFIRMED')">CHANGE TO: YES, I WILL PARTICIPATE</button>
        </div>
    </div>

    <!-- Confirmed Attendees Pill List (Matches Reference "KIMLAR KELADI") -->
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">CONFIRMED PARTICIPANTS</h3>
            <span class="badge-count" id="confirmed-count-badge">0 members</span>
        </div>
        <div class="pill-list" id="confirmed-pills"></div>
    </div>
</div>

<!-- VIEW 3: TEAMS DISPLAY (Matches Reference "1 KAMANDA / 2 KAMANDA") -->
<div id="view-teams" class="hidden">
    <div class="team-card">
        <div class="team-banner-1">
            <span>🛡️ TEAM 1</span>
            <span id="team1-count">0 members</span>
        </div>
        <div class="team-body" id="team1-members"></div>
    </div>

    <div class="team-card">
        <div class="team-banner-2">
            <span>⚡ TEAM 2</span>
            <span id="team2-count">0 members</span>
        </div>
        <div class="team-body" id="team2-members"></div>
    </div>
</div>

<!-- VIEW 4: ADMIN DASHBOARD (Verified Backend Access Only) -->
<div id="view-admin" class="hidden">
    <!-- Team Builder & Announcement -->
    <div class="card" style="border-color: var(--gold-accent);">
        <div class="card-header">
            <h3 class="card-title" style="color: var(--gold-accent);">👑 TEAM MANAGEMENT</h3>
            <span class="badge-count" id="unassigned-badge">0 unassigned</span>
        </div>
        <p style="font-size: 12px; color: var(--text-muted); margin-top: 0;">
            Assign confirmed participants to Team 1 or Team 2, then announce via private Telegram messages.
        </p>
        <div id="admin-team-assign-list"></div>

        <button class="btn btn-gold" id="btn-announce-teams" onclick="announceTeams()" style="margin-top: 16px;">
            📢 CONFIRM AND ANNOUNCE TEAMS
        </button>
    </div>

    <!-- Full Registered Roster (Includes Phone Numbers for Admins Only) -->
    <div class="card">
        <div class="card-header">
            <h3 class="card-title">ALL REGISTERED MEMBERS</h3>
            <span class="badge-count" id="total-roster-badge">0 total</span>
        </div>
        <div id="admin-roster-list"></div>
    </div>

    <!-- Session & Channel Settings -->
    <div class="card">
        <h3 class="card-title">SESSION & CHANNEL SETTINGS</h3>
        <label style="font-size: 11px; color: var(--text-muted); display:block; margin-top:10px;">START NEW SATURDAY SESSION (Resets attendance for new week)</label>
        <input type="text" id="admin-new-date" placeholder="Leave blank for automatic next Saturday">
        <button class="btn btn-outline" onclick="createNewSession()">CREATE NEW SESSION</button>

        <label style="font-size: 11px; color: var(--text-muted); display:block; margin-top:16px;">DEBATE CLUB CHANNEL LINK</label>
        <input type="text" id="admin-channel-link" placeholder="https://t.me/debate_IIS">
        <button class="btn btn-outline" onclick="updateChannelLink()">SAVE CHANNEL LINK</button>
    </div>
</div>

<script>
    const tg = window.Telegram.WebApp;
    tg.expand();
    tg.ready();

    let appState = null;

    // Subtle Matrix Digital Rain Background
    const canvas = document.getElementById('matrix-bg');
    const ctx = canvas.getContext('2d');
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    const chars = '01DEBATECLUBABCDEFGHIJKLMNOPQRSTUVWXYZ';
    const fontSize = 13;
    const columns = Math.floor(canvas.width / fontSize);
    const drops = Array(columns).fill(1);

    function drawMatrix() {
        ctx.fillStyle = 'rgba(7, 11, 7, 0.08)';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#00ff66';
        ctx.font = fontSize + 'px monospace';
        for (let i = 0; i < drops.length; i++) {
            const text = chars[Math.floor(Math.random() * chars.length)];
            ctx.fillText(text, i * fontSize, drops[i] * fontSize);
            if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) drops[i] = 0;
            drops[i]++;
        }
    }
    setInterval(drawMatrix, 65);

    function getAuthPayload(extra = {}) {
        return Object.assign({
            initData: tg.initData || '',
            fallback_user_id: tg.initDataUnsafe?.user?.id || null
        }, extra);
    }

    function switchTab(tab) {
        ['session', 'teams', 'admin'].forEach(t => {
            document.getElementById('view-' + t).classList.add('hidden');
            const btn = document.getElementById('tab-btn-' + t);
            if (btn) btn.classList.remove('active');
        });
        document.getElementById('view-' + tab).classList.remove('hidden');
        const activeBtn = document.getElementById('tab-btn-' + tab);
        if (activeBtn) activeBtn.classList.add('active');
    }

    function loadState() {
        fetch('/api/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload())
        })
        .then(r => r.json())
        .then(data => {
            document.getElementById('loading-box').classList.add('hidden');
            if (data.error) {
                alert(data.error);
                return;
            }
            appState = data;

            // Update Header & Stats
            document.getElementById('session-date-header').innerText = `SATURDAY, ${data.session.session_date.toUpperCase()}`;
            document.getElementById('stat-going').innerText = data.stats.confirmed;
            document.getElementById('stat-declined').innerText = data.stats.declined;
            document.getElementById('stat-pending').innerText = data.stats.no_response;

            document.getElementById('view-register').classList.add('hidden');
            document.getElementById('view-reg-success').classList.add('hidden');

            // First-time non-admin participant -> show registration form only
            if (!data.is_admin && !data.registered) {
                document.getElementById('nav-tabs').classList.add('hidden');
                document.getElementById('view-register').classList.remove('hidden');
                return;
            }

            document.getElementById('nav-tabs').classList.remove('hidden');

            // Admin visibility
            if (data.is_admin) {
                document.getElementById('tab-btn-admin').classList.remove('hidden');
                document.getElementById('rsvp-card').classList.add('hidden');
                document.getElementById('admin-channel-link').value = data.channel_link;
                renderAdminView(data);
            } else {
                document.getElementById('rsvp-card').classList.remove('hidden');
                document.getElementById('greeting-text').innerText = `${data.greeting} (${data.full_name})`;
                document.getElementById('rsvp-session-date').innerText = `Saturday, ${data.session.session_date}`;

                const st = data.my_attendance.status;
                const btns = document.getElementById('rsvp-buttons');
                const curBox = document.getElementById('rsvp-current-box');
                const statusMsg = document.getElementById('rsvp-status-msg');
                const btnCancel = document.getElementById('btn-cancel-rsvp');
                const btnReconfirm = document.getElementById('btn-reconfirm-rsvp');

                if (st === 'NO_RESPONSE') {
                    btns.classList.remove('hidden');
                    curBox.classList.add('hidden');
                } else {
                    btns.classList.add('hidden');
                    curBox.classList.remove('hidden');
                    if (st === 'CONFIRMED') {
                        statusMsg.style.color = 'var(--neon-green)';
                        statusMsg.innerText = '✅ CONFIRMED — You are participating this Saturday!';
                        btnCancel.classList.remove('hidden');
                        btnReconfirm.classList.add('hidden');
                    } else {
                        statusMsg.style.color = 'var(--danger-red)';
                        statusMsg.innerText = st === 'CANCELLED' ? '⚠️ ATTENDANCE CANCELLED' : '❌ NOT ATTENDING';
                        btnCancel.classList.add('hidden');
                        btnReconfirm.classList.remove('hidden');
                    }
                }
            }

            // Render Confirmed Pills
            const pills = document.getElementById('confirmed-pills');
            document.getElementById('confirmed-count-badge').innerText = `${data.confirmed_list.length} members`;
            pills.innerHTML = data.confirmed_list.length
                ? data.confirmed_list.map(p => `<span class="player-pill">${p.full_name}</span>`).join('')
                : '<span style="color:var(--text-muted); font-size:13px;">No confirmed participants yet.</span>';

            // Render Teams
            renderTeamList('team1-members', 'team1-count', data.team1);
            renderTeamList('team2-members', 'team2-count', data.team2);

            // Check URL param for ?tab=admin
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.get('tab') === 'admin' && data.is_admin) {
                switchTab('admin');
            } else {
                switchTab('session');
            }
        });
    }

    function renderTeamList(containerId, countId, list) {
        document.getElementById(countId).innerText = `${list.length} members`;
        const el = document.getElementById(containerId);
        if (!list.length) {
            el.innerHTML = '<div style="padding:12px 0; color:var(--text-muted); font-size:13px;">No members assigned yet.</div>';
            return;
        }
        el.innerHTML = list.map(p => `
            <div class="member-row">
                <div><span class="avatar-circle">${p.full_name.charAt(0).toUpperCase()}</span><b>${p.full_name}</b></div>
                <span style="color:var(--text-muted); font-size:12px;">Debater</span>
            </div>
        `).join('');
    }

    function renderAdminView(data) {
        document.getElementById('unassigned-badge').innerText = `${data.unassigned.length} unassigned`;
        const assignBox = document.getElementById('admin-team-assign-list');

        if (!data.confirmed_list.length) {
            assignBox.innerHTML = '<div style="color:var(--text-muted); font-size:13px;">No confirmed participants to assign yet.</div>';
        } else {
            assignBox.innerHTML = data.confirmed_list.map(p => `
                <div class="member-row">
                    <div>
                        <b>${p.full_name}</b><br>
                        <span style="font-size:11px; color:var(--gold-accent);">${p.team || 'Unassigned'}</span>
                    </div>
                    <div>
                        <button class="mini-btn" style="background:var(--gold-accent); color:#000;" onclick="assignTeam(${p.user_id}, 'TEAM 1')">T1</button>
                        <button class="mini-btn" style="background:var(--neon-green); color:#000;" onclick="assignTeam(${p.user_id}, 'TEAM 2')">T2</button>
                        <button class="mini-btn" style="background:#263626; color:#fff;" onclick="assignTeam(${p.user_id}, '')">Clear</button>
                    </div>
                </div>
            `).join('');
        }

        const announceBtn = document.getElementById('btn-announce-teams');
        if (data.session.announced) {
            announceBtn.innerText = '✅ TEAMS ALREADY ANNOUNCED';
            announceBtn.style.opacity = '0.6';
        } else {
            announceBtn.innerText = '📢 CONFIRM AND ANNOUNCE TEAMS';
            announceBtn.style.opacity = '1';
        }

        document.getElementById('total-roster-badge').innerText = `${data.roster.length} total`;
        document.getElementById('admin-roster-list').innerHTML = data.roster.map(p => `
            <div class="member-row">
                <div>
                    <b>${p.full_name}</b> <span style="color:var(--text-muted); font-size:12px;">(${p.username !== 'No username' ? '@' + p.username : 'No username'})</span><br>
                    <span style="font-size:12px; color:#a3c2a3;">📞 ${p.phone}</span>
                </div>
                <span style="font-size:11px; font-weight:800; color:${p.status === 'CONFIRMED' ? 'var(--neon-green)' : (p.status === 'NO_RESPONSE' ? 'var(--text-muted)' : 'var(--danger-red)')};">
                    ${p.status}
                </span>
            </div>
        `).join('');
    }

    function submitRegistration() {
        const full_name = document.getElementById('reg-name').value.trim();
        const phone = document.getElementById('reg-phone').value.trim();
        if (!full_name || !phone) {
            alert('Please fill in both your Full Name and Phone Number.');
            return;
        }
        fetch('/api/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload({ full_name, phone }))
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            document.getElementById('view-register').classList.add('hidden');
            document.getElementById('success-date').innerText = res.session_date;
            document.getElementById('view-reg-success').classList.remove('hidden');
        });
    }

    function sendRsvp(status) {
        fetch('/api/rsvp', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload({ status }))
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            loadState();
        });
    }

    function assignTeam(target_user_id, team) {
        fetch('/api/admin/assign', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload({ target_user_id, team }))
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            loadState();
        });
    }

    function announceTeams() {
        if (!appState) return;
        if (appState.unassigned.length > 0) {
            if (!confirm(`Warning: ${appState.unassigned.length} confirmed participant(s) are still unassigned. Continue announcing?`)) return;
        } else {
            if (!confirm('Confirm and send private team announcements to all assigned participants?')) return;
        }

        fetch('/api/admin/announce', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload())
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            alert(`Announcements sent! Delivered: ${res.sent}, Failed: ${res.failed}`);
            loadState();
        });
    }

    function createNewSession() {
        const session_date = document.getElementById('admin-new-date').value.trim();
        if (!confirm('Start a new Saturday session? Previous session attendance will be archived and reset for the new week.')) return;
        fetch('/api/admin/new_session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload({ session_date }))
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            alert(`New session created for Saturday, ${res.session_date}`);
            loadState();
        });
    }

    function updateChannelLink() {
        const channel_link = document.getElementById('admin-channel-link').value.trim();
        fetch('/api/admin/update_channel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(getAuthPayload({ channel_link }))
        })
        .then(r => r.json())
        .then(res => {
            if (res.error) { alert(res.error); return; }
            alert('Debate Club channel link updated!');
            loadState();
        });
    }

    window.onload = loadState;
</script>
</body>
</html>
"""

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
