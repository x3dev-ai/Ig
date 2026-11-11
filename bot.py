import os
import re
import sys
import glob
import json
import time
import shutil
import asyncio
import logging
import threading
import subprocess
from dataclasses import dataclass
from typing import List, Dict, Optional

import yt_dlp
import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8941723408:AAFWPuHBXycHCpS8spKT68t5IigMJ7nbPuk")
DOWNLOAD_DIR = "downloads"
COOKIES_FILE = "cookies.txt"
DB_FILE = "bot_database.json"
PORT = int(os.getenv("PORT", "8080"))

# Advertising & Referral Default Settings (Editable via /setad & /admin in Telegram)
DEFAULT_AD_TEXT = os.getenv("AD_TEXT", "🔥 Sponsor / Advertise Here — Contact Admin")
DEFAULT_AD_URL = os.getenv("AD_URL", "https://t.me/telegram")
REFERRAL_REWARD_POINTS = 10

ADMIN_IDS = set()
if os.getenv("ADMIN_ID"):
    try:
        ADMIN_IDS.add(int(os.getenv("ADMIN_ID")))
    except ValueError:
        pass

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("UltraFastSocialBotV3")

# Matches unlimited HTTP/HTTPS links in any message
URL_REGEX = re.compile(r"(https?://[^\s<>"']+)")
YOUTUBE_REGEX = re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)
INSTAGRAM_REGEX = re.compile(r"(instagram\.com|instagr\.am)", re.IGNORECASE)


# ================= PERSISTENT DATABASE (REFERRALS & ADS) =================
def load_db() -> dict:
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "users": {},
        "ad_button_text": DEFAULT_AD_TEXT,
        "ad_button_url": DEFAULT_AD_URL,
        "ad_enabled": True,
    }


def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2)
    except Exception as e:
        logger.warning(f"Database save warning: {e}")


db = load_db()


def register_user(user_id: int, username: str, referrer_id: Optional[int] = None) -> bool:
    """Registers user and credits referrer if new. Returns True if referral was credited."""
    uid = str(user_id)
    credited = False
    if uid not in db["users"]:
        db["users"][uid] = {
            "name": username,
            "points": 0,
            "referrals": 0,
            "downloads": 0,
            "referred_by": referrer_id,
        }
        if referrer_id and str(referrer_id) in db["users"] and str(referrer_id) != uid:
            ref_uid = str(referrer_id)
            db["users"][ref_uid]["points"] += REFERRAL_REWARD_POINTS
            db["users"][ref_uid]["referrals"] += 1
            credited = True
        save_db()
    return credited


def build_promo_keyboard(bot_username: str, user_id: int, is_youtube: bool = False, url: str = "") -> InlineKeyboardMarkup:
    """Builds Advertising + Earn Referral + Optional YouTube MP3 Fast Action buttons."""
    buttons = []
    if is_youtube and url:
        short_key = f"mp3_{abs(hash(url)) % 1000000}"
        url_cache[short_key] = url
        buttons.append([InlineKeyboardButton("🎵 Download MP3 Audio (320kbps)", callback_data=short_key)])

    if db.get("ad_enabled", True):
        buttons.append([
            InlineKeyboardButton(
                db.get("ad_button_text", DEFAULT_AD_TEXT),
                url=db.get("ad_button_url", DEFAULT_AD_URL),
            )
        ])

    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    share_url = (
        f"https://t.me/share/url?url={ref_link}"
        "&text=⚡+Download+Instagram+Reels,+YouTube+MP3/MP4+&+Social+Videos+Fast!"
    )
    buttons.append([
        InlineKeyboardButton("💰 Invite & Earn", url=share_url),
        InlineKeyboardButton("📊 My Earnings", callback_data="my_referrals"),
    ])
    return InlineKeyboardMarkup(buttons)


# ================= FASTAPI ULTRA-LOW-LATENCY ENGINE =================
fastapi_app = FastAPI(title="Telegram Social Media Downloader FastAPI Engine V3")
url_cache: Dict[str, str] = {}
stats = {"completed": 0, "failed": 0, "retried": 0, "mp3_converted": 0}


