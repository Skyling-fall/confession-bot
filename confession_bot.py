print("Loading bot file...")

import os
import re

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

# ====== CONFIG (EDIT THESE) ======
# If you run locally, easiest is to just put your token here:
# BOT_TOKEN = "YOUR_REAL_BOT_TOKEN_HERE"
# If you deploy on Render etc, you can use env vars:
BOT_TOKEN = os.getenv("BOT_TOKEN", "8553110584:AAFndGLahzXs-Hgbu3Kv6Nna3hdM9EgYAB4")

CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1003210666863"))   # your confession channel id
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "6809653923")) # your Telegram user id (for logs)

# VERY IMPORTANT: your channel @username WITHOUT @
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "hehehe_010101")
# Example: if your channel link is https://t.me/hehehe_010101
# then CHANNEL_USERNAME = "hehehe_010101"

WATERMARK = "\n\nCF YTJT"   # text added at bottom of each public confession
REPORT_THRESHOLD = 3        # delete after this many reports
REPORT_COUNTS = {}          # message_id -> count (in memory only)
# ================================

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["RULES/INFO"],
        ["CONFESS"],
        ["LOST N FOUND"],
        ["REPORT"],
    ],
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "Welcome to the confession bot 🐳\n\n"
        "Choose an option:"
    )
    await update.message.reply_text(text, reply_markup=MAIN_MENU_KEYBOARD)


async def menu_confess(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "confess"
    await update.message.reply_text(
        "Send your confession.\n\n"
        "⚠ You can send text OR one photo/video/file.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def menu_lost_found(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "lostfound"
    await update.message.reply_text(
        "Send your LOST & FOUND message.\n"
        "You may attach one media.\n"
	"If can guna this format ah🐳\n\n"
	"Item:\n"
	"Area/Place:\n"
	"Time:",
	reply_markup=ReplyKeyboardRemove(),        
    )


async def menu_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mode"] = "report"
    await update.message.reply_text(
        "Paste the post link from the confession channel that you want to report.\n\n"
        "Example:\n"
        "https://t.me/" + CHANNEL_USERNAME + "/25",
        reply_markup=ReplyKeyboardRemove(),
    )


async def menu_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules = (
        "📌 RULES / INFO\n\n"
        "1. Enjoy jela asal jangan personal.\n"
        "2. No hate speech.\n"
        "3. Admins may remove posts.\n"
	"4. Admins mah bebas bro.\n"
        "5. Your identity stays anonymous in the channel.\n\n"
        "Note: Selebew "
    )
    await update.message.reply_text(rules, reply_markup=MAIN_MENU_KEYBOARD)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not message:
        return

    # Ignore menu button labels here
    if message.text in ["CONFESS", "LOST N FOUND", "REPORT", "RULES/INFO"]:
        return

    mode = context.user_data.get("mode")

    if mode is None:
        await message.reply_text(
            "Please choose an option from the menu 🐳",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # Basic user info for admin logs
    user = message.from_user
    user_id = user.id
    username = f"@{user.username}" if user.username else "(no username)"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "(no name)"

    # ============ REPORT MODE ============
    if mode == "report":
        report_text = message.text or message.caption or ""
        msg_id = extract_message_id_from_text(report_text)

        # Build log text for admin
        log_text = (
            f"🚨 NEW REPORT\n"
            f"From ID: {user_id}\n"
            f"Username: {username}\n"
            f"Name: {full_name}\n\n"
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
                chat_id=ADMIN_CHAT_ID,
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
                f"Lol bro kena report\n"
                f"Jaga mulut tuh 🐳"
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
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=log_text)

        context.user_data["mode"] = None
        return

    # ============ CONFESS / LOST N FOUND ============
    if mode == "confess":
        header = "💬 CONFESSION:"
    elif mode == "lostfound":
        header = "📦 LOST & FOUND:"
    else:
        header = "💬 Message:"

    # Admin log header
    log_header = (
        f"👀 NEW SUBMISSION LOG\n"
        f"Mode: {mode}\n"
        f"From ID: {user_id}\n"
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
        public_post = f"{header}\n\n{message.text}{WATERMARK}"

        # Send to channel
        sent = await context.bot.send_message(chat_id=CHANNEL_ID, text=public_post)

        # Admin log
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=log_header + f"Text:\n{message.text}",
        )

    else:
        # MEDIA CASE
        caption = message.caption or ""
        public_caption = f"{header}\n\n{caption}{WATERMARK}".strip()

        # Anonymous copy to channel
        sent = await context.bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            caption=public_caption,
        )

        # Admin copy + log
        admin_caption = (log_header + f"Caption:\n{caption}").strip()
        await context.bot.copy_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            caption=admin_caption,
        )

    # Reply to user
    await message.reply_text("✅ DAH POST TU", reply_markup=MAIN_MENU_KEYBOARD)
    context.user_data["mode"] = None


def main():
    print("Creating application...")
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    # Optional: let plain "start" (no slash) also trigger menu:
    # app.add_handler(MessageHandler(filters.Regex("(?i)^start$"), start))

    # Menu buttons
    app.add_handler(MessageHandler(filters.Regex("^CONFESS$"), menu_confess))
    app.add_handler(MessageHandler(filters.Regex("^LOST N FOUND$"), menu_lost_found))
    app.add_handler(MessageHandler(filters.Regex("^REPORT$"), menu_report))
    app.add_handler(MessageHandler(filters.Regex("^RULES/INFO$"), menu_rules))

    # Any other text or media
    app.add_handler(MessageHandler(~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    print("Starting main()...")
    main()