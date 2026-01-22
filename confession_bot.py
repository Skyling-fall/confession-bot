from keep_alive import keep_alive
keep_alive()

print("Loading bot file...")

import os
import re
import time
import asyncio
import logging
import datetime
import json  # for /check, tickets, banned words

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Bot,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# reduce noisy logs
logging.getLogger("telegram.ext").setLevel(logging.ERROR)

# ====== CONFIG (env vars from Replit Secrets) ======
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var not set!")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003210666863"))   # your confession channel id
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "6809653923")) # your Telegram user id (for logs)

# your channel @username WITHOUT @
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "hehehe_010101")

# private log channel for submissions (only you + bot)
# falls back to ADMIN_CHAT_ID if not set, so nothing breaks
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", str(ADMIN_CHAT_ID)))

WATERMARK = "\nCF YTJT"   # text added at bottom of each public confession
REPORT_THRESHOLD = 3       # delete after this many reports
REPORT_COUNTS = {}         # message_id -> count (in memory only)

# uptime / status tracking
START_TIME = datetime.datetime.now()
LAST_HEARTBEAT = datetime.datetime.now()
# ================================


# mapping admin chat-log message id -> user info (for /who)
CHAT_ADMIN_ORIGINS: dict[int, dict] = {}

# 🔐 ORIGINS STORAGE (for /check)
ORIGINS_FILE = "origins.json"
# message_id (int) -> {
#   "user_id", "username", "full_name", "mode", "timestamp"
# }
MESSAGE_ORIGINS: dict[int, dict] = {}

# 🔐 TICKET STORAGE (for /reply and /ticketinfo)
TICKETS_FILE = "tickets.json"
# ticket_id (int) -> {
#   "user_id", "username", "full_name",
#   "created_at", "status", "messages": [ { "from", "text", "timestamp" } ]
# }
TICKETS: dict[int, dict] = {}
NEXT_TICKET_ID = 1

# 🔐 BANNED WORDS STORAGE
BANNED_WORDS_FILE = "banned_words.json"


