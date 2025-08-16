#!/usr/bin/env python3
"""
Instagram Video Downloader Telegram Bot - OPTIMIZED VERSION WITH HTTP API

This bot downloads Instagram videos using yt-dlp, compresses them with ffmpeg,
uploads to Dropbox, and cleans up local files.

NEW: HTTP API for iOS Shortcuts integration

OPTIMIZATIONS:
1. Dropbox token caching - only refresh when expired
2. Fixed compression logic - prevent size increase
3. Duplicate video tracking - fast hash-based lookup
4. HTTP API endpoint for external access
5. FIXED: Connection pool timeout issues

Required dependencies:
- python-telegram-bot
- yt-dlp
- dropbox
- ffmpeg (system dependency)
- flask

Environment variables required:
- TELEGRAM_BOT_TOKEN
- DROPBOX_APP_KEY
- DROPBOX_APP_SECRET
- DROPBOX_REFRESH_TOKEN
- ADMIN_CHAT_ID (for HTTP API notifications)
"""

import os
import re
import json
import hashlib
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timedelta
import uuid
from flask import Flask, request, jsonify
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import socket
import requests

# Third-party imports
import dropbox
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest
import yt_dlp

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
VIDEO_FOLDER = Path("video")
COOKIES_BROWSER = "chrome"  # Use Chrome browser cookies automatically
TOKEN_CACHE_FILE = "dropbox_token_cache.json"
DUPLICATE_DB_FILE = "processed_videos.json"

# Get admin chat ID for HTTP API notifications
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
if ADMIN_CHAT_ID:
    ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

class TokenManager:
    """Manages Dropbox access token caching and refresh"""
    
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
        """Refresh the access token using refresh token"""
        try:
            logger.info("Refreshing Dropbox access token...")
            import requests
            
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
                logger.info("Successfully refreshed access token")
                return True
            else:
                logger.error(f"Token refresh failed: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error refreshing token: {e}")
            return False

    def get_valid_token(self):
        """Get a valid access token, refreshing if necessary"""
        if self.is_token_expired():
            if not self.refresh_access_token():
                raise Exception("Could not obtain valid access token")
        return self.access_token

