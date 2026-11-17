import os
import sqlite3
from flask import Flask, render_template_string, request, jsonify
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# Configuration & Exact Credentials
TOKEN = "9198533634:AAHGPh3weh5P-bLvT-qBZZiWNjxZhtfJA5U"
bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

ADMIN_IDS = [8342484427, 8990367353]
CHANNEL_LINK = "https://t.me/debate_IIS"

# Database Initialization
def get_db():
    conn = sqlite3.connect("debate_club.db")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                date TEXT,
                status TEXT DEFAULT 'OPEN'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                full_name TEXT,
                phone TEXT,
                team TEXT DEFAULT 'Unassigned'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                telegram_id INTEGER,
                session_id INTEGER,
                status TEXT,
                PRIMARY KEY (telegram_id, session_id)
            )
        """)
        # Ensure a default open session exists
        active = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        if not active:
            conn.execute("INSERT INTO sessions (title, date, status) VALUES (?, ?, ?)", 
                         ("Saturday Debate Session", "This Saturday, 15:00", "OPEN"))
        conn.commit()

init_db()

# Telegram Bot Handlers
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = InlineKeyboardMarkup()
    web_app_url = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:5000")
    markup.add(InlineKeyboardButton("🚀 Open Debate Club", web_app=WebAppInfo(url=web_app_url)))
    bot.reply_to(message, "👋 **Welcome to the Debate Club!**\n\nTap the button below to open the interactive Mini App:", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id in ADMIN_IDS:
        markup = InlineKeyboardMarkup()
        web_app_url = f"{os.getenv('RENDER_EXTERNAL_URL', 'http://127.0.0.1:5000')}/admin"
        markup.add(InlineKeyboardButton("🛠 Open Admin Panel", web_app=WebAppInfo(url=web_app_url)))
        bot.reply_to(message, "🔐 **Admin Verified.** Access your dashboard below:", reply_markup=markup, parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ You are not authorized to use admin commands.")

# Flask Frontend Routes
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Debate Club Mini App</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {
            --bg-color: #0d0208;
            --matrix-green: #00ff66;
            --dark-green: #003311;
            --panel-bg: #121212;
            --text-color: #00ff66;
            --border-color: #00ff6633;
        }
        body {
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: 'Courier New', Courier, monospace;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 600px;
            margin: 0 auto;
            background: var(--panel-bg);
            border: 1px solid var(--border-color);
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 0 20px rgba(0, 255, 102, 0.1);
        }
        h1, h3 { text-align: center; text-transform: uppercase; letter-spacing: 2px; }
        .btn {
            background: var(--dark-green);
            color: var(--matrix-green);
            border: 1px solid var(--matrix-green);
            padding: 12px;
            width: 100%;
            cursor: pointer;
            font-weight: bold;
            font-family: inherit;
            margin-top: 10px;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .btn:hover { background: var(--matrix-green); color: var(--bg-color); }
        input {
            width: 100%;
            padding: 10px;
            margin: 8px 0;
            background: #000;
            border: 1px solid var(--border-color);
            color: var(--text-color);
            box-sizing: border-box;
            font-family: inherit;
        }
        .card { background: #00000088; border: 1px solid var(--border-color); padding: 15px; margin-top: 15px; border-radius: 6px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>IIS Debate Club</h1>
        <div id="app">
            <div id="reg-section" class="card" style="display:none;">
                <h3>Member Registration</h3>
                <input type="text" id="fullname" placeholder="Full Name">
                <input type="text" id="phone" placeholder="Phone Number">
                <button class="btn" onclick="registerUser()">Register</button>
            </div>
            <div id="main-section" style="display:none;">
                <div class="card">
                    <h3>Session Status</h3>
                    <p id="session-info" style="text-align:center;">Loading session...</p>
                    <button class="btn" id="rsvp-btn" onclick="toggleRsvp()">Confirm Attendance</button>
                </div>
            </div>
        </div>
    </div>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();
        const user = tg.initDataUnsafe?.user || { id: 8342484427, first_name: "TestUser" };

        async function init() {
            let res = await fetch(`/api/user/${user.id}`);
            let data = await res.json();
            if (!data.registered) {
                document.getElementById('reg-section').style.display = 'block';
            } else {
                document.getElementById('main-section').style.display = 'block';
                loadSession();
            }
        }

        async function registerUser() {
            let name = document.getElementById('fullname').value;
            let phone = document.getElementById('phone').value;
            if(!name || !phone) { alert("Please fill all fields"); return; }
            await fetch('/api/register', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({telegram_id: user.id, full_name: name, phone: phone})
            });
            document.getElementById('reg-section').style.display = 'none';
            document.getElementById('main-section').style.display = 'block';
            loadSession();
        }

        async function loadSession() {
            let res = await fetch(`/api/session?user_id=${user.id}`);
            let data = await res.json();
            document.getElementById('session-info').innerText = `${data.session.title} (${data.session.date})`;
            let btn = document.getElementById('rsvp-btn');
            if(data.attending) {
                btn.innerText = "CANCEL ATTENDANCE";
                btn.style.background = "#330000";
                btn.style.borderColor = "#ff0033";
                btn.style.color = "#ff0033";
            } else {
                btn.innerText = "YES, I WILL PARTICIPATE";
                btn.style.background = "var(--dark-green)";
                btn.style.borderColor = "var(--matrix-green)";
                btn.style.color = "var(--matrix-green)";
            }
        }

        async function toggleRsvp() {
            await fetch('/api/rsvp', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({telegram_id: user.id})
            });
            loadSession();
        }

        init();
    </script>
</body>
</html>
"""

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Dashboard</title>
    <style>
        body { background: #0d0208; color: #00ff66; font-family: 'Courier New', Courier, monospace; padding: 20px; }
        .container { max-width: 600px; margin: 0 auto; background: #121212; border: 1px solid #00ff6633; padding: 20px; border-radius: 8px; }
        h2 { text-align: center; }
        .card { background: #000; border: 1px solid #00ff6633; padding: 10px; margin-top: 10px; border-radius: 4px; }
        .btn { background: #003311; color: #00ff66; border: 1px solid #00ff66; padding: 10px; width: 100%; cursor: pointer; margin-top: 10px; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <h2>Admin Panel</h2>
        <div class="card">
            <h3>Confirmed Participants</h3>
            <div id="participant-list">Loading...</div>
        </div>
        <button class="btn" onclick="broadcastTeams()">Broadcast Channel Link</button>
    </div>
    <script>
        async function loadParticipants() {
            let res = await fetch('/api/admin/participants');
            let data = await res.json();
            let html = "";
            data.forEach(p => {
                html += `<p>${p.full_name} (${p.phone})</p>`;
            });
            document.getElementById('participant-list').innerHTML = html || "No participants yet.";
        }
        async function broadcastTeams() {
            let res = await fetch('/api/admin/broadcast', {method: 'POST'});
            let data = await res.json();
            alert(data.message);
        }
        loadParticipants();
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/admin")
def admin_panel():
    return render_template_string(ADMIN_TEMPLATE)

@app.route("/api/user/<int:user_id>")
def check_user(user_id):
    with get_db() as conn:
        user = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
    return jsonify({"registered": user is not None})

@app.route("/api/register", methods=["POST"])
def register():
    data = request.json
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO users (telegram_id, full_name, phone) VALUES (?, ?, ?)",
                     (data["telegram_id"], data["full_name"], data["phone"]))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/session")
def session_data():
    user_id = request.args.get("user_id")
    with get_db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        attending = False
        if session and user_id:
            att = conn.execute("SELECT * FROM attendance WHERE telegram_id = ? AND session_id = ? AND status = 'GOING'",
                               (user_id, session["session_id"])).fetchone()
            attending = att is not None
    return jsonify({
        "session": {"title": session["title"], "date": session["date"], "id": session["session_id"]} if session else None,
        "attending": attending
    })

@app.route("/api/rsvp", methods=["POST"])
def rsvp():
    data = request.json
    user_id = data["telegram_id"]
    with get_db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        if not session:
            return jsonify({"success": False})
        att = conn.execute("SELECT * FROM attendance WHERE telegram_id = ? AND session_id = ?",
                           (user_id, session["session_id"])).fetchone()
        if att and att["status"] == "GOING":
            conn.execute("UPDATE attendance SET status = 'NOT_GOING' WHERE telegram_id = ? AND session_id = ?",
                         (user_id, session["session_id"]))
        else:
            conn.execute("INSERT OR REPLACE INTO attendance (telegram_id, session_id, status) VALUES (?, ?, 'GOING')",
                         (user_id, session["session_id"]))
        conn.commit()
    return jsonify({"success": True})

@app.route("/api/admin/participants")
def admin_participants():
    with get_db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        if not session:
            return jsonify([])
        users = conn.execute("""
            SELECT u.full_name, u.phone FROM users u
            JOIN attendance a ON u.telegram_id = a.telegram_id
            WHERE a.session_id = ? AND a.status = 'GOING'
        """, (session["session_id"],)).fetchall()
    return jsonify([dict(u) for u in users])

@app.route("/api/admin/broadcast", methods=["POST"])
def admin_broadcast():
    with get_db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        if not session:
            return jsonify({"message": "No open session found."})
        users = conn.execute("""
            SELECT u.telegram_id FROM users u
            JOIN attendance a ON u.telegram_id = a.telegram_id
            WHERE a.session_id = ? AND a.status = 'GOING'
        """, (session["session_id"],)).fetchall()
    
    count = 0
    for u in users:
        try:
            bot.send_message(u["telegram_id"], f"📢 **Debate Announcement!**\n\nJoin our official channel for updates: {CHANNEL_LINK}", parse_mode="Markdown")
            count += 1
        except Exception as e:
            print(f"Failed to send to {u['telegram_id']}: {e}")
            
    return jsonify({"message": f"Broadcast sent to {count} participants!"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
