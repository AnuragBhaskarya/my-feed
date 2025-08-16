#!/usr/bin/env python3
"""
Instagram -> Aggressive compress -> HLS -> Dropbox uploader -> Telegram bot
- Downloads Instagram videos (yt-dlp)
- Aggressive compression (smart bitrate choices)
- Generates HLS (m3u8 + .ts segments)  
- Uploads /hls/<video_id>/ to Dropbox (all segments + playlist)
- Returns Worker playlist URL

Uses WORKING TokenManager logic from your functioning bot.
"""
import os
import re
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse
import hashlib

# third-party libs
import requests
import dropbox
import yt_dlp
from dotenv import load_dotenv

# telegram
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# flask for optional HTTP API
from flask import Flask, request, jsonify
import threading
import asyncio

# load env
load_dotenv()

# ------------
# Config & Logging
# ------------
VIDEO_FOLDER = Path("video")
VIDEO_FOLDER.mkdir(parents=True, exist_ok=True)
WORKER_BASE = "https://on-demand-feed.kmxconnect.workers.dev"  # your worker domain
TOKEN_CACHE_FILE = "dropbox_token_cache.json"
DUPLICATE_DB_FILE = "processed_videos.json"

# env vars
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY")
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET")  
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

if ADMIN_CHAT_ID:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except Exception:
        ADMIN_CHAT_ID = None

# sanity check
missing = []
if not TELEGRAM_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
if not DROPBOX_APP_KEY: missing.append("DROPBOX_APP_KEY")
if not DROPBOX_APP_SECRET: missing.append("DROPBOX_APP_SECRET")
if not DROPBOX_REFRESH_TOKEN: missing.append("DROPBOX_REFRESH_TOKEN")

if missing:
    raise SystemExit(f"Missing required env vars: {', '.join(missing)}")

# logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger("insta-hls-bot")

# ------------
# WORKING TokenManager: Exact copy from your functioning bot
# ------------
class TokenManager:
    """Manages Dropbox access token caching and refresh - WORKING VERSION"""
    
    def __init__(self, app_key: str, app_secret: str, refresh_token: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.refresh_token = refresh_token
        self.access_token = None
        self.token_expires_at = None
        self.load_cached_token()

    def load_cached_token(self):
        """Load cached access token from file"""
        try:
            if Path(TOKEN_CACHE_FILE).exists():
                with open(TOKEN_CACHE_FILE, 'r') as f:
                    data = json.load(f)
                self.access_token = data.get('access_token')
                expires_str = data.get('expires_at')
                if expires_str:
                    self.token_expires_at = datetime.fromisoformat(expires_str)
                logger.info("Loaded cached Dropbox access token")
        except Exception as e:
            logger.warning(f"Could not load cached token: {e}")

    def save_token_cache(self):
        """Save access token to cache file"""
        try:
            data = {
                'access_token': self.access_token,
                'expires_at': self.token_expires_at.isoformat() if self.token_expires_at else None
            }
            with open(TOKEN_CACHE_FILE, 'w') as f:
                json.dump(data, f)
            logger.info("Saved access token to cache")
        except Exception as e:
            logger.warning(f"Could not save token cache: {e}")

    def is_token_expired(self):
        """Check if the current token is expired or will expire soon"""
        if not self.access_token or not self.token_expires_at:
            return True
        # Add 5 minute buffer to prevent using token that expires during operation
        buffer_time = timedelta(minutes=5)
        return datetime.now() >= (self.token_expires_at - buffer_time)

    def refresh_access_token(self):
        """Refresh the access token using refresh token - PROVEN WORKING METHOD"""
        try:
            logger.info("Refreshing Dropbox access token...")
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.refresh_token
            }
            auth = (self.app_key, self.app_secret)
            response = requests.post('https://api.dropbox.com/oauth2/token', data=data, auth=auth)
            
            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data['access_token']
                expires_in = token_data.get('expires_in', 14400)  # Default 4 hours
                self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                self.save_token_cache()
                logger.info("✅ Successfully refreshed access token")
                return True
            else:
                logger.error(f"❌ Token refresh failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"❌ Error refreshing token: {e}")
            return False

    def get_valid_token(self):
        """Get a valid access token, refreshing if necessary"""
        if self.is_token_expired():
            if not self.refresh_access_token():
                raise Exception("Could not obtain valid access token")
        return self.access_token

# ------------
# DuplicateTracker (simple persisted set)
# ------------
class DuplicateTracker:
    def __init__(self, db_file=DUPLICATE_DB_FILE):
        self.path = Path(db_file)
        self.set = set()
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text())
                self.set = set(data.get("processed_videos", []))
        except Exception:
            self.set = set()

    def save(self):
        try:
            self.path.write_text(json.dumps({"processed_videos": list(self.set)}))
        except Exception:
            pass

    def extract_video_id(self, url: str) -> str:
        m = re.search(r'/(p|reel)/([A-Za-z0-9_-]+)', url)
        if m:
            return m.group(2)
        # fallback: md5 short
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def is_duplicate(self, url: str) -> bool:
        return self.extract_video_id(url) in self.set

    def mark_processed(self, url: str):
        self.set.add(self.extract_video_id(url))
        self.save()