def load_message_origins():
    """Load MESSAGE_ORIGINS from a JSON file (if exists)."""
    global MESSAGE_ORIGINS
    if not os.path.exists(ORIGINS_FILE):
        MESSAGE_ORIGINS = {}
        return
    try:
        with open(ORIGINS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            MESSAGE_ORIGINS = {int(k): v for k, v in data.items()}
        else:
            print("origins.json format invalid (not dict), ignoring.")
            MESSAGE_ORIGINS = {}
    except Exception as e:
        print(f"Failed to load message origins: {e}")
        MESSAGE_ORIGINS = {}


def save_message_origins():
    """Save MESSAGE_ORIGINS to JSON file."""
    try:
        serializable = {str(k): v for k, v in MESSAGE_ORIGINS.items()}
        with open(ORIGINS_FILE, "w") as f:
            json.dump(serializable, f)
    except Exception as e:
        print(f"Failed to save message origins: {e}")


def load_tickets():
    """Load TICKETS and NEXT_TICKET_ID from JSON file."""
    global TICKETS, NEXT_TICKET_ID
    if not os.path.exists(TICKETS_FILE):
        TICKETS = {}
        NEXT_TICKET_ID = 1
        return
    try:
        with open(TICKETS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            TICKETS = {int(k): v for k, v in data.items()}
            if TICKETS:
                NEXT_TICKET_ID = max(TICKETS.keys()) + 1
            else:
                NEXT_TICKET_ID = 1
        else:
            print("tickets.json format invalid (not dict), ignoring.")
            TICKETS = {}
            NEXT_TICKET_ID = 1
    except Exception as e:
        print(f"Failed to load tickets: {e}")
        TICKETS = {}
        NEXT_TICKET_ID = 1


def save_tickets():
    """Save TICKETS to JSON file."""
    try:
        serializable = {str(k): v for k, v in TICKETS.items()}
        with open(TICKETS_FILE, "w") as f:
            json.dump(serializable, f)
    except Exception as e:
        print(f"Failed to save tickets: {e}")


# ---------- CENSORSHIP (dynamic) ----------
# Default bad-word list (used if no JSON file yet)
DEFAULT_BAD_WORDS = [
    "fuck", "fak", "babi", "sial", "bangsat", "pukimak", "anjing",
    "bodoh", "idiot", "kafir", "cibai", "cipap", "pussy",
    "dick", "asshole", "bitch", "shit", "njir", "kontol", "memek",
    "bengong", "bodo", "nigga", "keling", "konek", "burit", "burik",
    "kocak", "pele", "pelir", "boti", "dozak", "desah", "jubo",
    "dozhak", "dozyak", "dhozak",
    "g4y", "b0t1", "bot1", "b0ti", "gay", "gay****", "tetek", "jpp", "JPP", "🐳", "⭐", "pantat",
]

# live, editable list
BAD_WORDS = DEFAULT_BAD_WORDS.copy()


def load_banned_words():
    """Load BAD_WORDS from JSON if exists, else keep default list."""
    global BAD_WORDS
    if not os.path.exists(BANNED_WORDS_FILE):
        print("No banned_words.json found, using default bad words list.")
        return
    try:
        with open(BANNED_WORDS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            BAD_WORDS = data
            print(f"Loaded {len(BAD_WORDS)} banned words from file.")
        else:
            print("banned_words.json format invalid (not list), using default list.")
    except Exception as e:
        print(f"Failed to load banned words: {e}")


def save_banned_words():
    """Save BAD_WORDS to JSON file."""
    try:
        with open(BANNED_WORDS_FILE, "w") as f:
            json.dump(BAD_WORDS, f)
        print(f"Saved {len(BAD_WORDS)} banned words.")
    except Exception as e:
        print(f"Failed to save banned words: {e}")


def censor_text(text: str) -> str:
    """Censor bad words but keep the first letter visible (case-insensitive)."""
    if not text:
        return text
    clean = text
    for word in BAD_WORDS:
        pattern = re.compile(re.escape(word), re.IGNORECASE)

        def repl(m):
            original = m.group(0)
            if len(original) <= 1:
                return "*"
            first = original[0]                 # keep first letter
            hidden = "*" * (len(original) - 1)  # censor the rest
            return first + hidden

        clean = pattern.sub(repl, clean)
    return clean
# -------------------------------


# load on startup
load_message_origins()
load_tickets()
load_banned_words()
print("Loaded message origins for", len(MESSAGE_ORIGINS), "messages")
print("Loaded tickets:", len(TICKETS))
print("Banned words count:", len(BAD_WORDS))

RETURN_MENU_TEXT = "RETURN MENU"

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["RULES/INFO"],
        ["CONFESS"],
        ["LOST N FOUND"],
        ["REPORT"],
        ["CHAT ADMIN"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)

MODE_KEYBOARD = ReplyKeyboardMarkup(
    [[RETURN_MENU_TEXT]],
    resize_keyboard=True,
    one_time_keyboard=False,
)


def extract_message_id_from_text(text: str) -> int | None:
    """
    Find the last /<number> pattern in the text.
    Works for links like:
      https://t.me/channel/25
      https://t.me/c/123456/42
    or anything ending with /<digits>
    """
    if not text:
        return None
    match = re.search(r"/(\d+)(?:\D*$)", text.strip())
    if match:
        return int(match.group(1))
    return None


async def update_heartbeat():
    """Update last heartbeat timestamp for /status."""
    global LAST_HEARTBEAT
    LAST_HEARTBEAT = datetime.datetime.now()


async def announce_online(bot: Bot):
    """Announce in the channel that the bot just came online (currently unused)."""
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text="ding dong"
        )
    except Exception as e:
        print(f"Failed to send online announcement: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()

    # 🚫 Block banned users on /start
    if update.message and update.message.from_user:
        if update.message.from_user.id in BANNED_USERS:
            await update.message.reply_text("❌ You are banned from using this bot.")
            return

    context.user_data.clear()
    text = (
        "Welcome to the confession bot 🐳\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(text, reply_markup=MAIN_MENU_KEYBOARD)


async def menu_confess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()
    context.user_data["mode"] = "confess"
    await update.message.reply_text(
        "Send your confession.\n\n"
        "⚠ You can send text OR one photo/video/file.",
        reply_markup=MODE_KEYBOARD,
    )


async def menu_lost_found(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()
    context.user_data["mode"] = "lostfound"
    await update.message.reply_text(
        "Send your LOST & FOUND message.\n"
        "You may attach one media.\n"
        "If can guna this format ah🐳\n\n"
        "Item:\n"
        "Area/Place:\n"
        "Time:",
        reply_markup=MODE_KEYBOARD,
    )


async def menu_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()
    context.user_data["mode"] = "report"
    await update.message.reply_text(
        "Paste the post link from the confession channel that you want to report.\n\n"
        "Example:\n"
        "https://t.me/" + CHANNEL_USERNAME + "/25",
        reply_markup=MODE_KEYBOARD,
    )


async def menu_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()
    rules = (
        "📌 RULES / INFO\n\n"
        "1. Enjoy jela asal jangan personal.\n"
        "2. No hate speech.\n"
        "3. Admins may remove posts.\n"
        "4. Your identity stays anonymous in the channel.\n\n"
        "Note: Selebew "
    )
    await update.message.reply_text(rules, reply_markup=MAIN_MENU_KEYBOARD)


# 🔹 CHAT ADMIN MENU
async def menu_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()
    context.user_data["mode"] = "chatadmin"
    await update.message.reply_text(
        "You can send a private message to admin now.\n"
        "Type anything you want to tell admin.\n\n"
        "Use RETURN MENU if you change your mind.",
        reply_markup=MODE_KEYBOARD,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_heartbeat()

    message = update.message
    if not message:
        return

    # 🚫 Block banned users for ALL normal messages
    if message.from_user and message.from_user.id in BANNED_USERS:
        await message.reply_text("❌ You are banned from using this bot.")
        return

    text = message.text or ""

    # RETURN MENU global handler
    if text == RETURN_MENU_TEXT:
        context.user_data.clear()
        await message.reply_text(
            "Back to menu ✅",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # Ignore main-menu button labels here
    if text in ["CONFESS", "LOST N FOUND", "REPORT", "RULES/INFO", "CHAT ADMIN"]:
        return

    mode = context.user_data.get("mode")

    if mode is None:
        await message.reply_text(
            "Please choose an option from the menu 🐳",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # Basic user info
    user = message.from_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "(no username)"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "(no name)"

    # ============ CHAT ADMIN MODE ============
    if mode == "chatadmin":
        global NEXT_TICKET_ID

        user_text = message.text or message.caption or ""

        # create new ticket
        ticket_id = NEXT_TICKET_ID
        NEXT_TICKET_ID += 1

        ticket_data = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "created_at": datetime.datetime.now().isoformat(),
            "status": "open",
            "messages": [
                {
                    "from": "user",
                    "text": user_text,
                    "timestamp": datetime.datetime.now().isoformat(),
                }
            ],
        }
        TICKETS[ticket_id] = ticket_data
        save_tickets()

        info_header = (
            "📩 NEW MESSAGE TO ADMIN (ANON)\n"
            f"Ticket ID: {ticket_id}\n"
            "User: (hidden)\n\n"
        )

        # TEXT ONLY
        if message.text and not (
            message.photo
            or message.video
            or message.document
            or message.audio
            or message.voice
            or message.animation
            or message.video_note
        ):
            admin_msg = await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=info_header + f"Message:\n{user_text}",
            )
        else:
            # MEDIA CASE – forward with caption containing info
            caption = message.caption or ""
            admin_caption = (info_header + f"Caption:\n{caption}").strip()
            admin_msg = await context.bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
                caption=admin_caption,
            )

        # store who sent this chat-admin message (in-memory only)
        CHAT_ADMIN_ORIGINS[admin_msg.message_id] = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "ticket_id": ticket_id,
        }

        await message.reply_text(
            f"✅ Your message has been sent to admin.\n"
            f"Ticket ID: {ticket_id}\n"
            "He will read it soon.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        context.user_data["mode"] = None
        return

    # ============ REPORT MODE ============
    if mode == "report":
        report_text = message.text or message.caption or ""
        msg_id = extract_message_id_from_text(report_text)

        # Build log text for admin
        log_text = (
            "🚨 NEW REPORT ID\n"
            f"Reporter ID: {user_id}\n"
            f"Reporter Username: {username}\n"
            f"Reporter Name: {full_name}\n\n"
            f"Report text:\n{report_text}\n"
        )

        if msg_id is None:
            await message.reply_text(
                "❌ I couldn't find a post ID in that message.\n"
                "Please copy the post link from the channel and paste it here.\n\n"
                "Example:\nhttps://t.me/" + CHANNEL_USERNAME + "/25",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
            # still log to admin
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=log_text + "\n(No message_id found)",
            )
            context.user_data["mode"] = None
            return

        # Update report count
        current_count = REPORT_COUNTS.get(msg_id, 0) + 1
        REPORT_COUNTS[msg_id] = current_count

        log_text += f"\nReported message_id: {msg_id}\nTotal reports: {current_count}/{REPORT_THRESHOLD}\n"

        # Post KMJ-style report post in the channel (EVERY time)
        try:
            report_link = f"https://t.me/{CHANNEL_USERNAME}/{msg_id}"
            report_notice = (
                f"{report_link}\n"
                f"Content is reported!\n"
                f"Please beware of what you send."
            )
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=report_notice,
            )
        except Exception as e:
            log_text += f"\nWarning post failed: {e}\n"

        # Try delete if over threshold
        deleted = False
        if current_count >= REPORT_THRESHOLD:
            try:
                await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
                deleted = True
                log_text += "✔ Message deleted from channel.\n"
            except Exception as e:
                log_text += f"❌ Failed to delete message: {e}\n"

        # Notify reporting user (no counts shown)
        if deleted:
            await message.reply_text(
                "✅ Report recorded.\n"
                "That post has been removed from the channel.",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
        else:
            await message.reply_text(
                "✅ Report recorded.\n"
                "Thank you for helping keep the channel clean.",
                reply_markup=MAIN_MENU_KEYBOARD,
            )

        # Send full log to admin (with counts)
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text)

        context.user_data["mode"] = None
        return

    # ============ CONFESS / LOST N FOUND ============
    if mode == "lostfound":
        header = "📦 LOST & FOUND:"
    else:
        header = ""

    # Admin log header (WITH user details, goes only to private log channel)
    log_header = (
        "👀 NEW SUBMISSION LOG\n"
        f"Mode: {mode}\n"
        f"User ID: {user_id}\n"
        f"Username: {username}\n"
        f"Name: {full_name}\n\n"
    )

    # TEXT ONLY
    if message.text and not (
        message.photo
        or message.video
        or message.document
        or message.audio
        or message.voice
        or message.animation
        or message.video_note
    ):
        # censor only for channel, not for admin
        clean_text = censor_text(message.text)
        public_post = f"{header}\n\n{clean_text}{WATERMARK}"

        # Send to channel (anonymous)
        sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=public_post)

        # Save who sent this post for /check (persistent)
        MESSAGE_ORIGINS[sent.message_id] = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "mode": mode,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        save_message_origins()

        # Log original text (uncensored) with user info to private log channel
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_header + f"Text:\n{message.text}",
        )

    else:
        # MEDIA CASE
        caption = message.caption or ""
        clean_caption = censor_text(caption)
        public_caption = f"{header}\n\n{clean_caption}{WATERMARK}".strip()

        # Anonymous copy to channel (with censored caption)
        sent = await context.bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            caption=public_caption,
        )

        # Save who sent this post for /check (persistent)
        MESSAGE_ORIGINS[sent.message_id] = {
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "mode": mode,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        save_message_origins()

        # Admin copy + log (original caption, WITH user details) to private log channel
        admin_caption = (log_header + f"Caption:\n{caption}").strip()
        await context.bot.copy_message(
            chat_id=LOG_CHANNEL_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            caption=admin_caption,
        )

    # Reply to user
    await message.reply_text("✅ DAH POST TU", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data["mode"] = None


# ============ STATUS / SHUTDOWN / OPEN (ADMIN ONLY) ============

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only status command showing uptime and last heartbeat."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    now = datetime.datetime.now()
    uptime = now - START_TIME
    since_hb = now - LAST_HEARTBEAT

    text = (
        "📡 *Bot Status (Admin Only)*\n\n"
        f"🟢 *Online*: Yes\n"
        f"⏱ *Uptime*: {str(uptime).split('.')[0]}\n"
        f"💓 *Last Heartbeat*: {str(since_hb).split('.')[0]} ago\n"
        f"🔄 *Last Restart*: {START_TIME.strftime('%Y-%m-%d %H:%M:%S')}\n"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def shutdown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only manual shutdown command."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    # Notify channel
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text="Bot is going offline now. See you guys later. If ado salah silap maaf deh. Gn 🐳"
        )
    except Exception as e:
        print(f"Failed to send shutdown notice to channel: {e}")

    # Confirm privately to admin
    await update.message.reply_text("✔️ Bot shutting down...")

    # Small delay then exit process (use current loop)
    loop = asyncio.get_running_loop()
    loop.call_later(1, lambda: os._exit(0))


async def open_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only manual open/start announcement."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    # Post opening message to channel
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text="🐳 Bot is now OPEN!\nYou may start sending confessions."
        )
    except Exception as e:
        await update.message.reply_text(f"Failed to send open notice: {e}")
        return

    # Confirm to admin
    await update.message.reply_text("✔️ Opening message posted.")


# ============ BAN / UNBAN COMMANDS (ADMIN ONLY) ============

async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to ban a user by ID."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("User ID must be a number.")
        return

    BANNED_USERS.add(user_id)
    await update.message.reply_text(f"✔ User {user_id} is now banned from using the bot.")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to unban a user by ID."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return

    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("User ID must be a number.")
        return

    if user_id in BANNED_USERS:
        BANNED_USERS.remove(user_id)
        await update.message.reply_text(f"✔ User {user_id} has been unbanned.")
    else:
        await update.message.reply_text(f"User {user_id} is not in the banned list.")


# ============ BANNED WORDS COMMANDS (ADMIN ONLY) ============

async def addword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: add a new banned word."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /addword <word>")
        return

    word = " ".join(context.args).strip()
    if not word:
        await update.message.reply_text("Word cannot be empty.")
        return

    if word.lower() in (w.lower() for w in BAD_WORDS):
        await update.message.reply_text(f"'{word}' is already in the banned words list.")
        return

    BAD_WORDS.append(word)
    save_banned_words()
    await update.message.reply_text(f"✔ Added '{word}' to banned words.")


async def removeword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: remove a banned word."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /removeword <word>")
        return

    word = " ".join(context.args).strip()
    if not word:
        await update.message.reply_text("Word cannot be empty.")
        return

    # find case-insensitive match
    to_remove = None
    for w in BAD_WORDS:
        if w.lower() == word.lower():
            to_remove = w
            break

    if not to_remove:
        await update.message.reply_text(f"'{word}' is not in the banned words list.")
        return

    BAD_WORDS.remove(to_remove)
    save_banned_words()
    await update.message.reply_text(f"✔ Removed '{to_remove}' from banned words.")