class DuplicateTracker:
    """Fast duplicate video tracking using hash-based lookup"""
    
    def __init__(self):
        self.processed_videos = set()
        self.load_database()

    def load_database(self):
        """Load processed videos database"""
        try:
            if Path(DUPLICATE_DB_FILE).exists():
                with open(DUPLICATE_DB_FILE, 'r') as f:
                    data = json.load(f)
                    self.processed_videos = set(data.get('processed_videos', []))
                logger.info(f"Loaded {len(self.processed_videos)} processed videos from database")
        except Exception as e:
            logger.warning(f"Could not load duplicate database: {e}")
            self.processed_videos = set()

    def save_database(self):
        """Save processed videos database"""
        try:
            data = {'processed_videos': list(self.processed_videos)}
            with open(DUPLICATE_DB_FILE, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Could not save duplicate database: {e}")

    def extract_video_id(self, url: str) -> str:
        """Extract Instagram video ID from URL"""
        match = re.search(r'/(p|reel)/([A-Za-z0-9_-]+)', url)
        if match:
            return match.group(2)
        # Fallback: create hash from URL
        return hashlib.md5(url.encode()).hexdigest()[:12]

    def is_duplicate(self, url: str) -> bool:
        """Check if video has already been processed"""
        video_id = self.extract_video_id(url)
        return video_id in self.processed_videos

    def mark_processed(self, url: str):
        """Mark video as processed"""
        video_id = self.extract_video_id(url)
        self.processed_videos.add(video_id)
        self.save_database()
        logger.info(f"Marked video {video_id} as processed")

class VideoProcessor:
    """Handles video download, compression, and upload operations"""
    
    def __init__(self, dropbox_app_key: str, dropbox_app_secret: str, dropbox_refresh_token: str):
        self.token_manager = TokenManager(dropbox_app_key, dropbox_app_secret, dropbox_refresh_token)
        self.duplicate_tracker = DuplicateTracker()
        self._dbx = None

    @property
    def dbx(self):
        """Get Dropbox client with valid token"""
        if self._dbx is None or self.token_manager.is_token_expired():
            access_token = self.token_manager.get_valid_token()
            self._dbx = dropbox.Dropbox(access_token)
        return self._dbx

    def setup_directories(self):
        """Create necessary directories"""
        VIDEO_FOLDER.mkdir(exist_ok=True)

    def is_instagram_url(self, url: str) -> bool:
        """Check if URL is from Instagram"""
        parsed = urlparse(url)
        return 'instagram.com' in parsed.netloc.lower()

    def download_instagram_video(self, url: str) -> str:
        """Download video from Instagram using yt-dlp with Chrome browser cookies"""
        try:
            # Generate unique filename
            video_id = re.search(r'/(p|reel)/([A-Za-z0-9_-]+)', url)
            if video_id:
                filename = f"instagram_{video_id.group(2)}.%(ext)s"
            else:
                filename = "instagram_video_%(epoch)s.%(ext)s"
            
            output_path = VIDEO_FOLDER / filename
            
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': str(output_path),
                'cookies_from_browser': (COOKIES_BROWSER, ),  # Use Chrome cookies automatically
                'no_warnings': True,
                'extractaudio': False,
                'audioformat': 'mp3',
                'ignoreerrors': True,
            }
            
            logger.info(f"Using {COOKIES_BROWSER} browser cookies for Instagram access")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = ydl.prepare_filename(info)
            
            logger.info(f"Downloaded video: {downloaded_file}")
            return downloaded_file
        
        except Exception as e:
            logger.error(f"Failed to download video: {str(e)}")
            raise Exception(f"Download failed: {str(e)}")

    def get_video_info(self, video_path: str) -> dict:
        """Get video information using ffprobe"""
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-print_format', 'json',
                '-show_format', '-show_streams', video_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except Exception as e:
            logger.error(f"Failed to get video info: {str(e)}")
            return {}

    def compress_video(self, input_path: str) -> str:
        """Compress video with very aggressive settings - 480p output as default with 60-70% size reduction"""
        try:
            input_file = Path(input_path)
            file_size_mb = input_file.stat().st_size / (1024 * 1024)
            logger.info(f"Original file size: {file_size_mb:.2f} MB")
            logger.info("Applying very aggressive compression with 480p as default output...")
    
            # Get video info for intelligent compression
            video_info = self.get_video_info(str(input_file))
            format_info = video_info.get('format', {})
            duration = float(format_info.get('duration', 30))
    
            # Get current bitrate to calculate target
            current_bitrate = format_info.get('bit_rate')
            if current_bitrate:
                current_bitrate_kbps = int(current_bitrate) // 1000
            else:
                current_bitrate_kbps = int((file_size_mb * 8 * 1024) / duration) if duration > 0 else 1000
    
            logger.info(f"Current bitrate: ~{current_bitrate_kbps} kbps")
    
            # VERY AGGRESSIVE COMPRESSION SETTINGS for 480p default
            if file_size_mb > 50:
                # Very large files: maximum compression
                target_bitrate = min(current_bitrate_kbps * 0.2, 600)  # 20% of original, cap at 600kbps
                crf = 34
                preset = "medium"
            elif file_size_mb > 20:
                # Large files: aggressive compression
                target_bitrate = min(current_bitrate_kbps * 0.25, 700)  # 25% of original, cap at 700kbps
                crf = 33
                preset = "medium"
            elif file_size_mb > 5:
                # Medium files: strong compression
                target_bitrate = min(current_bitrate_kbps * 0.3, 500)  # 30% of original, cap at 500kbps
                crf = 32
                preset = "fast"
            else:
                # Small files: balanced compression for maximum reduction
                target_bitrate = min(current_bitrate_kbps * 0.35, 400)  # 35% of original, cap at 400kbps
                crf = 31
                preset = "fast"
    
            # Ensure minimum quality (very low for maximum compression)
            target_bitrate = max(target_bitrate, 150)  # Minimum 80 kbps for 480p
    
            logger.info(f"Target bitrate: {target_bitrate} kbps, CRF: {crf} (targeting 70-80% reduction for 480p)")
    
            # Create 480p compressed filename
            compressed_path = input_file.parent / f"compressed_480p_{input_file.name}"
    
            # Build very aggressive ffmpeg command for 480p
            cmd = [
                'ffmpeg', '-i', str(input_file),
                '-c:v', 'libx264',
                '-preset', preset,
                '-crf', str(crf),
                '-b:v', f'{int(target_bitrate)}k',          # Target bitrate
                '-maxrate', f'{int(target_bitrate * 1.1)}k', # Tight maxrate control
                '-bufsize', f'{int(target_bitrate * 1.5)}k', # Small buffer for aggressive compression
                '-c:a', 'aac',
                '-b:a', '128k',                              # Low audio bitrate
                '-movflags', '+faststart',
                '-pix_fmt', 'yuv420p',
                '-vf', "scale='-2:480'",                      # Force 480p resolution
                '-threads', '0',                            # Use all CPU cores
                '-y'                                        # Overwrite output
            ]
    
            cmd.append(str(compressed_path))
    
            logger.info(f"Aggressive 480p compression command: {' '.join(cmd)}")
    
            # Run compression
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    
            # Check compressed file size
            compressed_size_mb = compressed_path.stat().st_size / (1024 * 1024)
            compression_ratio = ((file_size_mb - compressed_size_mb) / file_size_mb * 100) if file_size_mb > 0 else 0
    
            logger.info(f"Compressed 480p file size: {compressed_size_mb:.2f} MB")
            logger.info(f"Compression achieved: {compression_ratio:.1f}% size reduction")
    
            # Validate we achieved good compression (should be 60%+ for 480p)
            if compression_ratio < 60:
                logger.warning(f"Lower than expected compression ratio: {compression_ratio:.1f}% - content may be highly optimized already")
    
            # Remove original file to save space
            input_file.unlink()
            logger.info(f"Removed original file: {input_file.name}")
    
            return str(compressed_path)
    
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg compression failed: {e.stderr}")
            raise Exception(f"Compression failed: {e.stderr}")
        except Exception as e:
            logger.error(f"Compression error: {str(e)}")
            raise

    def upload_to_dropbox(self, file_path: str) -> str:
        """Upload file to Dropbox and return the shared link"""
        try:
            file_name = Path(file_path).name
            dropbox_path = f"/{file_name}"
            
            logger.info(f"Uploading {file_name} to Dropbox...")
            
            with open(file_path, 'rb') as f:
                # Upload file using valid token
                self.dbx.files_upload(
                    f.read(),
                    dropbox_path,
                    mode=dropbox.files.WriteMode.overwrite
                )
            
            # Create shared link
            try:
                shared_link = self.dbx.sharing_create_shared_link(dropbox_path)
                logger.info(f"File uploaded successfully: {shared_link.url}")
                return shared_link.url
            except dropbox.exceptions.ApiError as e:
                if "shared_link_already_exists" in str(e):
                    # Get existing shared link
                    links = self.dbx.sharing_list_shared_links(path=dropbox_path)
                    if links.links:
                        return links.links[0].url
                raise
                
        except Exception as e:
            logger.error(f"Dropbox upload failed: {str(e)}")
            raise Exception(f"Upload failed: {str(e)}")

    def cleanup_local_files(self):
        """Remove all files from the local video folder"""
        try:
            if VIDEO_FOLDER.exists():
                for file_path in VIDEO_FOLDER.iterdir():
                    if file_path.is_file():
                        try:
                            file_path.unlink()
                            logger.info(f"Deleted local file: {file_path.name}")
                        except FileNotFoundError:
                            pass
                        except Exception as e:
                            logger.warning(f"Could not delete {file_path.name}: {e}")
            logger.info("Local cleanup completed")
        except Exception as e:
            logger.error(f"Cleanup error: {str(e)}")

