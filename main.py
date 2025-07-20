import os
import re
import time
import threading
import ffmpeg
import shutil
import logging
from uuid import uuid4
from functools import wraps
from yt_dlp import YoutubeDL
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from youtubesearchpython import VideosSearch

BOT_TOKEN = "YOUR_BOT_TOKEN"
DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO)


# ==== UTILITIES ====

def cleanup(file_path, delay=30):
    def delete_file():
        time.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
    threading.Thread(target=delete_file).start()


def progress_hook(message, context):
    def hook(d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '').strip()
            try:
                context.bot.edit_message_text(
                    chat_id=message.chat_id,
                    message_id=message.message_id,
                    text=f"⏬ Downloading... {percent}"
                )
            except Exception:
                pass
    return hook


def search_youtube(query):
    results = VideosSearch(query, limit=5).result()
    return [(v['title'], v['link']) for v in results['result']]


# ==== COMMANDS ====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send a YouTube link or search term to get started.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "youtube.com" in text or "youtu.be" in text:
        context.user_data['links'] = re.findall(r'(https?://[^\s]+)', text)
        await ask_filename(update, context)
    else:
        results = search_youtube(text)
        buttons = [[InlineKeyboardButton(title, callback_data=url)] for title, url in results]
        await update.message.reply_text("🔍 Search Results:", reply_markup=InlineKeyboardMarkup(buttons))


async def ask_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📛 Enter custom filename (without extension):")
    return


async def filename_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['filename'] = update.message.text.strip()
    await update.message.reply_text("🎵 Choose audio format:", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("MP3", callback_data="format_mp3"),
         InlineKeyboardButton("M4A", callback_data="format_m4a"),
         InlineKeyboardButton("OGG", callback_data="format_ogg"),
         InlineKeyboardButton("WAV", callback_data="format_wav")]
    ]))


async def handle_format_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    format_selected = query.data.replace("format_", "")
    context.user_data['format'] = format_selected
    await query.edit_message_text("⏱️ Optional: enter start and end time (e.g., `0:10-2:30`) or type `skip`")

async def handle_trim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text != "skip":
        try:
            start, end = text.split("-")
            context.user_data['start'] = start.strip()
            context.user_data['end'] = end.strip()
        except:
            await update.message.reply_text("❌ Invalid format. Please enter like `0:10-1:30` or type `skip`.")
            return
    else:
        context.user_data['start'] = None
        context.user_data['end'] = None

    await download_and_send(update, context)


# ==== DOWNLOAD & SEND ====

async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = await update.message.reply_text("🛠 Preparing download...")

    for url in context.user_data['links']:
        ext = context.user_data['format']
        filename_base = context.user_data['filename'] or str(uuid4())
        output_path = os.path.join(DOWNLOAD_DIR, f"{filename_base}.{ext}")

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_path,
            'quiet': True,
            'progress_hooks': [progress_hook(message, context)],
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': ext,
                'preferredquality': '192',
            }],
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        # Optional Trim
        if context.user_data.get("start") and context.user_data.get("end"):
            trimmed_path = output_path.replace(f".{ext}", f"_trimmed.{ext}")
            (
                ffmpeg
                .input(output_path, ss=context.user_data['start'], to=context.user_data['end'])
                .output(trimmed_path)
                .run(overwrite_output=True, quiet=True)
            )
            os.remove(output_path)
            output_path = trimmed_path

        # Preview Clip
        preview_path = output_path.replace(f".{ext}", f"_preview.{ext}")
        (
            ffmpeg
            .input(output_path, t=10)
            .output(preview_path)
            .run(overwrite_output=True, quiet=True)
        )
        await update.message.reply_audio(InputFile(preview_path), caption="🎧 Preview Clip")
        os.remove(preview_path)

        # Send final audio
        await update.message.reply_audio(InputFile(output_path), caption=f"✅ Done: {filename_base}.{ext}")

        # Auto delete after sending
        cleanup(output_path)


# ==== CALLBACK FOR SEARCH RESULT ====

async def handle_search_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    url = query.data
    context.user_data['links'] = [url]
    await query.edit_message_text(f"✅ Selected:\n{url}")
    await ask_filename(update, context)


# ==== MAIN ====

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, filename_handler), group=1)
    app.add_handler(CallbackQueryHandler(handle_format_choice, pattern="^format_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_trim), group=2)
    app.add_handler(CallbackQueryHandler(handle_search_result, pattern="^https?://"))

    app.run_polling()


if __name__ == "__main__":
    main()