async def words_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: list current banned words."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not BAD_WORDS:
        await update.message.reply_text("No banned words set.")
        return

    sorted_words = sorted(BAD_WORDS, key=lambda x: x.lower())
    text = "🚫 *Current Banned Words*:\n" + ", ".join(sorted_words)
    await update.message.reply_text(text, parse_mode="Markdown")


# ============ TICKET COMMANDS (ADMIN ONLY) ============

async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reply to a ticket -> send message to that user anonymously."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /reply <ticket_id> <message>")
        return

    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Ticket ID must be a number.")
        return

    if ticket_id not in TICKETS:
        await update.message.reply_text("❌ Ticket not found.")
        return

    reply_text = " ".join(context.args[1:]).strip()
    if not reply_text:
        await update.message.reply_text("Reply message cannot be empty.")
        return

    ticket = TICKETS[ticket_id]
    user_id = ticket["user_id"]

    # send message to user
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "📩 Reply from Admin (via bot)\n"
                f"(Ticket ID: {ticket_id})\n\n"
                f"{reply_text}"
            ),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send message to user: {e}")
        return

    # log message in ticket
    ticket["messages"].append(
        {
            "from": "admin",
            "text": reply_text,
            "timestamp": datetime.datetime.now().isoformat(),
        }
    )
    ticket["status"] = "answered"
    save_tickets()

    await update.message.reply_text(f"✔ Reply sent to ticket {ticket_id}.")