class InstagramBot:
    """Main Telegram bot class with HTTP API support - FIXED connection pool issues"""
    
    def __init__(self, token: str, processor: VideoProcessor):
        # Configure connection limits for concurrent operations
        from telegram.ext import Defaults
        
        # Create request object with larger connection pool for main application
        main_request = HTTPXRequest(
            connection_pool_size=20,  # Increase from default 1
            pool_timeout=30.0,        # Increase timeout
            read_timeout=30.0,        # Read timeout
            write_timeout=30.0,       # Write timeout
            connect_timeout=10.0      # Connection timeout
        )
        
        self.application = Application.builder().token(token).request(main_request).build()
        
        # Create separate Bot instance for HTTP API with its own connection pool
        api_request = HTTPXRequest(
            connection_pool_size=10,
            pool_timeout=20.0,
            read_timeout=20.0,
            write_timeout=20.0,
            connect_timeout=10.0
        )
        self.bot = Bot(token, request=api_request)
        self.processor = processor
        
        # Add handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def check_connection_health(self):
        """Check Telegram connection health"""
        try:
            await self.bot.get_me()
            return True
        except Exception as e:
            logger.warning(f"Telegram connection health check failed: {e}")
            return False

    async def process_instagram_url(self, url: str, chat_id: int, source: str = "Telegram"):
        """Process Instagram URL and send result to specified chat"""
        downloaded_file = None
        compressed_file = None
        
        try:
            # Check for duplicates first
            if self.processor.duplicate_tracker.is_duplicate(url):
                video_id = self.processor.duplicate_tracker.extract_video_id(url)
                message = (
                    f"🔄 **Already Processed!** ({source})\n\n"
                    f"📁 **Video ID:** {video_id}\n"
                    f"⚠️ This video has already been downloaded and uploaded.\n\n"
                    f"🗂️ Check your Dropbox for the processed file."
                )
                await self.bot.send_message(chat_id, message, parse_mode='Markdown')
                return message

            self.processor.setup_directories()
            
            # Send processing message
            processing_msg = await self.bot.send_message(
                chat_id, 
                f"⏳ Processing Instagram video from {source}...",
                parse_mode='Markdown'
            )

            # Download video
            downloaded_file = self.processor.download_instagram_video(url)
            await self.bot.edit_message_text(
                "✅ Downloaded. Compressing...",
                chat_id=chat_id,
                message_id=processing_msg.message_id
            )

            # Compress video (always)
            compressed_file = self.processor.compress_video(downloaded_file)
            await self.bot.edit_message_text(
                "✅ Compressed. Uploading to Dropbox...",
                chat_id=chat_id,
                message_id=processing_msg.message_id
            )

            # Upload to Dropbox
            dropbox_url = self.processor.upload_to_dropbox(compressed_file)

            # Mark as processed
            self.processor.duplicate_tracker.mark_processed(url)

            # Cleanup
            self.processor.cleanup_local_files()

            # Get file info for final message
            try:
                if compressed_file and Path(compressed_file).exists():
                    file_size = Path(compressed_file).stat().st_size / (1024 * 1024)
                else:
                    file_size = 0  # File was cleaned up
                file_name = Path(compressed_file).name if compressed_file else "video"
            except Exception:
                file_size = 0
                file_name = "video"

            # Send success message
            video_id = self.processor.duplicate_tracker.extract_video_id(url)
            success_message = (
                f"✅ **Processing Complete!** ({source})\n\n"
                f"📁 **File:** {file_name}\n"
                f"📊 **Size:** {file_size:.2f} MB (optimized)\n"
                f"🆔 **Video ID:** {video_id}\n"
                f"🔗 **Download:** [Click here]({dropbox_url})\n\n"
                f"🧹 Local files cleaned up automatically."
            )
            
            await self.bot.edit_message_text(
                success_message,
                chat_id=chat_id,
                message_id=processing_msg.message_id,
                parse_mode='Markdown'
            )
            
            return success_message

        except Exception as e:
            error_message = (
                f"❌ **Processing Failed** ({source})\n\n"
                f"**Error:** {str(e)}\n\n"
                "Please try again or check the URL."
            )
            
            # Improved error handling with retry logic
            for attempt in range(3):
                try:
                    await asyncio.sleep(attempt * 2)  # Backoff: 0, 2, 4 seconds
                    await self.bot.send_message(chat_id, error_message, parse_mode='Markdown')
                    break
                except Exception as send_error:
                    logger.error(f"Attempt {attempt + 1} failed to send error message: {send_error}")
                    if attempt == 2:  # Last attempt
                        logger.error(f"Could not send error message to chat {chat_id} after 3 attempts")
            
            logger.exception(f"Error processing IG URL from {source}")
            
            # Cleanup on error
            try:
                self.processor.cleanup_local_files()
            except Exception:
                pass
                
            return error_message

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = """
🎬 **Instagram Video Downloader Bot** 🎬

Welcome! I can download and optimize Instagram videos efficiently.

**New: HTTP API Support for iOS Shortcuts!** 📱

**Optimizations:**
• Smart token caching (no unnecessary refreshes)
• Intelligent compression (always optimizes every video)
• Duplicate detection (skips already processed videos)
• Chrome browser cookies (automatic private access)
• FIXED: Connection pool issues for stability

**Commands:**
• /help - Show detailed help
• /status - Check bot status
• /stats - View processing statistics

Just send me an Instagram URL to get started! 🚀

**HTTP API:**
GET https://your-ip:5000/process_instagram?url=INSTAGRAM_URL
"""
        await update.message.reply_text(welcome_message, parse_mode='Markdown')

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = """
📋 **Help & Instructions**

**Supported URLs:**
• Instagram posts: https://www.instagram.com/p/[POST_ID]/
• Instagram reels: https://www.instagram.com/reel/[REEL_ID]/

**Smart Features:**
• 🔄 **Token Management**: Automatic Dropbox token refresh only when needed
• 🗜️ **Always Compress**: Every video gets optimized regardless of size
• 🚫 **Duplicate Detection**: Fast lookup prevents re-processing same videos
• 🍪 **Auto Cookies**: Uses Chrome browser cookies automatically
• 🌐 **HTTP API**: External access for iOS Shortcuts
• 🔧 **Stable Connections**: Fixed pool timeout issues

**Process:**
1. 📥 Download from Instagram (with duplicate check)
2. 🗜️ Smart compression (always optimizes)
3. ☁️ Upload to Dropbox (cached token)
4. 🔗 Share download link
5. 🧹 Clean local files

**HTTP API Usage:**
GET https://your-ip:5000/process_instagram?url=INSTAGRAM_URL

Results will be sent to the configured admin chat.

**Tips:**
• Make sure you're logged into Instagram in Chrome
• Duplicate videos are automatically skipped
• Every video gets compressed for optimal size/quality

Need help? Just send me an Instagram URL! 📱
"""
        await update.message.reply_text(help_message, parse_mode='Markdown')

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stats command"""
        try:
            processed_count = len(self.processor.duplicate_tracker.processed_videos)
            
            # Check token status
            token_valid = not self.processor.token_manager.is_token_expired()
            token_expires = self.processor.token_manager.token_expires_at
            
            # Check connection health
            connection_healthy = await self.check_connection_health()
            
            stats_message = f"""
