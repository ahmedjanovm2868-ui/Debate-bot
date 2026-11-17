import os
import sqlite3
import telebot
from telebot import types
from dotenv import load_dotenv

# Load environment variables (from Render or .env file)
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_IDS = [
    int(chat_id.strip())
    for chat_id in os.getenv("ADMIN_CHAT_ID", "").split(",")
    if chat_id.strip()
]

bot = telebot.TeleBot(TOKEN)

# Database Setup
DB_NAME = "debate_club.db"


def init_db():
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  # Users table for persistent registration
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
  # RSVPs and session data
  cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            status TEXT,
            team TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
  conn.commit()
  conn.close()


init_db()


def is_admin(user_id):
  return user_id in ADMIN_CHAT_IDS


# --- START & REGISTRATION WORKFLOW ---
@bot.message_handler(commands=["start"])
def send_welcome(message):
  user_id = message.from_user.id
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
  user = cursor.fetchone()
  conn.close()

  if user:
    # Already registered, show main menu
    show_main_menu(message)
  else:
    # Start registration: ask for Full Name
    msg = bot.send_message(
        message.chat.id,
        "👋 Welcome to the Debate Club Bot!\n\nPlease enter your **Full Name**"
        " to register:",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(msg, process_name_step)


def process_name_step(message):
  user_id = message.from_user.id
  full_name = message.text.strip()
  username = (
      message.from_user.username
      if message.from_user.username
      else "No username"
  )

  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO users (user_id, username, full_name) VALUES"
      " (?, ?, ?)",
      (user_id, username, full_name),
  )
  conn.commit()
  conn.close()

  bot.send_message(
      message.chat.id,
      f"✅ Thank you, **{full_name}**! You are successfully registered.",
      parse_mode="Markdown",
  )
  show_main_menu(message)


def show_main_menu(message):
  markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
  markup.add(
      types.KeyboardButton("📝 RSVP for Friday"),
      types.KeyboardButton("👤 My Status"),
  )
  bot.send_message(
      message.chat.id,
      "Choose an option below:",
      reply_markup=markup,
  )


# --- RSVP & SESSION WORKFLOW ---
@bot.message_handler(
    func=lambda message: message.text == "📝 RSVP for Friday"
)
def handle_rsvp(message):
  user_id = message.from_user.id
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "INSERT OR REPLACE INTO sessions (user_id, status, team) VALUES (?,"
      " 'Going', NULL)",
      (user_id,),
  )
  conn.commit()
  conn.close()

  bot.send_message(
      message.chat.id,
      "✅ You have successfully registered/RSVP'd for the upcoming Friday"
      " session!",
  )


@bot.message_handler(func=lambda message: message.text == "👤 My Status")
def handle_status(message):
  user_id = message.from_user.id
  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT u.full_name, s.status, s.team FROM users u LEFT JOIN sessions s ON"
      " u.user_id = s.user_id WHERE u.user_id = ?",
      (user_id,),
  )
  data = cursor.fetchone()
  conn.close()

  if data:
    full_name, status, team = data
    status_text = status if status else "Not RSVP'd"
    team_text = team if team else "Not assigned"
    bot.send_message(
        message.chat.id,
        f"📊 **Your Status:**\n- **Name:** {full_name}\n- **RSVP:**"
        f" {status_text}\n- **Team:** {team_text}",
        parse_mode="Markdown",
    )
  else:
    bot.send_message(
        message.chat.id, "⚠️ Please type /start to register first."
    )


# --- ADMIN COMMANDS ---
@bot.message_handler(commands=["all_users"])
def cmd_all_users(message):
  if not is_admin(message.from_user.id):
    bot.send_message(message.chat.id, "⛔ Access denied.")
    return

  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("SELECT full_name, username, user_id FROM users")
  users = cursor.fetchall()
  conn.close()

  if not users:
    bot.send_message(message.chat.id, "📂 No members registered yet.")
    return

  text = "👥 **All Registered Club Members:**\n\n"
  for idx, (fname, uname, uid) in enumerate(users, 1):
    text += f"{idx}. **{fname}** (@{uname}) — `ID: {uid}`\n"

  bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["admin_summary"])
def cmd_admin_summary(message):
  if not is_admin(message.from_user.id):
    bot.send_message(message.chat.id, "⛔ Access denied.")
    return

  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute(
      "SELECT u.full_name, s.status, s.team FROM users u JOIN sessions s ON"
      " u.user_id = s.user_id WHERE s.status = 'Going'"
  )
  going_users = cursor.fetchall()
  conn.close()

  if not going_users:
    bot.send_message(
        message.chat.id, "📋 No members have RSVP'd for this session yet."
    )
    return

  text = "📋 **Friday Session Attendees:**\n\n"
  for idx, (fname, status, team) in enumerate(going_users, 1):
    team_info = f"[{team}]" if team else "[Unassigned]"
    text += f"{idx}. {fname} {team_info}\n"

  bot.send_message(message.chat.id, text, parse_mode="Markdown")


@bot.message_handler(commands=["clean"])
def cmd_clean(message):
  if not is_admin(message.from_user.id):
    bot.send_message(message.chat.id, "⛔ Access denied.")
    return

  conn = sqlite3.connect(DB_NAME)
  cursor = conn.cursor()
  cursor.execute("DELETE FROM sessions")
  conn.commit()
  conn.close()

  bot.send_message(
      message.chat.id,
      "🧹 **Session data cleaned!** All RSVPs and teams have been reset for the"
      " next debate.",
      parse_mode="Markdown",
  )


# --- 24/7 POLLING LOOP ---
if __name__ == "__main__":
  print("🤖 Debate Club Bot is running...")
  bot.infinity_polling(skip_pending=True)