# ------------
# VideoProcessor: download, aggressive compress, generate HLS, upload
# ------------
class VideoProcessor:
    def __init__(self, token_manager: TokenManager):
        self.token_manager = token_manager
        self._dbx = None
        self.duplicate = DuplicateTracker()
        VIDEO_FOLDER.mkdir(parents=True, exist_ok=True)

    @property
    def dbx(self):
        """Get Dropbox client with valid token - WORKING VERSION"""
        if self._dbx is None or self.token_manager.is_token_expired():
            access_token = self.token_manager.get_valid_token()
            self._dbx = dropbox.Dropbox(access_token)
        return self._dbx

    def download_instagram_video(self, url: str) -> str:
        """Download via yt-dlp, return local file path."""
        logger.info("Downloading %s", url)
        vid = self.duplicate.extract_video_id(url)
        outtmpl = str(VIDEO_FOLDER / f"{vid}.%(ext)s")
        ydl_opts = {
            "format": "best[ext=mp4]/best",
            "outtmpl": outtmpl,
            "no_warnings": True,
            "ignoreerrors": True,
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
        logger.info("Downloaded to %s", filename)
        return filename

    def ffprobe_json(self, path: str) -> dict:
        try:
            cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
            r = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return json.loads(r.stdout)
        except Exception as e:
            logger.debug("ffprobe failed: %s", e)
            return {}

    def aggressive_compress(self, input_path: str) -> str:
        """Aggressive compression logic similar to your working bot."""
        logger.info("Starting aggressive compression for %s", input_path)
        p = Path(input_path)
        out = p.parent / f"compressed_{p.name}"
        
        info = self.ffprobe_json(str(p))
        fmt = info.get("format", {})
        streams = info.get("streams", [])
        duration = float(fmt.get("duration", 0.0) or 0.0)
        orig_size_mb = p.stat().st_size / (1024 * 1024)
        
        logger.info("Orig size: %.2f MB, duration: %.2fs", orig_size_mb, duration)

        # estimate current bitrate kbps
        current_bitrate_kbps = None
        if fmt.get("bit_rate"):
            try:
                current_bitrate_kbps = int(fmt.get("bit_rate")) // 1000
            except Exception:
                current_bitrate_kbps = None
        
        if not current_bitrate_kbps:
            if duration > 0:
                current_bitrate_kbps = int((orig_size_mb * 8 * 1024) / duration)
            else:
                current_bitrate_kbps = 2000

        # choose aggressive target using tiers
        if orig_size_mb > 50:
            target_kbps = int(min(current_bitrate_kbps * 0.35, 1500))
            crf = 30
            preset = "medium"
        elif orig_size_mb > 20:
            target_kbps = int(min(current_bitrate_kbps * 0.4, 2000))
            crf = 28
            preset = "medium"
        elif orig_size_mb > 5:
            target_kbps = int(min(current_bitrate_kbps * 0.45, 1500))
            crf = 27
            preset = "medium"
        else:
            target_kbps = int(min(current_bitrate_kbps * 0.5, 1000))
            crf = 26
            preset = "medium"

        target_kbps = max(target_kbps, 150)  # don't go below 150kbps
        audio_k = 96
        logger.info("Target bitrate: %dkbps (video) + %dkbps (audio); CRF %s", target_kbps, audio_k, crf)

        # Build ffmpeg command
        cmd = [
            "ffmpeg", "-y", "-i", str(p),
            "-c:v", "libx264", "-preset", preset,
            "-crf", str(crf),
            "-b:v", f"{target_kbps}k",
            "-maxrate", f"{int(target_kbps * 1.2)}k",
            "-bufsize", f"{int(target_kbps * 2)}k",
            "-c:a", "aac", "-b:a", f"{audio_k}k",
            "-movflags", "+faststart",
            str(out)
        ]
        
        logger.info("Running ffmpeg (aggressive): %s", " ".join(cmd))
        subprocess.run(cmd, check=True)

        # sanity check
        try:
            compressed_size_mb = out.stat().st_size / (1024 * 1024)
            logger.info("Compressed size: %.2f MB", compressed_size_mb)
            if compressed_size_mb > orig_size_mb:
                logger.warning("Compressed is larger than original. Re-encoding with hard lower bitrate.")
                lower_kbps = max(150, int(target_kbps * 0.6))
                cmd2 = [
                    "ffmpeg", "-y", "-i", str(p),
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", f"{lower_kbps}k", "-maxrate", f"{lower_kbps}k", "-bufsize", f"{lower_kbps*2}k",
                    "-c:a", "aac", "-b:a", "64k",
                    "-movflags", "+faststart",
                    str(out)
                ]
                subprocess.run(cmd2, check=True)
        except Exception:
            pass

        logger.info("Aggressive compression finished: %s", out)
        return str(out)

    def generate_hls(self, input_path: str, video_id: str, seg_seconds: int = 2) -> str:
        """Generate HLS segments and playlist in VIDEO_FOLDER/hls/<video_id>/"""
        outdir = VIDEO_FOLDER / "hls" / video_id
        if outdir.exists():
            shutil.rmtree(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        
        seg_pattern = str(outdir / f"{video_id}_%05d.ts")
        playlist = str(outdir / f"{video_id}.m3u8")
        
        # Generate HLS with 2-second segments for streaming
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", "scale=854:-2",
            "-c:v", "libx264", "-preset", "veryfast",
            "-b:v", "400k", "-maxrate", "400k", "-bufsize", "800k",
            "-c:a", "aac", "-b:a", "64k",
            "-hls_time", str(seg_seconds),
            "-hls_playlist_type", "vod",
            "-hls_segment_filename", seg_pattern,
            playlist
        ]
        
        logger.info("Generating HLS with ffmpeg: %s", " ".join(cmd))
        subprocess.run(cmd, check=True)
        logger.info("HLS generated at %s", outdir)
        return str(outdir)

    def upload_hls_folder(self, local_dir: str, dropbox_base="/hls"):
        """Upload all files and fix m3u8 playlist URLs"""
        local = Path(local_dir)
        if not local.exists():
            raise FileNotFoundError("HLS folder not found: " + str(local_dir))
            
        video_id = local.name
        dropbox_folder = f"{dropbox_base}/{video_id}"
        logger.info("Uploading HLS folder to Dropbox: %s", dropbox_folder)
        
        # Upload .ts segments first and collect their shared links
        segment_urls = {}
        ts_files = [f for f in local.iterdir() if f.suffix == '.ts']
        
        for f in ts_files:
            remote = f"{dropbox_folder}/{f.name}"
            logger.info("Uploading segment %s -> %s", f.name, remote)
            data = f.read_bytes()
            
            # Upload segment
            self.dbx.files_upload(data, remote, mode=dropbox.files.WriteMode.overwrite)
            
            # Get shared link for this segment
            try:
                shared_link = self.dbx.sharing_create_shared_link(remote)
                # Convert to raw URL
                raw_url = shared_link.url.replace('?dl=0', '?raw=1')
                segment_urls[f.name] = raw_url
                logger.info("✅ Segment uploaded: %s", f.name)
            except Exception as e:
                if "shared_link_already_exists" in str(e):
                    # Get existing shared link
                    links = self.dbx.sharing_list_shared_links(path=remote)
                    if links.links:
                        raw_url = links.links[0].url.replace('?dl=0', '?raw=1')
                        segment_urls[f.name] = raw_url
        
        # Process and upload .m3u8 files with fixed URLs
        m3u8_files = [f for f in local.iterdir() if f.suffix == '.m3u8']
        
        for f in m3u8_files:
            remote = f"{dropbox_folder}/{f.name}"
            logger.info("Processing playlist: %s", f.name)
            
            # Read playlist content
            content = f.read_text()
            
            # Fix relative segment URLs to absolute URLs
            import re
            def replace_segment_url(match):
                filename = match.group(0)
                if filename.endswith('.ts') and filename in segment_urls:
                    return segment_urls[filename]
                return filename
            
            # Replace segment filenames with full URLs
            fixed_content = re.sub(r'^[^#\s].*\.ts$', replace_segment_url, content, flags=re.MULTILINE)
            
            # Upload fixed playlist
            self.dbx.files_upload(
                fixed_content.encode('utf-8'), 
                remote, 
                mode=dropbox.files.WriteMode.overwrite
            )
            logger.info("✅ Fixed and uploaded playlist: %s", f.name)
            logger.info("Fixed content preview:\n%s", fixed_content[:200])
        
        logger.info("Upload complete for %s", video_id)

    def cleanup_local(self):
        try:
            if VIDEO_FOLDER.exists():
                for p in VIDEO_FOLDER.iterdir():
                    try:
                        if p.is_file():
                            p.unlink()
                        elif p.is_dir():
                            shutil.rmtree(p)
                    except Exception:
                        pass
        except Exception:
            pass

# ------------
# Telegram Bot wrapper
# ------------
class InstagramBot:
    def __init__(self, token: str, processor: VideoProcessor):
        self.processor = processor
        # create HTTPXRequest with sane defaults
        req = HTTPXRequest(connection_pool_size=20, pool_timeout=30.0, read_timeout=30.0, write_timeout=30.0, connect_timeout=10.0)
        self.bot = Bot(token, request=req)
        self.application = Application.builder().token(token).request(req).build()
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("status", self.cmd_status))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Send an Instagram post/reel URL and I'll process it to HLS and upload to Dropbox.")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Send an Instagram URL (post or reel). The bot will download, compress, create HLS segments, and upload to Dropbox for streaming via Worker.")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        processed = len(self.processor.duplicate.set)
        token_valid = not self.processor.token_manager.is_token_expired()
        await update.message.reply_text(f"✅ Processed videos: {processed}\n🔑 Token status: {'Valid' if token_valid else 'Expired'}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()
        if "instagram.com" not in url:
            await update.message.reply_text("Please send an Instagram post or reel URL.")
            return

        processing_msg = await update.message.reply_text("⏳ Starting HLS processing...")
        
        try:
            # 1) Duplicate check
            if self.processor.duplicate.is_duplicate(url):
                await processing_msg.edit_text("🔁 This video was already processed. Skipping.")
                return

            # 2) Download
            await processing_msg.edit_text("⏳ Downloading video...")
            downloaded = await context.application.run_in_executor(None, self.processor.download_instagram_video, url)

            # 3) Compress
            await processing_msg.edit_text("⏳ Aggressive compression...")
            compressed = await context.application.run_in_executor(None, self.processor.aggressive_compress, downloaded)

            # 4) Generate HLS
            await processing_msg.edit_text("⏳ Generating HLS segments...")
            vid_id = self.processor.duplicate.extract_video_id(url)
            local_hls_dir = await context.application.run_in_executor(None, self.processor.generate_hls, compressed, vid_id)

            # 5) Upload HLS folder
            await processing_msg.edit_text("⏳ Uploading HLS to Dropbox...")
            await context.application.run_in_executor(None, self.processor.upload_hls_folder, local_hls_dir)

            # 6) Success
            playlist_url = f"{WORKER_BASE}/hls/{vid_id}.m3u8"
            self.processor.duplicate.mark_processed(url)
            await processing_msg.edit_text(f"✅ HLS uploaded successfully!\n\n🎬 Playlist URL:\n{playlist_url}")

        except Exception as e:
            logger.exception("Processing error: %s", e)
            await processing_msg.edit_text(f"❌ Processing failed: {e}")
        finally:
            # cleanup local files
            try:
                await context.application.run_in_executor(None, self.processor.cleanup_local)
            except Exception:
                pass

    def run(self):
        logger.info("Starting HLS Instagram Bot...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ------------
# Optional Flask API
# ------------
def create_flask_app(bot: InstagramBot):
    app = Flask(__name__)
    
    @app.route("/process_instagram", methods=["GET"])
    def process_instagram():
        url = request.args.get("url")
        if not url:
            return jsonify({"success": False, "message": "Missing url"}), 400
        if "instagram.com" not in url:
            return jsonify({"success": False, "message": "Invalid URL"}), 400

        def bg():
            try:
                p = bot.processor
                downloaded = p.download_instagram_video(url)
                compressed = p.aggressive_compress(downloaded)
                vid = p.duplicate.extract_video_id(url)
                hls_local = p.generate_hls(compressed, vid)
                p.upload_hls_folder(hls_local)
                p.duplicate.mark_processed(url)
                p.cleanup_local()
                logger.info(f"HLS processing complete: {WORKER_BASE}/hls/{vid}.m3u8")
            except Exception as e:
                logger.exception("HTTP API bg error: %s", e)

        th = threading.Thread(target=bg, daemon=True)
        th.start()
        return jsonify({"success": True, "message": "HLS processing started"}), 202

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "time": datetime.now().isoformat()}), 200

    return app

# ------------
# Bootstrap main
# ------------
if __name__ == "__main__":
    # Test token immediately on startup
    logger.info("🔐 Testing Dropbox credentials...")
    tm = TokenManager(DROPBOX_APP_KEY, DROPBOX_APP_SECRET, DROPBOX_REFRESH_TOKEN)
    
    try:
        test_token = tm.get_valid_token()
        logger.info("✅ Dropbox token working: %s...", test_token[:20])
    except Exception as e:
        logger.error("❌ Dropbox token failed: %s", e)
        exit(1)
    
    processor = VideoProcessor(tm)
    bot = InstagramBot(TELEGRAM_TOKEN, processor)

    # optional Flask API
    if ADMIN_CHAT_ID:
        flask_app = create_flask_app(bot)
        t = threading.Thread(target=lambda: flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False), daemon=True)
        t.start()
        logger.info("Flask API started on port 5000")

    bot.run()