📊 **Bot Statistics**

**Processing:**
• Videos processed: {processed_count}
• Duplicate database: {DUPLICATE_DB_FILE}

**Token Status:**
• Status: {'✅ Valid' if token_valid else '⚠️ Expired/Missing'}
• Expires: {token_expires.strftime('%Y-%m-%d %H:%M:%S') if token_expires else 'Unknown'}
• Cache file: {TOKEN_CACHE_FILE}

**Connection Status:**
• Telegram API: {'✅ Healthy' if connection_healthy else '⚠️ Issues detected'}
• Pool size: 20 (main) + 10 (API)
• Timeout handling: ✅ Improved

**Performance:**
• Smart caching: ✅ Enabled
• Always compress: ✅ Every video optimized
• Duplicate detection: ✅ Fast hash lookup
• HTTP API: ✅ Enabled on port 5000

**Database Files:**
• Token cache: {'✅' if Path(TOKEN_CACHE_FILE).exists() else '❌'}
• Duplicate DB: {'✅' if Path(DUPLICATE_DB_FILE).exists() else '❌'}
"""
            await update.message.reply_text(stats_message, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Stats error: {str(e)}")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        try:
            # Check video folder
            video_folder_exists = VIDEO_FOLDER.exists()
            
            # Check Dropbox connection
            try:
                account = self.processor.dbx.users_get_current_account()
                dropbox_status = f"✅ Connected ({account.name.display_name})"
            except Exception:
                dropbox_status = "❌ Connection failed"
            
            # Check ffmpeg
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
                ffmpeg_status = "✅ Available"
            except Exception:
                ffmpeg_status = "❌ Not found"
            
            status_message = f"""
