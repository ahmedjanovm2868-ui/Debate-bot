import os
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
import telebot
from telebot import types
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_IDS_RAW = os.getenv("ADMIN_CHAT_ID", "")

if not TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is missing in the .env file!")

ADMIN_IDS = [admin_id.strip() for admin_id in ADMIN_CHAT_IDS_RAW.split(",") if admin_id.strip()]

bot = telebot.TeleBot(TOKEN)
DB_FILE = "debate_club.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS participants (
            chat_id INTEGER PRIMARY KEY,
            full_name TEXT,
            phone_number TEXT,
            team TEXT,
            attending INTEGER DEFAULT 1
        )
    ''')
    try:
        cursor.execute("ALTER TABLE participants ADD COLUMN attending INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()

init_db()

# Temporary memory to track who is currently typing their name
registration_in_progress = set()

def is_admin(chat_id):
    return str(chat_id) in ADMIN_IDS

def send_user_menu(chat_id, custom_intro=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT full_name, phone_number, attending FROM participants WHERE chat_id = ?", (chat_id,))
    user = cursor.fetchone()
    conn.close()

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)

    # Not registered yet
    if not user or not user[1]:
        btn_register = types.KeyboardButton("📝 Register for Debate")
        markup.add(btn_register)
        msg = custom_intro or (
            "👋 Welcome to the Debate Club Bot!\n\n"
            "Since this is your first time, please click 📝 Register for Debate below to begin."
        )
        bot.send_message(chat_id, msg, reply_markup=markup)
        return

    full_name, phone_number, attending = user[0], user[1], user[2]

    # Registered & Attending current session
    if attending == 1:
        btn_cancel = types.KeyboardButton("❌ I cannot come (Cancel this session)")
        markup.add(btn_cancel)
        msg = custom_intro or (
            f"👋 Welcome back, {full_name}!\n\n"
            f"✅ Status: You are registered for the upcoming session.\n"
            f"If your plans change, tap the button below to cancel."
        )
        bot.send_message(chat_id, msg, reply_markup=markup)

    # Registered, but awaiting new session RSVP
    else:
        btn_yes = types.KeyboardButton("✅ Yes, I will come")
        btn_no = types.KeyboardButton("❌ No, I cannot come")
        markup.row(btn_yes, btn_no)
        msg = custom_intro or (
            f"👋 Welcome back, {full_name}!\n\n"
            f"Your profile is saved. Will you attend the upcoming debate session?"
        )
        bot.send_message(chat_id, msg, reply_markup=markup)

def build_admin_panel():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, full_name, phone_number, team FROM participants WHERE attending = 1 AND phone_number IS NOT NULL")
    participants = cursor.fetchall()
    conn.close()

    if not participants:
        return "⚠️ No participants confirmed for this session yet.", None

    text = f"📊 Admin Control Panel (Attending Session)\nTotal Confirmed: {len(participants)}\n\n"
    markup = types.InlineKeyboardMarkup()

    for p in participants:
        p_chat_id, full_name, phone, team = p
        status_icon = "🔴" if team == "Team 1" else ("🔵" if team == "Team 2" else "⚪")
        text += f"{status_icon} {full_name} ({phone}) — {team}\n"

        btn_t1 = types.InlineKeyboardButton(
            f"{'✅ ' if team == 'Team 1' else ''}T1: {full_name[:12]}",
            callback_data=f"set_team:{p_chat_id}:Team 1"
        )
        btn_t2 = types.InlineKeyboardButton(
            f"{'✅ ' if team == 'Team 2' else ''}T2: {full_name[:12]}",
            callback_data=f"set_team:{p_chat_id}:Team 2"
        )
        markup.row(btn_t1, btn_t2)

    text += "\n👇 Tap buttons below to assign teams, then click Confirm to notify everyone:"
    
    btn_confirm = types.InlineKeyboardButton("✅ Confirm & Notify All", callback_data="confirm_notify")
    btn_reset = types.InlineKeyboardButton("🔄 Reset Teams", callback_data="reset_teams")
    markup.row(btn_confirm)
    markup.row(btn_reset)

    return text, markup

# ==========================================
# COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    registration_in_progress.discard(message.chat.id)
    send_user_menu(message.chat.id)

@bot.message_handler(commands=['admin_summary'])
def admin_summary(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        bot.send_message(chat_id, "❌ Access denied.")
        return

    text, markup = build_admin_panel()
    bot.send_message(chat_id, text, reply_markup=markup)

@bot.message_handler(commands=['clean', 'new_session'])
def clean_session_prompt(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        bot.send_message(chat_id, "❌ Access denied.")
        return

    markup = types.InlineKeyboardMarkup()
    btn_yes = types.InlineKeyboardButton("🔄 Yes, Clean Session & Ask Everyone", callback_data="confirm_clean")
    btn_cancel = types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_admin_action")
    markup.row(btn_yes)
    markup.row(btn_cancel)

    bot.send_message(
        chat_id,
        "🧹 Clean Session for Next Debate?\n\n"
        "This will reset everyone's attendance to 0 and clear old teams while saving all user profiles, and automatically send Yes / No buttons to all registered members.",
        reply_markup=markup
    )

@bot.message_handler(commands=['remove_all'])
def remove_all_prompt(message):
    chat_id = message.chat.id
    if not is_admin(chat_id):
        bot.send_message(chat_id, "❌ Access denied.")
        return

    markup = types.InlineKeyboardMarkup()
    btn_yes = types.InlineKeyboardButton("🗑️ Yes, Wipe Database Completely", callback_data="confirm_remove_all")
    btn_cancel = types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_admin_action")
    markup.row(btn_yes)
    markup.row(btn_cancel)

    bot.send_message(
        chat_id,
        "⚠️ Permanently delete all user profiles?\n\n"
        "Everyone will have to register with their name and phone number again.",
        reply_markup=markup
    )

# ==========================================
# CONTACT HANDLER
# ==========================================

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    chat_id = message.chat.id
    phone_number = message.contact.phone_number
    registration_in_progress.discard(chat_id)

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT full_name FROM participants WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()

    if not row or not row[0]:
        conn.close()
        bot.send_message(chat_id, "Please start registration by sending /start.")
        return

    full_name = row[0]
    cursor.execute(
        "UPDATE participants SET phone_number = ?, attending = 1, team = 'Unassigned' WHERE chat_id = ?",
        (phone_number, chat_id)
    )
    conn.commit()
    conn.close()

    remove_markup = types.ReplyKeyboardRemove()
    bot.send_message(chat_id, "🎉 Registration complete!", reply_markup=remove_markup)

    intro = (
        f"🎉 You are successfully registered!\n\n"
        f"👤 Name: {full_name}\n"
        f"📞 Phone: {phone_number}\n\n"
        f"📢 Get information about debate times and updates in our channel:\n"
        f"👉 https://t.me/debate_IIS\n\n"
        f"You will be notified here once teams are assigned. If your plans change, tap the button below to cancel:"
    )
    send_user_menu(chat_id, custom_intro=intro)

# ==========================================
# INLINE CALLBACK HANDLERS
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_admin_callbacks(call):
    chat_id = call.message.chat.id

    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "❌ Unauthorized action.", show_alert=True)
        return

    data = call.data

    if data == "confirm_clean":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET attending = 0, team = 'Unassigned' WHERE phone_number IS NOT NULL")
        conn.commit()
        cursor.execute("SELECT chat_id, full_name FROM participants WHERE phone_number IS NOT NULL")
        saved_users = cursor.fetchall()
        conn.close()

        bot.answer_callback_query(call.id, "Session cleaned successfully!")
        sent_count = 0
        for u_chat_id, u_name in saved_users:
            try:
                intro = (
                    f"📢 New Debate Session Open, {u_name}!\n\n"
                    f"Will you attend the upcoming debate session? Tap a button below:"
                )
                send_user_menu(u_chat_id, custom_intro=intro)
                sent_count += 1
            except Exception as e:
                print(f"Could not message {u_chat_id}: {e}")

        bot.edit_message_text(
            f"🧹 Session Cleaned!\n\n"
            f"• Attendance reset to 0\n"
            f"• Sent RSVP prompt to {sent_count} members.",
            chat_id,
            call.message.message_id
        )
        return

    elif data == "confirm_remove_all":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM participants")
        conn.commit()
        conn.close()

        registration_in_progress.clear()
        bot.answer_callback_query(call.id, "All participants deleted!")
        bot.edit_message_text(
            "🗑️ Database cleared. Total registered is now 0.",
            chat_id,
            call.message.message_id
        )
        return

    elif data == "cancel_admin_action":
        bot.answer_callback_query(call.id, "Cancelled")
        bot.edit_message_text("✅ Action cancelled.", chat_id, call.message.message_id)
        return

    elif data.startswith("set_team:"):
        _, target_chat_id, chosen_team = data.split(":")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET team = ? WHERE chat_id = ?", (chosen_team, int(target_chat_id)))
        conn.commit()
        conn.close()

        bot.answer_callback_query(call.id, f"Assigned to {chosen_team}")
        text, markup = build_admin_panel()
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass

    elif data == "reset_teams":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET team = 'Unassigned' WHERE attending = 1")
        conn.commit()
        conn.close()

        bot.answer_callback_query(call.id, "Teams reset")
        text, markup = build_admin_panel()
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass

    elif data == "confirm_notify":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT chat_id, full_name, team FROM participants WHERE attending = 1 AND phone_number IS NOT NULL")
        all_participants = cursor.fetchall()
        conn.close()

        team1_members = [{"chat_id": p[0], "name": p[1]} for p in all_participants if p[2] == "Team 1"]
        team2_members = [{"chat_id": p[0], "name": p[1]} for p in all_participants if p[2] == "Team 2"]

        if not team1_members and not team2_members:
            bot.answer_callback_query(call.id, "⚠️ Please assign at least one person to Team 1 or Team 2 first!", show_alert=True)
            return

        bot.answer_callback_query(call.id, "Sending notifications...")

        team1_names = [m["name"] for m in team1_members]
        for member in team1_members:
            teammates = [n for n in team1_names if n != member["name"]]
            teammates_str = ", ".join(teammates) if teammates else "None (solo on team)"
            try:
                bot.send_message(
                    member["chat_id"],
                    f"⚔️ Official Debate Teams Confirmed!\n\n"
                    f"🔴 You are on: Team 1\n"
                    f"👥 Your Teammates: {teammates_str}"
                )
            except Exception as e:
                print(f"Failed to notify {member['chat_id']}: {e}")

        team2_names = [m["name"] for m in team2_members]
        for member in team2_members:
            teammates = [n for n in team2_names if n != member["name"]]
            teammates_str = ", ".join(teammates) if teammates else "None (solo on team)"
            try:
                bot.send_message(
                    member["chat_id"],
                    f"⚔️ Official Debate Teams Confirmed!\n\n"
                    f"🔵 You are on: Team 2\n"
                    f"👥 Your Teammates: {teammates_str}"
                )
            except Exception as e:
                print(f"Failed to notify {member['chat_id']}: {e}")

        t1_summary = ", ".join(team1_names) if team1_names else "None"
        t2_summary = ", ".join(team2_names) if team2_names else "None"
        
        final_admin_msg = (
            f"✅ Teams Confirmed & Notifications Sent!\n\n"
            f"🔴 Team 1 ({len(team1_names)}): {t1_summary}\n"
            f"🔵 Team 2 ({len(team2_names)}): {t2_summary}"
        )
        bot.edit_message_text(final_admin_msg, chat_id, call.message.message_id)

# ==========================================
# GENERAL TEXT MESSAGE HANDLER
# ==========================================

@bot.message_handler(func=lambda message: True)
def handle_messages(message):
    chat_id = message.chat.id
    text = message.text

    # 1. Clicked Register Button
    if text == "📝 Register for Debate":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT full_name, phone_number FROM participants WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        
        if row and row[1]:
            conn.close()
            send_user_menu(chat_id)
            return

        cursor.execute(
            "INSERT OR REPLACE INTO participants (chat_id, full_name, phone_number, team, attending) VALUES (?, '', NULL, 'Unassigned', 0)",
            (chat_id,)
        )
        conn.commit()
        conn.close()

        registration_in_progress.add(chat_id)
        bot.send_message(chat_id, "Please enter your Full Name or Nickname (any name is accepted):")
        return

    # 2. Check if user is currently in the middle of typing their name
    if chat_id in registration_in_progress:
        full_name = text.strip()
        if len(full_name) < 1:
            bot.send_message(chat_id, "Please enter a valid name.")
            return

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET full_name = ? WHERE chat_id = ?", (full_name, chat_id))
        conn.commit()
        conn.close()

        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        btn_phone = types.KeyboardButton("📱 Share Phone Number", request_contact=True)
        markup.add(btn_phone)

        bot.send_message(
            chat_id,
            f"Thank you, {full_name}!\nNow please click the button below to share your phone number.",
            reply_markup=markup
        )
        return

    # 3. Returning User RSVP: "Yes, I will come"
    if text == "✅ Yes, I will come":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT full_name FROM participants WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            send_user_menu(chat_id)
            return

        cursor.execute("UPDATE participants SET attending = 1, team = 'Unassigned' WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

        intro = (
            f"🎉 Awesome, {row[0]}! You are confirmed for this session.\n\n"
            f"📢 Check debate times and updates in our channel:\n"
            f"👉 https://t.me/debate_IIS\n\n"
            f"If your plans change, tap the button below to cancel:"
        )
        send_user_menu(chat_id, custom_intro=intro)
        return

    # 4. Returning User RSVP: "No, I cannot come"
    elif text == "❌ No, I cannot come":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET attending = 0, team = 'Unassigned' WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

        intro = (
            "👍 Got it! We marked that you won't be attending this session.\n\n"
            "If you change your mind later, tap ✅ Yes, I will come below:"
        )
        send_user_menu(chat_id, custom_intro=intro)
        return

    # 5. Cancel Current Session Registration
    elif text == "❌ I cannot come (Cancel this session)":
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("UPDATE participants SET attending = 0, team = 'Unassigned' WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

        intro = (
            "❌ Your registration for this session has been cancelled.\n\n"
            "We hope to see you next time! If your plans change back, tap ✅ Yes, I will come below:"
        )
        send_user_menu(chat_id, custom_intro=intro)
        return

    send_user_menu(chat_id)

# ==========================================
# BACKGROUND WEEKLY SCHEDULER (Friday 10 PM)
# ==========================================

def send_weekly_summary():
    while True:
        tashkent_tz = timezone(timedelta(hours=5))
        now = datetime.now(tashkent_tz)
        
        if now.weekday() == 4 and now.hour == 22 and now.minute == 0:
            if ADMIN_IDS:
                text, markup = build_admin_panel()
                for admin_id in ADMIN_IDS:
                    try:
                        bot.send_message(
                            admin_id,
                            f"⏰ Friday 10:00 PM Automated Report\n\n{text}",
                            reply_markup=markup
                        )
                    except Exception as e:
                        print(f"Error sending weekly summary to {admin_id}: {e}")
            
            time.sleep(60)
        
        time.sleep(30)

scheduler_thread = threading.Thread(target=send_weekly_summary, daemon=True)
scheduler_thread.start()

if __name__ == "__main__":
    print("🤖 Debate Club Bot is running...")
    bot.infinity_polling()