async def ticketinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: show info about a ticket."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /ticketinfo <ticket_id>")
        return

    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Ticket ID must be a number.")
        return

    ticket = TICKETS.get(ticket_id)
    if not ticket:
        await update.message.reply_text("❌ Ticket not found.")
        return

    msgs = ticket.get("messages", [])
    preview_lines = []
    for m in msgs[-5:]:  # last 5 messages
        frm = m.get("from", "?")
        txt = m.get("text", "")
        preview_lines.append(f"{frm}: {txt}")

    preview = "\n".join(preview_lines) if preview_lines else "(no messages logged)"

    text = (
        f"🎫 *Ticket Info*\n"
        f"ID: {ticket_id}\n"
        f"Status: {ticket.get('status')}\n"
        f"User ID: {ticket.get('user_id')}\n"
        f"Username: {ticket.get('username')}\n"
        f"Name: {ticket.get('full_name')}\n"
        f"Created: {ticket.get('created_at')}\n\n"
        f"Last messages:\n{preview}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ============ CHECK COMMAND (ADMIN ONLY) ============

async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to see who sent a specific channel post."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: /check <channel post link or message_id>\n\n"
            f"Example:\n/check https://t.me/{CHANNEL_USERNAME}/25"
        )
        return

    raw = " ".join(context.args).strip()

    # Try extract ID from link or raw number
    msg_id = extract_message_id_from_text(raw)
    if msg_id is None:
        try:
            msg_id = int(raw)
        except ValueError:
            await update.message.reply_text("❌ I couldn't find a valid message ID in that.")
            return

    info = MESSAGE_ORIGINS.get(msg_id)
    if not info:
        await update.message.reply_text(
            "⚠ No data found for that post.\n"
            "- Maybe it was sent before tracking was added\n"
            "- Or origins.json was deleted."
        )
        return

    await update.message.reply_text(
        "🕵️ CHECK RESULT\n"
        f"Message ID: {msg_id}\n"
        f"Mode: {info.get('mode')}\n"
        f"User ID: {info.get('user_id')}\n"
        f"Username: {info.get('username')}\n"
        f"Name: {info.get('full_name')}\n"
        f"Timestamp: {info.get('timestamp')}\n"
    )