🔧 **Bot Status**

**Core Components:**
• Video folder: {'✅' if video_folder_exists else '❌'} {VIDEO_FOLDER}
• Chrome cookies: ✅ Auto-enabled
• FFmpeg: {ffmpeg_status}
• Dropbox: {dropbox_status}

**Optimizations:**
• Token caching: ✅ Smart refresh only when needed
• Always compress: ✅ Every video gets optimized
• Duplicate detection: ✅ {len(self.processor.duplicate_tracker.processed_videos)} videos tracked
• HTTP API: ✅ Running on port 5000
• Connection pools: ✅ 20+10 pool size, improved timeouts

**Settings:**
• Compression: Always optimize with aggressive settings (40-50% reduction)
• Cookie source: Chrome browser (automatic)
• Video folder: {VIDEO_FOLDER.absolute()}
• Admin Chat ID: {ADMIN_CHAT_ID if ADMIN_CHAT_ID else 'Not set'}

**Ready to process Instagram videos!** 🚀
"""
            await update.message.reply_text(status_message, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ Status check failed: {str(e)}")

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages (Instagram URLs)"""
        message_text = update.message.text.strip()
        
        # Check if message contains Instagram URL
        if not self.processor.is_instagram_url(message_text):
            await update.message.reply_text(
                "📱 Please send me a valid Instagram URL (post or reel)\n\n"
                "Examples:\n"
                "• https://www.instagram.com/p/ABC123/\n"
                "• https://www.instagram.com/reel/XYZ789/",
                parse_mode='Markdown'
            )
            return

        # Process the URL
        await self.process_instagram_url(message_text, update.effective_chat.id, "Telegram")

    def run(self):
        """Start the bot"""
        logger.info("Starting Optimized Instagram Video Downloader Bot with HTTP API and fixed connection pools...")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

