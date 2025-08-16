#!/usr/bin/env python3
"""
Setup script for Instagram Video Downloader Bot

This script helps you set up the bot environment and configuration.
"""

import os
import sys
import subprocess
import webbrowser
from pathlib import Path

def print_header(title):
    print(f"\n{'='*50}")
    print(f" {title}")
    print(f"{'='*50}")

def install_dependencies():
    """Install Python dependencies"""
    print_header("INSTALLING PYTHON DEPENDENCIES")

    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        print("✅ Python dependencies installed successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install dependencies: {e}")
        return False

def check_ffmpeg():
    """Check if ffmpeg is installed"""
    print_header("CHECKING FFMPEG INSTALLATION")

    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, check=True)
        print("✅ FFmpeg is installed and working!")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ FFmpeg is not installed or not in PATH")
        print("\nPlease install FFmpeg:")
        print("- Windows: Download from https://ffmpeg.org/download.html")
        print("- macOS: brew install ffmpeg")
        print("- Linux: sudo apt install ffmpeg (Ubuntu/Debian)")
        return False

def setup_telegram_bot():
    """Guide user through Telegram bot setup"""
    print_header("TELEGRAM BOT SETUP")

    print("1. Open Telegram and search for @BotFather")
    print("2. Send /newbot command")
    print("3. Follow the prompts to create your bot")
    print("4. Copy the bot token when provided")
    print()

    token = input("Enter your Telegram bot token: ").strip()
    if not token:
        print("❌ No token provided")
        return None

    return token

def setup_dropbox_app():
    """Guide user through Dropbox app setup"""
    print_header("DROPBOX APP SETUP")

    print("Setting up Dropbox API access...")
    print("1. Go to https://www.dropbox.com/developers/apps")
    print("2. Click 'Create app'")
    print("3. Choose 'Scoped access' and 'Full Dropbox'")
    print("4. Name your app")
    print()

    app_key = input("Enter your Dropbox App Key: ").strip()
    if not app_key:
        print("❌ No app key provided")
        return None, None, None

    app_secret = input("Enter your Dropbox App Secret: ").strip()
    if not app_secret:
        print("❌ No app secret provided")
        return None, None, None

    # Generate refresh token
    print("\nGenerating refresh token...")
    auth_url = f"https://www.dropbox.com/oauth2/authorize?client_id={app_key}&response_type=code&token_access_type=offline"

    print(f"\n1. Opening authorization URL: {auth_url}")
    try:
        webbrowser.open(auth_url)
    except:
        print("Could not open browser automatically. Please copy the URL above.")

    print("2. Click 'Allow' and copy the authorization code")
    auth_code = input("\nEnter the authorization code: ").strip()

    if not auth_code:
        print("❌ No authorization code provided")
        return None, None, None

    # Get refresh token
    try:
        import requests
        import base64

        auth_header = base64.b64encode(f'{app_key}:{app_secret}'.encode()).decode()

        headers = {
            'Authorization': f'Basic {auth_header}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'code': auth_code,
            'grant_type': 'authorization_code'
        }

        response = requests.post('https://api.dropbox.com/oauth2/token', headers=headers, data=data)

        if response.status_code == 200:
            token_data = response.json()
            refresh_token = token_data['refresh_token']
            print("✅ Refresh token obtained successfully!")
            return app_key, app_secret, refresh_token
        else:
            print(f"❌ Failed to get refresh token: {response.text}")
            return None, None, None

    except Exception as e:
        print(f"❌ Error getting refresh token: {e}")
        return None, None, None