# ============ WHO COMMAND FOR CHAT-ADMIN LOGS (ADMIN ONLY) ============

async def who_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: reply to a chat-admin log to see who sent it."""
    if update.message.chat_id != ADMIN_CHAT_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Reply to a CHAT ADMIN log message and send /who to see the sender."
        )
        return

    original_id = update.message.reply_to_message.message_id
    info = CHAT_ADMIN_ORIGINS.get(original_id)

    if not info:
        await update.message.reply_text(
            "⚠ No data for this message.\n"
            "Maybe the bot was restarted or it's not a chat-admin log."
        )
        return

    await update.message.reply_text(
        "🕵️ CHAT ADMIN SENDER\n"
        f"User ID: {info.get('user_id')}\n"
        f"Username: {info.get('username')}\n"
        f"Name: {info.get('full_name')}\n"
        f"Ticket ID: {info.get('ticket_id')}\n"
    )


def build_application():
    print("Creating application...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("shutdown", shutdown_cmd))
    app.add_handler(CommandHandler("open", open_cmd))

    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))

    # ticket & admin tools
    app.add_handler(CommandHandler("reply", reply_cmd))
    app.add_handler(CommandHandler("ticketinfo", ticketinfo_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("who", who_cmd))

    # banned words
    app.add_handler(CommandHandler("addword", addword_cmd))
    app.add_handler(CommandHandler("removeword", removeword_cmd))
    app.add_handler(CommandHandler("words", words_cmd))

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex("^CONFESS$"), menu_confess))
    app.add_handler(MessageHandler(filters.Regex("^LOST N FOUND$"), menu_lost_found))
    app.add_handler(MessageHandler(filters.Regex("^REPORT$"), menu_report))
    app.add_handler(MessageHandler(filters.Regex("^RULES/INFO$"), menu_rules))
    app.add_handler(MessageHandler(filters.Regex("^CHAT ADMIN$"), menu_chat_admin))

    # Any other text or media
    app.add_handler(MessageHandler(~filters.COMMAND, handle_message))

    return app


async def notify_admin_crash(msg: str):
    """Send a DM to admin when bot crashes."""
    try:
        bot = Bot(token=BOT_TOKEN)
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
    except Exception as e:
        print(f"Failed to notify admin: {e}")


if __name__ == "__main__":
    # create a dedicated event loop for the whole process (fixes 'no current event loop')
    asyncio.set_event_loop(asyncio.new_event_loop())
    loop = asyncio.get_event_loop()

    while True:
        try:
            # reset timers on each restart loop
            START_TIME = datetime.datetime.now()
            LAST_HEARTBEAT = datetime.datetime.now()

            print("Starting main()...")
            application = build_application()

            # just run the bot, no startup message
            application.run_polling()
        except Exception as e:
            error_msg = f"⚠ Bot crashed with error:\n{e}\n\nRestarting in 5 seconds..."
            print(error_msg)
            try:
                loop.run_until_complete(notify_admin_crash(error_msg))
            except Exception as e2:
                print(f"Also failed to notify admin: {e2}")
            time.sleep(5)