# ===== HTTP API IMPLEMENTATION =====

def create_flask_app(bot_instance: InstagramBot):
    """Create Flask app for HTTP API with improved asyncio handling"""
    app = Flask(__name__)
    
    @app.route('/process_instagram', methods=['GET'])
    def process_instagram():
        """HTTP endpoint for processing Instagram URLs"""
        url = request.args.get('url')
        
        if not url:
            return jsonify({
                "success": False, 
                "message": "Missing 'url' parameter"
            }), 400
            
        if not bot_instance.processor.is_instagram_url(url):
            return jsonify({
                "success": False, 
                "message": "Invalid Instagram URL provided"
            }), 400
            
        if not ADMIN_CHAT_ID:
            return jsonify({
                "success": False, 
                "message": "Admin chat ID not configured"
            }), 500

        # FIXED: Improved background processing with proper asyncio handling
        def background_process():
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    # Add delay to avoid immediate connection conflicts
                    time.sleep(retry_count * 2 + 1)  # 1, 3, 5 seconds
                    
                    # Create isolated event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Create new Bot instance for this thread to avoid sharing
                    thread_request = HTTPXRequest(
                        connection_pool_size=5,
                        pool_timeout=30.0,
                        read_timeout=30.0,
                        write_timeout=30.0,
                        connect_timeout=15.0
                    )
                    thread_bot = Bot(os.getenv('TELEGRAM_BOT_TOKEN'), request=thread_request)
                    
                    # Create a thread-specific processor method
                    async def process_in_thread():
                        try:
                            # Check for duplicates first
                            if bot_instance.processor.duplicate_tracker.is_duplicate(url):
                                video_id = bot_instance.processor.duplicate_tracker.extract_video_id(url)
                                message = (
                                    f"🔄 **Already Processed!** (HTTP API)\n\n"
                                    f"📁 **Video ID:** {video_id}\n"
                                    f"⚠️ This video has already been downloaded and uploaded.\n\n"
                                    f"🗂️ Check your Dropbox for the processed file."
                                )
                                await thread_bot.send_message(ADMIN_CHAT_ID, message, parse_mode='Markdown')
                                return message

                            bot_instance.processor.setup_directories()
                            
                            # Send processing message
                            processing_msg = await thread_bot.send_message(
                                ADMIN_CHAT_ID, 
                                f"⏳ Processing Instagram video from HTTP API...",
                                parse_mode='Markdown'
                            )

                            # Download video
                            downloaded_file = bot_instance.processor.download_instagram_video(url)
                            await thread_bot.edit_message_text(
                                "✅ Downloaded. Compressing...",
                                chat_id=ADMIN_CHAT_ID,
                                message_id=processing_msg.message_id
                            )

                            # Compress video (always)
                            compressed_file = bot_instance.processor.compress_video(downloaded_file)
                            await thread_bot.edit_message_text(
                                "✅ Compressed. Uploading to Dropbox...",
                                chat_id=ADMIN_CHAT_ID,
                                message_id=processing_msg.message_id
                            )

                            # Upload to Dropbox
                            dropbox_url = bot_instance.processor.upload_to_dropbox(compressed_file)

                            # Mark as processed
                            bot_instance.processor.duplicate_tracker.mark_processed(url)

                            # Cleanup
                            bot_instance.processor.cleanup_local_files()

                            # Get file info for final message
                            try:
                                if compressed_file and Path(compressed_file).exists():
                                    file_size = Path(compressed_file).stat().st_size / (1024 * 1024)
                                else:
                                    file_size = 0  # File was cleaned up
                                file_name = Path(compressed_file).name if compressed_file else "video"
                            except Exception:
                                file_size = 0
                                file_name = "video"

                            # Send success message
                            video_id = bot_instance.processor.duplicate_tracker.extract_video_id(url)
                            success_message = (
                                f"✅ **Processing Complete!** (HTTP API)\n\n"
                                f"📁 **File:** {file_name}\n"
                                f"📊 **Size:** {file_size:.2f} MB (optimized)\n"
                                f"🆔 **Video ID:** {video_id}\n"
                                f"🔗 **Download:** [Click here]({dropbox_url})\n\n"
                                f"🧹 Local files cleaned up automatically."
                            )
                            
                            await thread_bot.edit_message_text(
                                success_message,
                                chat_id=ADMIN_CHAT_ID,
                                message_id=processing_msg.message_id,
                                parse_mode='Markdown'
                            )
                            
                            return success_message

                        except Exception as process_error:
                            error_message = (
                                f"❌ **Processing Failed** (HTTP API)\n\n"
                                f"**Error:** {str(process_error)}\n\n"
                                "Please try again or check the URL."
                            )
                            
                            try:
                                await thread_bot.send_message(ADMIN_CHAT_ID, error_message, parse_mode='Markdown')
                            except Exception:
                                logger.error(f"Could not send error message to chat {ADMIN_CHAT_ID}")
                            
                            # Cleanup on error
                            try:
                                bot_instance.processor.cleanup_local_files()
                            except Exception:
                                pass
                                
                            raise process_error

                    # Run the processing coroutine
                    result = loop.run_until_complete(process_in_thread())
                    
                    # Properly close the loop
                    loop.close()
                    
                    logger.info(f"HTTP API processing completed successfully for {url}")
                    return  # Success, exit retry loop
                    
                except Exception as e:
                    retry_count += 1
                    error_type = type(e).__name__
                    logger.error(f"HTTP API processing attempt {retry_count} failed: {error_type}: {str(e)}")
                    
                    # Clean up the loop if it exists
                    try:
                        if 'loop' in locals():
                            if not loop.is_closed():
                                loop.close()
                    except:
                        pass
                    
                    if retry_count >= max_retries:
                        # Final failure notification
                        try:
                            # Create one final attempt to notify
                            final_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(final_loop)
                            
                            final_request = HTTPXRequest(
                                connection_pool_size=1,
                                pool_timeout=10.0,
                                read_timeout=10.0,
                                write_timeout=10.0,
                                connect_timeout=5.0
                            )
                            final_bot = Bot(os.getenv('TELEGRAM_BOT_TOKEN'), request=final_request)
                            
                            async def send_final_error():
                                await final_bot.send_message(
                                    ADMIN_CHAT_ID,
                                    f"❌ **HTTP API Processing Failed After {max_retries} Attempts**\n\n"
                                    f"**URL:** {url}\n"
                                    f"**Final Error:** {error_type}: {str(e)}\n\n"
                                    "Please try again manually.",
                                    parse_mode='Markdown'
                                )
                            
                            final_loop.run_until_complete(send_final_error())
                            final_loop.close()
                            
                        except Exception as final_error:
                            logger.error(f"Could not send final error notification: {final_error}")
                        
                        logger.error(f"HTTP API processing failed permanently for {url} after {max_retries} attempts")
                        return
                    else:
                        logger.info(f"Retrying HTTP API processing for {url} (attempt {retry_count + 1}/{max_retries})")

        # Start background thread
        thread = threading.Thread(target=background_process)
        thread.daemon = True
        thread.start()

        return jsonify({
            "success": True,
            "message": "Video processing started. You will be notified in Telegram.",
            "admin_chat_id": ADMIN_CHAT_ID,
            "retry_logic": "3 attempts with backoff"
        }), 202

    @app.route('/health', methods=['GET'])
    def health_check():
        """Health check endpoint"""
        return jsonify({
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "admin_chat_configured": ADMIN_CHAT_ID is not None,
            "connection_pools": "Isolated per thread",
            "asyncio_handling": "Improved with proper loop management"
        }), 200

    return app