def create_env_file(telegram_token, dropbox_key, dropbox_secret, dropbox_refresh):
    """Create .env file with configuration"""
    print_header("CREATING CONFIGURATION FILE")

    env_content = f"""# Instagram Video Downloader Bot Configuration

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN={telegram_token}

# Dropbox API Configuration
DROPBOX_APP_KEY={dropbox_key}
DROPBOX_APP_SECRET={dropbox_secret}
DROPBOX_REFRESH_TOKEN={dropbox_refresh}
"""

    try:
        with open('.env', 'w') as f:
            f.write(env_content)
        print("✅ Configuration file created: .env")

        # Also create a script to load environment variables
        load_env_script = """#!/bin/bash
# Load environment variables from .env file
export $(cat .env | grep -v '^#' | xargs)
echo "Environment variables loaded!"
echo "Run: python main.py"
"""

        with open('load_env.sh', 'w') as f:
            f.write(load_env_script)

        # Make script executable on Unix systems
        try:
            os.chmod('load_env.sh', 0o755)
        except:
            pass

        print("✅ Environment loader created: load_env.sh")
        return True

    except Exception as e:
        print(f"❌ Failed to create configuration: {e}")
        return False

def setup_cookies():
    """Guide user through cookies setup"""
    print_header("INSTAGRAM COOKIES SETUP (OPTIONAL)")

    print("For downloading private Instagram content, you need browser cookies.")
    print("\nSteps:")
    print("1. Install 'Get cookies.txt LOCALLY' browser extension")
    print("2. Go to instagram.com and log in")
    print("3. Click the extension icon and export cookies")
    print("4. Save the file as 'instagram_cookies.txt' in this directory")
    print("\n⚠️ Without cookies, only public content will be accessible.")

    setup_cookies = input("\nDo you want to set up cookies now? (y/n): ").lower().strip()

    if setup_cookies == 'y':
        print("\n📋 Instructions:")
        print("1. Chrome/Edge: https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc")
        print("2. Firefox: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/")
        print("3. After installation, visit Instagram and export cookies as 'instagram_cookies.txt'")

        input("\nPress Enter when you've saved the cookies file...")

        if Path('instagram_cookies.txt').exists():
            print("✅ Cookies file found!")
        else:
            print("⚠️ Cookies file not found. You can add it later.")

def main():
    """Main setup function"""
    print("🎬 Instagram Video Downloader Bot Setup")
    print("=" * 50)

    # Check Python version
    if sys.version_info < (3, 7):
        print("❌ Python 3.7 or higher is required")
        return

    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")

    # Install dependencies
    if not install_dependencies():
        print("\n❌ Setup failed: Could not install dependencies")
        return

    # Check ffmpeg
    ffmpeg_ok = check_ffmpeg()
    if not ffmpeg_ok:
        cont = input("\nContinue without ffmpeg? (Video compression will not work) y/n: ")
        if cont.lower() != 'y':
            return

    # Setup Telegram bot
    telegram_token = setup_telegram_bot()
    if not telegram_token:
        print("\n❌ Setup failed: Telegram bot token required")
        return

    # Setup Dropbox
    dropbox_key, dropbox_secret, dropbox_refresh = setup_dropbox_app()
    if not all([dropbox_key, dropbox_secret, dropbox_refresh]):
        print("\n❌ Setup failed: Dropbox configuration required")
        return

    # Create configuration file
    if not create_env_file(telegram_token, dropbox_key, dropbox_secret, dropbox_refresh):
        print("\n❌ Setup failed: Could not create configuration")
        return

    # Setup cookies (optional)
    setup_cookies()

    # Final instructions
    print_header("SETUP COMPLETE!")
    print("🎉 Your Instagram Video Downloader Bot is ready!")
    print("\n📋 Next steps:")
    print("1. Load environment variables: source load_env.sh  (Linux/Mac)")
    print("   Or manually set environment variables on Windows")
    print("2. Run the bot: python main.py")
    print("3. Send Instagram URLs to your bot!")
    print("\n📁 Files created:")
    print("- main.py (main bot script)")
    print("- requirements.txt (dependencies)")
    print("- .env (configuration)")
    print("- load_env.sh (environment loader)")
    print("- setup.py (this script)")

    if Path('instagram_cookies.txt').exists():
        print("- instagram_cookies.txt (cookies for private content)")

if __name__ == "__main__":
    main()
