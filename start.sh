#!/usr/bin/env bash
set -e
echo "=================================================="
echo " Starting Ultra-Fast Telegram Downloader Bot V3"
echo "=================================================="
python3 -m pip install --upgrade pip
python3 -m pip install --upgrade -r requirements.txt
python3 bot.py
