# ⚡ Telegram Ultra-Fast Social Media Downloader Bot V3 (All-in-One Package)

This single consolidated ZIP file (`telegram-social-downloader-bot-v3.zip`) contains the complete source code, Docker configuration, cloud deployment blueprints, and startup scripts for your Telegram Downloader Bot.

---

## 🌟 Complete Feature Set Included
1. **Multi-Link Batch Queue (`asyncio.Queue`)**: Send 1, 10, or 50+ links in one message; each task is queued and downloaded 1-by-1 with batch progress (`[Link 1/5]`, `[Link 2/5]`).
2. **FastAPI + Uvicorn Server (`PORT 8080`)**: Sub-millisecond health dashboard (`/`) and JSON telemetry (`/api/status`) so free cloud platforms stay awake 24/7.
3. **Low-Latency Telegram API (`HTTPXRequest`)**: 32-connection HTTPX pool with `poll_interval=0.1` and concurrent update handling.
4. **Live Animated Status Bar & Auto-Deleted Emojis**: Real-time progress bar (`[██████░░░░] 60.0%`), speed (`MB/s`), and `ETA`. Both the animated emoji (`⚡`) and progress message automatically delete once the media is delivered.
5. **YouTube MP3 Extraction (`/mp3` + Inline Button)**: Extract 320kbps MP3 audio via `/mp3 <link>` or tap the **🎵 Download MP3 Audio** button below any downloaded YouTube video.
6. **Advertising & Referral System (`/refer` & `/setad`)**:
   - Every delivered file includes your custom **Sponsor / Ad Button** plus **💰 Invite & Earn** and **📊 My Earnings** buttons.
   - Admins can update the ad button live in Telegram: `/setad 🔥 Join Our Channel | https://t.me/yourchannel`.
7. **Admin Auto-Fixer & Rebuilder (`/admin`, `/fix`, `/rebuild`)**: Live-upgrades `yt-dlp` and `gallery-dl`, clears stuck cache, and restarts the bot process directly from Telegram chat.

---

## 🚀 Quick Deployment Steps

### Option 1: Render.com (Free Docker + FFmpeg Pre-Installed)
1. Unzip this file and upload all 10 files to a **Private GitHub Repository**.
2. Sign in to [https://render.com](https://render.com) ➔ **New +** ➔ **Web Service** ➔ Connect your repository.
3. Select **Docker** as the Runtime and **Free** as the instance type.
4. Click **Create Web Service**. Add your Render URL to [https://cron-job.org](https://cron-job.org) (every 5 minutes) to keep it awake 24/7.

### Option 2: Hugging Face Spaces (No GitHub Required — Direct Upload)
1. Sign in to [https://huggingface.co](https://huggingface.co) ➔ **New Space** ➔ Select **Docker (Blank)** ➔ **Free CPU**.
2. Upload `Dockerfile`, `requirements.txt`, `bot.py`, and `cookies.txt`.
3. Once the Space says **Running**, open your bot on Telegram and send `/start` and `/claimadmin`.