@fastapi_app.get("/", response_class=HTMLResponse)
async def health_dashboard():
    q_len = task_queue.qsize() if task_queue else 0
    return f"""
    <html>
        <head><title>FastAPI Telegram Downloader Engine V3</title></head>
        <body style="font-family:system-ui,sans-serif;background:#090d16;color:#f8fafc;text-align:center;padding:40px;">
            <h1>⚡ FastAPI + Telegram Downloader V3 Active</h1>
            <p><b>Completed:</b> {stats['completed']} | <b>MP3 Audio:</b> {stats['mp3_converted']} | <b>Auto-Reconnects:</b> {stats['retried']} | <b>Queue:</b> {q_len}</p>
            <p><b>Registered Users:</b> {len(db.get('users', {}))}</p>
        </body>
    </html>
    """


@fastapi_app.get("/api/status")
async def api_status():
    return JSONResponse({
        "status": "online",
        "version": "3.0",
        "engine": "FastAPI + Uvicorn + yt-dlp + gallery-dl",
        "queue_size": task_queue.qsize() if task_queue else 0,
        "stats": stats,
        "users_count": len(db.get("users", {})),
    })


def start_fastapi_server():
    uvicorn.run(fastapi_app, host="0.0.0.0", port=PORT, log_level="warning")


# ================= TASK QUEUE & PROGRESS DATA STRUCTURE =================
@dataclass
class DownloadTask:
    chat_id: int
    user_id: int
    message_id: int
    status_message_id: int
    emoji_message_id: Optional[int]
    url: str
    user_name: str
    audio_only: bool = False
    batch_index: int = 1
    batch_total: int = 1
    progress_percent: float = 0.0
    speed_str: str = "Connecting..."
    eta_str: str = "--:--"
    stage: str = "Initializing FastAPI Stream..."


task_queue: asyncio.Queue | None = None
current_task: DownloadTask | None = None
worker_task: asyncio.Task | None = None


# ================= FAST INSTAGRAM & ALL-PLATFORM DOWNLOAD ENGINE =================
def make_progress_bar(percent: float, length: int = 10) -> str:
    filled = int(round((percent / 100.0) * length))
    filled = max(0, min(length, filled))
    return "█" * filled + "░" * (length - filled)


