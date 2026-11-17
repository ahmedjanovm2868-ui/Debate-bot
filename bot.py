import os
import sqlite3
import threading
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
        active = conn.execute("SELECT * FROM sessions WHERE status = 'OPEN' ORDER BY session_id DESC LIMIT 1").fetchone()
        if not active:
            conn.execute("INSERT INTO sessions (title, date, status) VALUES (?, ?, ?)", 
                         ("🔥 Saturday Elite Debate Session", "This Saturday, 15:00", "OPEN"))
        conn.commit()

init_db()

# Telegram Bot Handlers
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    markup = InlineKeyboardMarkup()
    web_app_url = os.getenv("RENDER_EXTERNAL_URL", "http://127.0.0.1:5000")
    
    if user_id in ADMIN_IDS:
        markup.add(InlineKeyboardButton("🛠 Open Admin Control Panel", web_app=WebAppInfo(url=f"{web_app_url}/admin")))
        bot.reply_to(message, "⚡ **Welcome Commander!** You are recognized as an Admin. Open your control panel below:", reply_markup=markup, parse_mode="Markdown")
    else:
        markup.add(InlineKeyboardButton("🚀 Open Debate Mini App", web_app=WebAppInfo(url=web_app_url)))
        bot.reply_to(message, "🌟 **Welcome to IIS Debate Club!** 🎙️\n\nStep into the arena of rhetoric and logic. Tap the button below to register, RSVP, and join upcoming sessions! 👇", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(commands=['admin'])
def admin_command(message):
    user_id = message.from_user.id
    if user_id in ADMIN_IDS:
        markup = InlineKeyboardMarkup()
        web_app_url = f"{os.getenv('RENDER_EXTERNAL_URL', 'http://127.0.0.1:5000')}/admin"
        markup.add(InlineKeyboardButton("🛠 Open Admin Control Panel", web_app=WebAppInfo(url=web_app_url)))
        bot.reply_to(message, "🔐 **Admin Access Granted!** 🛡️\n\nTap below to open your management dashboard:", reply_markup=markup, parse_mode="Markdown")
    else:
        bot.reply_to(message, "🚫 **Access Denied!** You are not authorized to use admin commands. 🛑")

# Frontend Templates with full Matrix aesthetic & colorful UI
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IIS Debate Club Mini App</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        :root {
            --bg-color: #0d0208;
            --matrix-green: #00ff66;
            --dark-green: #003311;
            --panel-bg: #121212;
            --text-color: #00ff66;
            --border-color: #00ff6644;
            --accent-glow: rgba(0, 255, 102, 0.2);
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
            border-radius: 12px;
            box-shadow: 0 0 25px var(--accent-glow);
        }
        h1, h3 { text-align: center; text-transform: uppercase; letter-spacing: 2px; }
        h1 { color: #fff; text-shadow: 0 0 10px var(--matrix-green); }
        .btn {
            background: var(--dark-green);
            color: var(--matrix-green);
            border: 1px solid var(--matrix-green);
            padding: 14px;
            width: 100%;
            cursor: pointer;
            font-weight: bold;
            font-family: inherit;
            margin-top: 12px;
            border-radius: 6px;
            text-transform: uppercase;
            transition: all 0.3s ease;
        }
        .btn:hover { background: var(--matrix-green); color: var(--bg-color); box-shadow: 0 0 15px var(--matrix-green); }
        input {
            width: 100%;
            padding: 12px;
            margin: 10px 0;
            background: #000;
            border: 1px solid var(--border-color);
            color: var(--text-color);
            box-sizing: border-box;
            font-family: inherit;
            border-radius: 6px;
        }
        .card { 
            background: #000000aa; 
            border: 1px solid var(--border-color); 
            padding: 20px; 
            margin-top: 15px; 
            border-radius: 8px; 
            box-shadow: inset 0 0 10px rgba(0,255,102,0.05);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🌐 IIS Debate Club ⚡</h1>
        <div id="app">
            <div id="reg-section" class="card" style="display:none;">
                <h3>📝 Member Registration</h3>
                <p style="text-align:center; color:#88ffbb; font-size:0.9em;">Please enter your details to join the club database:</p>
                <input type="text" id="fullname" placeholder="👤 Full Name (e.g. Alisher Navoi)">
                <input type="text" id="phone" placeholder="📱 Phone Number (+998...)">
                <button class="btn" onclick="registerUser()">✨ Complete Registration</button>
            </div>
            <div id="main-section" style="display:none;">
                <div class="card">
                    <h3>🎯 Upcoming Session</h3>
                    <p id="session-info" style="text-align:center; font-weight:bold; color:#fff; font-size:1.1em;">Loading session...</p>
                    <button class="btn" id="rsvp-btn" onclick="toggleRsvp()">⏳ Loading status...</button>
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
            if(!name || !phone) { alert("⚠️ Please fill in all required fields!"); return; }
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
            document.getElementById('session-info').innerText = `${data.session.title}\\n📅 ${data.session.date}`;
            let btn = document.getElementById('rsvp-btn');
            if(data.attending) {
                btn.innerText = "❌ CANCEL MY ATTENDANCE";
                btn.style.background = "#330000";
                btn.style.borderColor = "#ff0033";
                btn.style.color = "#ff0033";
            } else {
                btn.innerText = "✅ YES, I WILL PARTICIPATE! 🔥";
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
    <title>Admin Dashboard - IIS Debate</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        body { background: #0d0208; color: #00ff66; font-family: 'Courier New', Courier, monospace; padding: 20px; margin: 0; }
        .container { max-width: 600px; margin: 0 auto; background: #121212; border: 1px solid #00ff6644; padding: 20px; border-radius: 12px; box-shadow: 0 0 25px rgba(0,255,102,0.2); }
        h2 { text-align: center; color: #fff; text-shadow: 0 0 10px #00ff66; text-transform: uppercase; letter-spacing: 2px; }
        .card { background: #000; border: 1px solid #00ff6644; padding: 15px; margin-top: 15px; border-radius: 8px; }
        .btn { background: #003311; color: #00ff66; border: 1px solid #00ff66; padding: 14px; width: 100%; cursor: pointer; margin-top: 15px; font-weight: bold; border-radius: 6px; text-transform: uppercase; font-family: inherit; }
        .btn:hover { background: #00ff66; color: #0d0208; box-shadow: 0 0 15px #00ff66; }
        ul { padding-left: 20px; }
        li { margin: 8px 0; color: #88ffbb; }
    </style>
</head>
<body>
    <div class="container">
        <h2>🛡️ Admin Command Center 🛠️</h2>
        <div class="card">
            <h3>👥 Confirmed Participants</h3>
            <div id="participant-list">🔄 Loading roster...</div>
        </div>
        <button class="btn" onclick="broadcastTeams()">📢 Broadcast Channel Link & Announcement 🚀</button>
    </div>
    <script>
        const tg = window.Telegram.WebApp;
        tg.expand();

        async function loadParticipants() {
            let res = await fetch('/api/admin/participants');
            let data = await res.json();
            let html = "<ul>";
            if(data.length === 0) {
                html = "<p style='text-align:center; color:#888;'>No confirmed participants yet for this session. 📭</p>";
            } else {
                data.forEach(p => {
                    html += `<li>👤 <b>${p.full_name}</b> 📞 <code>${p.phone}</code></li>`;
                });
                html += "</ul>";
            }
            document.getElementById('participant-list').innerHTML = html;
        }
        async function broadcastTeams() {
            let res = await fetch('/api/admin/broadcast', {method: 'POST'});
            let data = await res.json();
            alert("✨ " + data.message);
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
        if att and att["status"] == 'GOING':
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
            return jsonify({"message": "No open session found to broadcast."})
        users = conn.execute("""
            SELECT u.telegram_id FROM users u
            JOIN attendance a ON u.telegram_id = a.telegram_id
            WHERE a.session_id = ? AND a.status = 'GOING'
        """, (session["session_id"],)).fetchall()
    
    count = 0
    for u in users:
        try:
            bot.send_message(u["telegram_id"], f"📢 **IIS Debate Announcement!** 🔥\n\nGet ready for our upcoming session. Join our official community channel for updates and motion drops: {CHANNEL_LINK} 🚀", parse_mode="Markdown")
            count += 1
        except Exception as e:
            print(f"Failed to send to {u['telegram_id']}: {e}")
            
    return jsonify({"message": f"Broadcast successfully dispatched to {count} participants! 🎯"})

if __name__ == "__main__":
    def run_bot():
        print("🤖 Telegram Bot Polling Started...")
        bot.infinity_polling(none_stop=True, skip_pending=True)

    bot_thread = threading.Thread(target=run_bot)
    bot_thread.daemon = True
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    print(f"🌐 Flask Server Running on port {port}...")
    app.run(host="0.0.0.0", port=port)