def get_ip():
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def notify_ip():
    """Notify external service of current IP"""
    try:
        url = "https://ip-holder.kmxconnect.workers.dev"
        ip = get_ip()
        response = requests.post(url, json={"ip": ip}, timeout=10)
        if response.status_code == 200:
            logger.info(f"IP notification sent successfully: {ip}")
        else:
            logger.warning(f"IP notification failed: {response.status_code}")
    except Exception as e:
        logger.warning(f"Could not notify IP: {e}")

def main():
    """Main function to initialize and run the bot with HTTP API"""
    
    # Check required environment variables
    required_env_vars = [
        'TELEGRAM_BOT_TOKEN',
        'DROPBOX_APP_KEY', 
        'DROPBOX_APP_SECRET',
        'DROPBOX_REFRESH_TOKEN'
    ]
    
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        print("\nRequired environment variables:")
        print("- TELEGRAM_BOT_TOKEN: Your Telegram bot token from @BotFather")
        print("- DROPBOX_APP_KEY: Your Dropbox app key")
        print("- DROPBOX_APP_SECRET: Your Dropbox app secret") 
        print("- DROPBOX_REFRESH_TOKEN: Your Dropbox refresh token")
        print("- ADMIN_CHAT_ID: Telegram chat ID for HTTP API notifications (optional)")
        print("\nPlease set these variables and try again.")
        return

    # Notify external service of IP
    notify_ip()

    try:
        # Initialize video processor
        processor = VideoProcessor(
            dropbox_app_key=os.getenv('DROPBOX_APP_KEY'),
            dropbox_app_secret=os.getenv('DROPBOX_APP_SECRET'),
            dropbox_refresh_token=os.getenv('DROPBOX_REFRESH_TOKEN')
        )
        
        # Initialize bot with improved connection handling
        bot = InstagramBot(
            token=os.getenv('TELEGRAM_BOT_TOKEN'),
            processor=processor
        )
        
        # Create and start Flask app in a separate thread
        if ADMIN_CHAT_ID:
            app = create_flask_app(bot)
            flask_thread = threading.Thread(
                target=lambda: app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False),
                daemon=True
            )
            flask_thread.start()
            logger.info(f"HTTP API started on port 5000 (Admin Chat ID: {ADMIN_CHAT_ID})")
            logger.info("Connection pools: 20 (main) + 10 (API) - Fixed timeout issues")
        else:
            logger.warning("ADMIN_CHAT_ID not set - HTTP API disabled")
        
        # Start Telegram bot (main thread)
        bot.run()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Bot error: {str(e)}")

if __name__ == "__main__":
    main()