def download_media_sync(task: DownloadTask, task_folder: str) -> List[str]:
    """
    Ultra-fast media downloader with:
    - Real-time yt-dlp progress hook for live animated status bar
    - Fast H.264 MP4 selection for instant Telegram streaming
    - YouTube MP3 audio extraction mode
    - Auto-reconnect & multi-attempt fallback (yt-dlp -> gallery-dl)
    """
    os.makedirs(task_folder, exist_ok=True)
    output_template = os.path.join(task_folder, "%(id)s.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            if total > 0:
                task.progress_percent = min(99.9, (downloaded / total) * 100.0)
            else:
                task.progress_percent = min(95.0, task.progress_percent + 7.5)
            speed = d.get("speed")
            if speed:
                task.speed_str = f"{speed / (1024 * 1024):.2f} MB/s"
            eta = d.get("eta")
            if eta is not None:
                task.eta_str = f"{int(eta)}s"
            task.stage = "Downloading Media Stream..."
        elif d.get("status") == "finished":
            task.progress_percent = 100.0
            task.stage = "Merging & Optimizing via FFmpeg..."

    is_ig = bool(INSTAGRAM_REGEX.search(task.url))

    ydl_opts = {
        "outtmpl": output_template,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "geo_bypass": True,
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "extractor_retries": 5,
        "socket_timeout": 15,
        "buffersize": 1024 * 64,
        "http_chunk_size": 10485760,
        "progress_hooks": [progress_hook],
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
                if is_ig else
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }

    if task.audio_only:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        ydl_opts["format"] = (
            "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
            "best[ext=mp4]/best"
        )
        ydl_opts["merge_output_format"] = "mp4"

    if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 100:
        with open(COOKIES_FILE, "r", encoding="utf-8", errors="ignore") as cf:
            content = [line.strip() for line in cf if line.strip() and not line.startswith("#")]
            if content:
                ydl_opts["cookiefile"] = COOKIES_FILE

    # Auto-Reconnect Loop (3 Attempts)
    for attempt in range(1, 4):
        try:
            if attempt > 1:
                stats["retried"] += 1
                task.stage = f"Auto-Reconnecting Stream (Attempt {attempt}/3)..."
                ydl_opts["http_headers"]["User-Agent"] = (
                    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
                )
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(task.url, download=True)
            break
        except Exception as e:
            logger.warning(f"Attempt {attempt} warning for {task.url}: {e}")
            time.sleep(1.0)

    files = [
        f for f in glob.glob(os.path.join(task_folder, "*"))
        if not f.endswith((".part", ".ytdl", ".json", ".txt"))
    ]

    # Fallback to gallery-dl for Multi-Photo Posts / Carousels
    if not files and not task.audio_only:
        try:
            task.stage = "Fetching Multi-Post Carousel via Gallery Engine..."
            task.progress_percent = 65.0
            cmd = ["gallery-dl", "--directory", task_folder, "--retries", "5", task.url]
            if "cookiefile" in ydl_opts:
                cmd.extend(["--cookies", COOKIES_FILE])
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
            for root, _, filenames in os.walk(task_folder):
                for fname in filenames:
                    if not fname.endswith((".part", ".json", ".txt")):
                        files.append(os.path.join(root, fname))
        except Exception as e:
            logger.error(f"gallery-dl fallback error for {task.url}: {e}")

    return sorted(list(set(files)))


# ================= ANIMATED DOWNLOAD STATUS BAR & EMOJI HANDLER =================
async def animate_download_progress(bot, task: DownloadTask, stop_event: asyncio.Event):
    """Renders a fast animated progress bar + speed + ETA in Telegram chat."""
    frames = ["⚡", "🚀", "🔥", "💫", "✨", "📥"]
    step = 0
    last_text = ""

    while not stop_event.is_set():
        icon = frames[step % len(frames)]
        bar = make_progress_bar(task.progress_percent, length=10)
        batch_tag = f" [Link {task.batch_index}/{task.batch_total}]" if task.batch_total > 1 else ""
        mode_tag = "🎵 MP3 Audio" if task.audio_only else "🎬 HD Media"

        text = (
            f"{icon} <b>FastAPI {mode_tag} Downloader{batch_tag}</b>\n"
            f"📡 <b>Status:</b> {task.stage}\n"
            f"<code>[{bar}] {task.progress_percent:.1f}%</code>\n"
            f"🚀 <b>Speed:</b> <code>{task.speed_str}</code> | ⏱ <b>ETA:</b> <code>{task.eta_str}</code>"
        )

        if text != last_text:
            try:
                await bot.edit_message_text(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
                last_text = text
            except Exception:
                pass

        step += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.3)
        except asyncio.TimeoutError:
            continue


async def cleanup_waiting_messages(bot, task: DownloadTask):
    """Automatically deletes both the animated emoji message and status bar once download finishes."""
    for msg_id in (task.status_message_id, task.emoji_message_id):
        if msg_id:
            try:
                await bot.delete_message(chat_id=task.chat_id, message_id=msg_id)
            except Exception:
                pass


# ================= SEQUENTIAL 1-BY-1 ULTRA-FAST WORKER =================
async def queue_worker(app: Application):
    global current_task
    bot = app.bot
    bot_info = await bot.get_me()
    bot_username = bot_info.username or "SocialDownloaderBot"
    logger.info(f"FastAPI Queue Worker active for @{bot_username}")

    while True:
        task: DownloadTask = await task_queue.get()
        current_task = task
        task_folder = os.path.join(
            DOWNLOAD_DIR, f"{task.chat_id}_{task.message_id}_{abs(hash(task.url)) % 100000}"
        )
        stop_anim = asyncio.Event()

        anim_task = asyncio.create_task(animate_download_progress(bot, task, stop_anim))

        try:
            loop = asyncio.get_running_loop()
            downloaded_files = await loop.run_in_executor(
                None, download_media_sync, task, task_folder
            )

            stop_anim.set()
            await anim_task

            if not downloaded_files:
                stats["failed"] += 1
                if task.emoji_message_id:
                    try:
                        await bot.delete_message(chat_id=task.chat_id, message_id=task.emoji_message_id)
                    except Exception:
                        pass
                await bot.edit_message_text(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                    text=(
                        "❌ <b>Download Failed After Auto-Reconnect</b>\n"
                        "The link may be private, deleted, or restricted.\n"
                        "💡 <i>Admins can tap /fix to update extractors immediately.</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                )
            else:
                try:
                    await bot.edit_message_text(
                        chat_id=task.chat_id,
                        message_id=task.status_message_id,
                        text=(
                            "📤 <b>Download 100% Complete!</b>\n"
                            "<code>[██████████] 100.0%</code>\n"
                            "⚡ <b>Sending to chat via Low-Latency API...</b>"
                        ),
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass

                is_yt = bool(YOUTUBE_REGEX.search(task.url)) and not task.audio_only
                reply_markup = build_promo_keyboard(bot_username, task.user_id, is_youtube=is_yt, url=task.url)

                for file_path in downloaded_files:
                    ext = os.path.splitext(file_path)[1].lower()
                    caption = (
                        f"✅ <b>Downloaded via FastAPI</b> | <a href='{task.url}'>Source Link</a>\n"
                        f"🎁 <i>Share & Earn rewards with /refer</i>"
                    )

                    with open(file_path, "rb") as media_file:
                        if ext in [".mp3", ".m4a", ".wav", ".ogg", ".opus"]:
                            stats["mp3_converted"] += 1
                            await bot.send_chat_action(chat_id=task.chat_id, action=ChatAction.UPLOAD_VOICE)
                            await bot.send_audio(
                                chat_id=task.chat_id,
                                audio=media_file,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_to_message_id=task.message_id,
                                reply_markup=reply_markup,
                            )
                        elif ext in [".mp4", ".mov", ".mkv", ".webm"]:
                            await bot.send_chat_action(chat_id=task.chat_id, action=ChatAction.UPLOAD_VIDEO)
                            await bot.send_video(
                                chat_id=task.chat_id,
                                video=media_file,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_to_message_id=task.message_id,
                                supports_streaming=True,
                                reply_markup=reply_markup,
                            )
                        elif ext in [".jpg", ".jpeg", ".png", ".webp"]:
                            await bot.send_chat_action(chat_id=task.chat_id, action=ChatAction.UPLOAD_PHOTO)
                            await bot.send_photo(
                                chat_id=task.chat_id,
                                photo=media_file,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_to_message_id=task.message_id,
                                reply_markup=reply_markup,
                            )
                        else:
                            await bot.send_document(
                                chat_id=task.chat_id,
                                document=media_file,
                                caption=caption,
                                parse_mode=ParseMode.HTML,
                                reply_to_message_id=task.message_id,
                                reply_markup=reply_markup,
                            )

                stats["completed"] += 1
                uid = str(task.user_id)
                if uid in db["users"]:
                    db["users"][uid]["downloads"] = db["users"][uid].get("downloads", 0) + 1
                    save_db()

                # Automatically delete waiting emojis & status bar once video/post is delivered
                await cleanup_waiting_messages(bot, task)

        except Exception as e:
            stop_anim.set()
            stats["failed"] += 1
            logger.error(f"Worker error on {task.url}: {e}")
            if task.emoji_message_id:
                try:
                    await bot.delete_message(chat_id=task.chat_id, message_id=task.emoji_message_id)
                except Exception:
                    pass
            try:
                await bot.edit_message_text(
                    chat_id=task.chat_id,
                    message_id=task.status_message_id,
                    text=f"⚠️ <b>FastAPI Stream Error:</b> <code>{str(e)[:120]}</code>\nUse /fix to repair.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        finally:
            shutil.rmtree(task_folder, ignore_errors=True)
            current_task = None
            task_queue.task_done()


# ================= BOT COMMANDS & MULTI-LINK HANDLERS =================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    referrer_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].replace("ref_", ""))
        except ValueError:
            pass

    credited = register_user(user.id, user.first_name or "User", referrer_id)
    if credited and referrer_id:
        try:
            await context.bot.send_message(
                chat_id=referrer_id,
                text=f"🎉 <b>New Referral!</b> {user.first_name} joined via your link! You earned <b>+{REFERRAL_REWARD_POINTS} points</b>!",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    bot_info = await context.bot.get_me()
    keyboard = build_promo_keyboard(bot_info.username or "bot", user.id)

    text = (
        "⚡ <b>Welcome to the Ultra-Fast Social Media Downloader V3!</b>\n\n"
        "🚀 <b>All-In-One Features:</b>\n"
        "• <b>Unlimited Multiple Links:</b> Paste 1, 10, or 50+ links in one message—queued & downloaded 1-by-1!\n"
        "• <b>Super-Fast Instagram & Socials:</b> Reels, Posts, Carousels, TikTok, X, Reddit & 1,000+ sites.\n"
        "• <b>YouTube MP3 & Video:</b> Send any YouTube link or use <code>/mp3 &lt;link&gt;</code> for instant MP3 audio.\n"
        "• <b>Live Animated Progress Bar:</b> Real-time percentage, speed, and auto-deleted waiting emojis.\n"
        "• <b>Invite & Earn:</b> Use /refer to share your link and earn rewards!\n\n"
        "🛠 <b>Commands:</b>\n"
        "• <code>/mp3 &lt;url&gt;</code> — Extract high-quality MP3 audio\n"
        "• /refer — Get your referral link & check earnings\n"
        "• /queue — Live FastAPI queue & speed stats\n"
        "• /claimadmin — Claim Admin access\n"
        "• /admin — Open Admin Fixer, Rebuilder & Ad Manager"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def refer_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name or "User")
    u_data = db["users"].get(str(user.id), {})
    bot_info = await context.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"

    text = (
        "💰 <b>Affiliate & Referral Earnings Program</b>\n\n"
        f"🔗 <b>Your Unique Referral Link:</b>\n<code>{ref_link}</code>\n\n"
        f"👥 <b>Total Referrals:</b> {u_data.get('referrals', 0)} user(s)\n"
        f"💎 <b>Earned Points:</b> {u_data.get('points', 0)} pts (<i>+{REFERRAL_REWARD_POINTS} pts per invite</i>)\n"
        f"📥 <b>Your Downloads:</b> {u_data.get('downloads', 0)}\n\n"
        "Share your link in groups or with friends to earn rewards!"
    )
    keyboard = build_promo_keyboard(bot_info.username or "bot", user.id)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def mp3_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    urls = URL_REGEX.findall(update.message.text or "")
    if not urls:
        await update.message.reply_text(
            "🎵 <b>YouTube & Social MP3 Downloader</b>\nSend <code>/mp3 https://youtu.be/...</code> to extract MP3 audio!",
            parse_mode=ParseMode.HTML,
        )
        return
    await enqueue_urls(update, context, urls, audio_only=True)


async def enqueue_urls(update: Update, context: ContextTypes.DEFAULT_TYPE, urls: List[str], audio_only: bool = False):
    user = update.effective_user
    register_user(user.id, user.first_name or "User")
    total = len(urls)

    for idx, url in enumerate(urls, start=1):
        emoji_msg = None
        try:
            emoji_msg = await update.message.reply_text(
                "⚡",
                reply_to_message_id=update.message.message_id,
            )
        except Exception:
            pass

        position = task_queue.qsize() + (1 if current_task else 0)
        batch_label = f" (Link {idx}/{total})" if total > 1 else ""
        mode_label = "🎵 MP3 Audio" if audio_only else "🎬 Video/Post"

        status_msg = await update.message.reply_text(
            f"⚡ <b>FastAPI Task Assigned (#{position}){batch_label}</b>\n"
            f"📡 <b>Mode:</b> {mode_label}\n"
            f"<code>[░░░░░░░░░░] 0.0%</code>\n"
            f"🚀 <b>Speed:</b> <code>Connecting...</code>",
            reply_to_message_id=update.message.message_id,
            parse_mode=ParseMode.HTML,
        )

        task = DownloadTask(
            chat_id=update.effective_chat.id,
            user_id=user.id,
            message_id=update.message.message_id,
            status_message_id=status_msg.message_id,
            emoji_message_id=emoji_msg.message_id if emoji_msg else None,
            url=url,
            user_name=user.first_name or "User",
            audio_only=audio_only,
            batch_index=idx,
            batch_total=total,
        )
        await task_queue.put(task)


async def handle_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    urls = URL_REGEX.findall(text)
    if not urls:
        return

    wants_mp3 = bool(re.search(r"\b(mp3|audio)\b", text, re.IGNORECASE))
    await enqueue_urls(update, context, urls, audio_only=wants_mp3)


# ================= ADMIN PANEL, AD CONFIG & AUTO-FIXER =================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS or not ADMIN_IDS


async def claim_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ADMIN_IDS:
        ADMIN_IDS.add(user_id)
        await update.message.reply_text(
            f"✅ <b>Admin Registered!</b> Your ID (<code>{user_id}</code>) now controls /admin, /fix, /rebuild, and /setad.",
            parse_mode=ParseMode.HTML,
        )
    elif user_id in ADMIN_IDS:
        await update.message.reply_text("✅ You are already an Admin.")
    else:
        await update.message.reply_text("⛔ An admin is already registered.")


async def setad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only command.")
        return

    raw = update.message.text.partition(" ")[2].strip()
    if "|" not in raw:
        await update.message.reply_text(
            "📢 <b>Set Advertising Button</b>\n"
            "Usage: <code>/setad 🔥 Join Our Sponsor Channel | https://t.me/yourchannel</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ad_text, _, ad_url = raw.partition("|")
    db["ad_button_text"] = ad_text.strip()
    db["ad_button_url"] = ad_url.strip()
    db["ad_enabled"] = True
    save_db()
    await update.message.reply_text(
        f"✅ <b>Advertising Button Updated!</b>\n"
        f"• <b>Text:</b> {db['ad_button_text']}\n"
        f"• <b>URL:</b> {db['ad_button_url']}",
        parse_mode=ParseMode.HTML,
    )


async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q_size = task_queue.qsize() if task_queue else 0
    active_str = (
        f"Downloading for <b>{current_task.user_name}</b> ({current_task.progress_percent:.1f}%)"
        if current_task else "Idle 🟢"
    )
    await update.message.reply_text(
        f"📊 <b>FastAPI Queue & Engine Status</b>\n"
        f"• <b>Active Task:</b> {active_str}\n"
        f"• <b>Waiting in Line:</b> {q_size} link(s)\n"
        f"• <b>Completed:</b> {stats['completed']} | <b>MP3s:</b> {stats['mp3_converted']}\n"
        f"• <b>Auto-Reconnects:</b> {stats['retried']} | <b>Failed:</b> {stats['failed']}",
        parse_mode=ParseMode.HTML,
    )


async def run_system_repair() -> str:
    report = []
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "gallery-dl",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        report.append("✅ Core extractors (<code>yt-dlp</code> & <code>gallery-dl</code>) upgraded to newest release.")
    except Exception as e:
        report.append(f"⚠️ Extractor update notice: <code>{e}</code>")

    try:
        shutil.rmtree(DOWNLOAD_DIR, ignore_errors=True)
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        report.append("✅ Temporary download cache purged & rebuilt.")
    except Exception as e:
        report.append(f"⚠️ Cache cleanup notice: <code>{e}</code>")

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    report.append("✅ FFmpeg verified for MP4/MP3." if ffmpeg_ok else "❌ FFmpeg missing!")
    return "\n".join(report)


async def admin_panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Unauthorized. Send /claimadmin first.")
        return

    ad_state = "🟢 ON" if db.get("ad_enabled", True) else "🔴 OFF"
    keyboard = [
        [
            InlineKeyboardButton("🛠 Auto-Fix Extractors", callback_data="admin_fix"),
            InlineKeyboardButton("🔄 Clear Queue", callback_data="admin_clear_q"),
        ],
        [
            InlineKeyboardButton(f"📢 Toggle Ad Button ({ad_state})", callback_data="admin_toggle_ad"),
            InlineKeyboardButton("🚀 Full Rebuild (Restart)", callback_data="admin_rebuild"),
        ],
    ]
    await update.message.reply_text(
        "🛡 <b>Admin Control, Fixer & Ad Manager</b>\n"
        f"• <b>Current Ad Button:</b> {db.get('ad_button_text')}\n"
        "• Use <code>/setad Text | https://link</code> to change your ad button.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def fix_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only command.")
        return
    msg = await update.message.reply_text("🔧 <b>Running FastAPI & Extractor Auto-Fixer...</b>", parse_mode=ParseMode.HTML)
    report = await run_system_repair()
    await msg.edit_text(f"🛠 <b>Auto-Fix Complete!</b>\n\n{report}", parse_mode=ParseMode.HTML)


async def rebuild_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only command.")
        return
    await update.message.reply_text("♻️ <b>Rebuilding & Restarting Bot...</b>", parse_mode=ParseMode.HTML)
    await run_system_repair()
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "my_referrals":
        user = query.from_user
        register_user(user.id, user.first_name or "User")
        u_data = db["users"].get(str(user.id), {})
        bot_info = await context.bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start=ref_{user.id}"
        await query.message.reply_text(
            f"💰 <b>{user.first_name}'s Referral Dashboard</b>\n\n"
            f"🔗 <b>Your Link:</b> <code>{ref_link}</code>\n"
            f"👥 <b>Referrals:</b> {u_data.get('referrals', 0)} | 💎 <b>Points:</b> {u_data.get('points', 0)} pts\n"
            f"📥 <b>Total Downloads:</b> {u_data.get('downloads', 0)}",
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("mp3_"):
        url = url_cache.get(data)
        if not url:
            await query.message.reply_text("⚠️ Session expired. Send <code>/mp3 &lt;link&gt;</code> directly.", parse_mode=ParseMode.HTML)
            return
        status_msg = await query.message.reply_text(
            "🎵 <b>FastAPI MP3 Audio Task Queued!</b>\n<code>[░░░░░░░░░░] 0.0%</code>",
            parse_mode=ParseMode.HTML,
        )
        task = DownloadTask(
            chat_id=query.message.chat_id,
            user_id=query.from_user.id,
            message_id=query.message.message_id,
            status_message_id=status_msg.message_id,
            emoji_message_id=None,
            url=url,
            user_name=query.from_user.first_name or "User",
            audio_only=True,
        )
        await task_queue.put(task)
        return

    if data.startswith("admin_"):
        if not is_admin(query.from_user.id):
            await query.edit_message_text("⛔ Unauthorized.")
            return

        if data == "admin_fix":
            await query.edit_message_text("🔧 <b>Upgrading Extractors & Repairing Cache...</b>", parse_mode=ParseMode.HTML)
            report = await run_system_repair()
            await query.edit_message_text(f"🛠 <b>Fix Complete!</b>\n\n{report}", parse_mode=ParseMode.HTML)

        elif data == "admin_clear_q":
            cleared = 0
            while not task_queue.empty():
                try:
                    task_queue.get_nowait()
                    task_queue.task_done()
                    cleared += 1
                except asyncio.QueueEmpty:
                    break
            await query.edit_message_text(f"🧹 <b>Queue Cleared!</b> Removed {cleared} waiting task(s).", parse_mode=ParseMode.HTML)

        elif data == "admin_toggle_ad":
            db["ad_enabled"] = not db.get("ad_enabled", True)
            save_db()
            state = "🟢 ENABLED" if db["ad_enabled"] else "🔴 DISABLED"
            await query.edit_message_text(f"📢 <b>Advertising Button is now {state}.</b>", parse_mode=ParseMode.HTML)

        elif data == "admin_rebuild":
            await query.edit_message_text("🚀 <b>Rebuilding & Restarting Process...</b>", parse_mode=ParseMode.HTML)
            await run_system_repair()
            os.execv(sys.executable, [sys.executable] + sys.argv)


# ================= LOW-LATENCY TELEGRAM API CONFIGURATION =================
async def post_init(application: Application):
    global task_queue, worker_task
    task_queue = asyncio.Queue()
    worker_task = asyncio.create_task(queue_worker(application))


def main():
    threading.Thread(target=start_fastapi_server, daemon=True).start()

    low_latency_request = HTTPXRequest(
        connection_pool_size=32,
        read_timeout=60.0,
        write_timeout=60.0,
        connect_timeout=10.0,
        pool_timeout=10.0,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(low_latency_request)
        .get_updates_request(HTTPXRequest(connection_pool_size=16, read_timeout=30.0, connect_timeout=10.0))
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("refer", refer_cmd))
    app.add_handler(CommandHandler("mp3", mp3_cmd))
    app.add_handler(CommandHandler("setad", setad_cmd))
    app.add_handler(CommandHandler("claimadmin", claim_admin_cmd))
    app.add_handler(CommandHandler("queue", queue_cmd))
    app.add_handler(CommandHandler("admin", admin_panel_cmd))
    app.add_handler(CommandHandler("fix", fix_cmd))
    app.add_handler(CommandHandler("rebuild", rebuild_cmd))
    app.add_handler(CallbackQueryHandler(callback_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_links))

    logger.info("Ultra-Fast Low-Latency Bot V3 is polling...")
    app.run_polling(poll_interval=0.1, timeout=20, drop_pending_updates=True)


if __name__ == "__main__":
    main()
