# Instagram Video Downloader Telegram Bot

A powerful Telegram bot that downloads Instagram videos, compresses them for optimal storage, and uploads them to Dropbox with automatic cleanup.

## 🎯 Features

- **Instagram Download**: Downloads videos from Instagram posts and reels using yt-dlp
- **Browser Cookies Support**: Access private content using browser cookies
- **Smart Compression**: Reduces video size from ~10MB to 3-5MB with minimal quality loss
- **Dropbox Integration**: Automatic upload to Dropbox with shared link generation
- **Auto Cleanup**: Removes local files after processing to save storage
- **Real-time Updates**: Live progress updates during processing
- **Error Handling**: Robust error handling with user-friendly messages

## 📋 Requirements

### System Dependencies
- **Python 3.7+**
- **FFmpeg** (for video compression)

### Python Dependencies
- `python-telegram-bot>=20.0`
- `yt-dlp>=2023.1.6`
- `dropbox>=11.36.0`
- `requests>=2.28.0`

## 🚀 Quick Setup

### Method 1: Automated Setup (Recommended)

1. **Clone/Download the files**
2. **Run the setup script:**
   ```bash
   python setup.py
   ```
3. **Follow the interactive prompts to configure:**
   - Telegram Bot Token
   - Dropbox App credentials
   - Instagram cookies (optional)

### Method 2: Manual Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install FFmpeg:**
   - **Windows**: Download from [ffmpeg.org](https://ffmpeg.org/download.html)
   - **macOS**: `brew install ffmpeg`
   - **Linux**: `sudo apt install ffmpeg`

3. **Create Telegram Bot:**
   - Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send `/newbot` and follow instructions
   - Save the bot token

4. **Setup Dropbox App:**
   - Go to [Dropbox App Console](https://www.dropbox.com/developers/apps)
   - Create new app with "Scoped access" and "Full Dropbox"
   - Note down App Key and App Secret
   - Generate refresh token (see instructions below)

5. **Configure Environment Variables:**
   ```bash
   export TELEGRAM_BOT_TOKEN="your_bot_token"
   export DROPBOX_APP_KEY="your_app_key"
   export DROPBOX_APP_SECRET="your_app_secret"
   export DROPBOX_REFRESH_TOKEN="your_refresh_token"
   ```

## 🔑 Getting Dropbox Refresh Token

1. **Replace `YOUR_APP_KEY` and visit this URL:**
   ```
   https://www.dropbox.com/oauth2/authorize?client_id=YOUR_APP_KEY&response_type=code&token_access_type=offline
   ```

2. **Authorize the app and copy the authorization code**

3. **Use this Python script to get refresh token:**
   ```python
   import requests
   import base64

   APP_KEY = "your_app_key"
   APP_SECRET = "your_app_secret"
   AUTH_CODE = "authorization_code_from_step_2"

   auth_header = base64.b64encode(f'{APP_KEY}:{APP_SECRET}'.encode()).decode()

   response = requests.post('https://api.dropbox.com/oauth2/token', 
       headers={'Authorization': f'Basic {auth_header}'},
       data={'code': AUTH_CODE, 'grant_type': 'authorization_code'})

   print(response.json()['refresh_token'])
   ```

## 🍪 Instagram Cookies Setup (Optional)

For accessing private Instagram content:

1. **Install browser extension:**
   - [Chrome/Edge](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)
   - [Firefox](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)

2. **Export cookies:**
   - Login to Instagram
   - Click the extension icon
   - Export cookies as `instagram_cookies.txt`
   - Place file in bot directory

## 🏃‍♂️ Running the Bot

### Linux/macOS
```bash
source load_env.sh  # Load environment variables
python3 main.py
```

### Windows
```cmd
set TELEGRAM_BOT_TOKEN=your_token
set DROPBOX_APP_KEY=your_key
set DROPBOX_APP_SECRET=your_secret
set DROPBOX_REFRESH_TOKEN=your_refresh_token
python main.py
```

## 🎮 Bot Usage

1. **Start the bot:** Send `/start` to your bot
2. **Send Instagram URL:** Paste any Instagram post or reel URL
3. **Wait for processing:** Bot will show real-time progress
4. **Get download link:** Receive Dropbox link for the compressed video

### Supported URLs
- `https://www.instagram.com/p/POST_ID/`
- `https://www.instagram.com/reel/REEL_ID/`

### Bot Commands
- `/start` - Welcome message and instructions
- `/help` - Detailed help information  
- `/status` - Check bot status and configuration

## 📁 File Structure

```
instagram-bot/
├── main.py      # Main bot script
├── requirements.txt      # Python dependencies
├── setup.py             # Interactive setup script
├── load_env.sh          # Environment loader (Unix)
├── .env                 # Configuration file (created by setup)
├── instagram_cookies.txt # Instagram cookies (optional)
├── video/               # Temporary video storage (auto-created)
└── README.md            # This file
```

## ⚙️ Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | ✅ |
| `DROPBOX_APP_KEY` | Dropbox app key | ✅ |
| `DROPBOX_APP_SECRET` | Dropbox app secret | ✅ |
| `DROPBOX_REFRESH_TOKEN` | Dropbox refresh token | ✅ |

### Compression Settings

You can modify these constants in `main.py`:

```python
MAX_FILE_SIZE_MB = 50    # Max size before compression
TARGET_SIZE_MB = 5       # Target size after compression
COOKIES_FILE = "instagram_cookies.txt"  # Cookies file path
```

## 🔧 Advanced Configuration

### Video Compression Parameters

The bot uses optimal FFmpeg settings:
- **Codec**: H.264 (libx264)
- **CRF**: 23-28 (dynamic based on file size)
- **Preset**: Medium (balance of speed/quality)
- **Audio**: AAC 128kbps
- **Resolution**: Auto-scaled to 720p for large files

### Dropbox Upload

- Files uploaded to root directory (`/`)
- Automatic shared link generation
- Overwrites existing files with same name
- Uses refresh token for persistent access

## 🐛 Troubleshooting

### Common Issues

1. **"FFmpeg not found"**
   - Install FFmpeg and add to PATH
   - Verify with `ffmpeg -version`

2. **"Instagram login required"**
   - Export and update browser cookies
   - Ensure cookies file exists and is valid

3. **"Dropbox authentication failed"**
   - Verify app key/secret are correct
   - Check refresh token validity
   - Ensure app has proper permissions

4. **"Video download failed"**
   - Check if URL is valid Instagram link
   - Verify internet connection
   - Update yt-dlp: `pip install -U yt-dlp`

### Debug Mode

Enable verbose logging by modifying the logging level:

```python
logging.basicConfig(level=logging.DEBUG)
```

## 🚦 Rate Limits & Best Practices

- **Instagram**: No specific rate limits, but avoid excessive requests
- **Dropbox**: 1,000 API calls per app per hour
- **Telegram**: 30 messages per second per bot

## 📄 License

This project is licensed under the MIT License. See LICENSE file for details.

## 🤝 Contributing

1. Fork the repository
2. Create feature branch: `git checkout -b feature-name`
3. Commit changes: `git commit -am 'Add feature'`
4. Push to branch: `git push origin feature-name`
5. Submit pull request

## ⚠️ Disclaimer

This bot is for educational purposes. Ensure you comply with Instagram's Terms of Service and respect content creators' rights. The bot should only be used to download content you have permission to access.

## 📞 Support

If you encounter issues:
1. Check the troubleshooting section
2. Review bot logs for error messages
3. Ensure all dependencies are properly installed
4. Verify configuration settings

---

**Happy downloading! 🎬✨**
