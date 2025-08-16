# my_feed — Personal Instagram-style Video Feed 🌊🎞️

Build your own scrollable, Instagram-like feed that plays videos stored in a private Dropbox **app folder**—no social network, no tracking, no ads.

## 🏗️ System Architecture

```

                   ┌──────────────────────────────────────────────────┐
                   │                Local Machine                     │
                   │                                                  │
                   │  instagram URL → compression_bot.py              │
                   │     · yt-dlp download                            │
                   │     · aggressive FFmpeg re-encode                │
                   │     · HLS playlist + TS segments                 │
                   │     · upload → Dropbox /hls/<video_id>/          │
                   └──────────────────────▲───────────────────────────┘
                                          │
                                          │ Dropbox App Folder
                                          ▼
┌───────────────────────────────┐   Cron every minute  ┌─────────────────────────────┐
│  videos_json_bot (Worker)     │─────────────────────▶│   videos.json in GitHub     │
│  -  refresh Dropbox token     │                      │  -  raw download links      │
│  -  list files / create links │                      │  -  updated only on change  │
│  -  diff \& commit to GitHub  │◀─────────────────────┤  (public branch for Pages)  │
└───────────────────────────────┘                      └─────────────────────────────┘
▲
│ HTTP GET
▼
┌────────────────────────────────┐
│  Cloudflare Pages “player/”    │
│  -  Fetch videos.json          │
│  -  Shuffle order per session  │
│  -  Smart buffer / auto-play  │
│  -  Global mute \& scrubber    │
└────────────────────────────────┘

```

---

## 🌳 Repository Layout

```

my-feed/
├── compression_bot/        \# Python + Telegram + Flask (optional API)
│   ├── compression_bot.py
│   ├── requirements.txt
│   └── .env.example
├── videos_json_bot/        \# Cloudflare Worker
│   ├── worker.js
│   └── wrangler.toml
└── player/                 \# Cloudflare Pages front-end
├── index.html
├── sw.js                   \# optional PWA service-worker
└── static/                 \# icons, manifest, etc.

```

---

## ✨ Component Overview

### 1. Front-end Player (`player/`)

• **Vanilla-JS feed** with scroll-snap, IntersectionObserver auto-play/pause, shuffled order, and an always-visible timeline scrubber.  
• **Global mute toggle** (persists in `localStorage`) and “tap to unmute” hint.  
• **Smart buffering** – pre-loads the next video, throttles distant ones.  
• **Wake-lock** API keeps the screen on during playback.  
• **PWA-ready** – manifest + service-worker for offline shell.  
• Tested on iOS Safari, Chrome Android, and desktop Chromium.

### 2. `videos_json_bot/` (Cloudflare Worker)

| Feature | Details |
|---------|---------|
| Dropbox OAuth | Refresh-token flow, 5-minute safety buffer |
| File listing  | `/2/files/list_folder` root of the app folder |
| Shared links  | Re-use existing link or create new; converted to `?raw=1` |
| GitHub commit | Uses a fine-grained PAT to PUT `videos.json` via REST v3 |
| Cron trigger  | `* * * * *` (every minute) — skips commit if list unchanged |
| Endpoints     | `/fetch` (manual run), `/videos.json` (proxy), `/debug`, `/github-test-detailed` |

### 3. `compression_bot/` (Python)

• **yt-dlp** downloads reel/post URL → MP4.  
• **Aggressive FFmpeg profile** picks target bitrate/CRF based on input size.  
• **HLS Packaging** (`ffmpeg -hls_time 2`) generates `.m3u8` + `.ts` segments.  
• **Dropbox upload**: entire `/hls/<video_id>/` folder; rewrites the playlist to absolute raw links.  
• **Duplicate tracking** via hash/ID file; prevents re-processing.  
• **TokenManager** caches Dropbox access token on disk.  
• **Telegram Bot**: send an IG URL → returns ready-to-stream playlist URL.  
• **Optional Flask API** (`/process_instagram?url=`) for programmatic ingestion.

---

## 🚀 Quick-start

### Prerequisites

| Component | Requirements |
|-----------|--------------|
| Cloudflare | Workers + Pages (free tier OK) |
| Dropbox    | App Folder, `files.content.write/read`, refresh token |
| GitHub     | Public repo (e.g. `video-api`) with PAT **scoped to `contents:write`** |
| Local Machine | Python 3.8+, FFmpeg, yt-dlp, `pip` |

---

### 1. Deploy `videos_json_bot`

```

cd videos_json_bot
wrangler kv:namespace create "CACHE_KV"          \# optional if you add caching

# Add namespace IDs in wrangler.toml if used

wrangler secret put DROPBOX_REFRESH_TOKEN
wrangler secret put DROPBOX_APP_KEY
wrangler secret put DROPBOX_APP_SECRET
wrangler secret put GITHUB_TOKEN
wrangler secret put GITHUB_OWNER            \# e.g. so9ic
wrangler secret put GITHUB_REPO             \# e.g. video-api
wrangler deploy

```

Cron is already defined in `wrangler.toml`:

```

[triggers]
crons = ["* * * * *"]   \# every minute

```

### 2. Publish the Player

1. In Cloudflare dashboard → Pages → **Create Project**.  
2. Select the `player/` folder as root.  
3. Build command: `none` (static).  
4. Set custom domain or use `<project>.pages.dev`.

The player expects:

```

https://<GITHUB_OWNER>.github.io/<GITHUB_REPO>/videos.json

```

If your GitHub Pages branch is `gh-pages`, enable Pages in repo settings.

### 3. Run the Compression Bot (optional but full pipeline)

```

cd compression_bot
python -m venv venv \&\& source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        \# fill tokens + chat IDs
python compression_bot.py   \# starts Telegram bot + optional Flask on :5000

```

Send any Instagram post/reel URL to your bot; after ~1–3 min it replies with an HLS playlist URL that is already playable by your feed.

---

## 🔧 Environment Variables

### `videos_json_bot` (Cloudflare Secrets)

```

DROPBOX_REFRESH_TOKEN=sl.xxxxx
DROPBOX_APP_KEY=app_key
DROPBOX_APP_SECRET=app_secret
GITHUB_TOKEN=ghp_xxx           \# needs 'contents:write'
GITHUB_OWNER=so9ic
GITHUB_REPO=video-api

```

### `compression_bot/.env`

```


# Telegram

TELEGRAM_BOT_TOKEN=bot123:ABC
ADMIN_CHAT_ID=123456789

# Dropbox

DROPBOX_APP_KEY=app_key
DROPBOX_APP_SECRET=app_secret
DROPBOX_REFRESH_TOKEN=sl.xxxx

# (Optional)  Worker playlist base

WORKER_BASE=https://on-demand-feed.kmxconnect.workers.dev

```

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| Player loads but shows “Failed to load videos” | Check GitHub Pages URL and ensure `videos.json` is public and valid JSON |
| Worker logs “Token refresh failed 400” | Verify **refresh token** and app credentials; refresh tokens are account-specific |
| GitHub 403 when committing | PAT lacks `contents:write` or repo is private—add `public_repo` or use a repo-scoped token |
| Feed videos mute even after tapping | Browser blocks audio until a user gesture—tap once anywhere or press **Global Mute** button |
| Telegram bot replies “Processing failed: ffmpeg not found” | Install FFmpeg and make sure it’s in PATH (`ffmpeg -version`) |

---

## 📝 License

MIT — fork, tweak, build your own private feed.

## 🙏 Credits

Built with Cloudflare Workers, yt-dlp, FFmpeg, Dropbox API, and lots of espresso.

<div style="text-align: center">⁂</div>
