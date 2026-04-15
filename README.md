# SharePad

A self-hosted dontpad/envpad alternative. URL = pad name. Anyone with the
link can read/write in real time. No accounts, no signup.

## Features

- **URL-as-pad**: `http://localhost:8000/my-notes` opens a pad named `my-notes`
- **Real-time sync** via WebSockets — edits appear instantly on every viewer
- **Auto-save** to SQLite, survives server restarts
- **Persistent connections** that auto-reconnect on network blips
- **Live viewer count** — see who else is on the pad
- **Cursor preservation** when remote edits arrive
- **Nested paths** supported: `/work/standup/today` works
- **No accounts**, no signup, no tracking

## Run locally

```bash
pip install -r requirements.txt
python main.py
```

Open http://localhost:8000 in your browser. Pick a name, start typing.

## Make it reachable from the internet

Your local server runs on `localhost:8000`, which only your machine can reach.
To let others on different networks (mobile data, other wifi) access it, you
need a public URL.

### Option 1: Cloudflare Tunnel (free, recommended)

```bash
# Install cloudflared (Windows: winget install Cloudflare.cloudflared)
# Then run:
cloudflared tunnel --url http://localhost:8000
```

It prints a URL like `https://random-words.trycloudflare.com` that you can
share with anyone, anywhere. Free, no signup needed for quick tunnels.

### Option 2: ngrok (also free)

```bash
ngrok http 8000
```

Same idea — gives you a public URL that forwards to your local server.

### Option 3: deploy to your VPS (permanent)

Copy the project folder to your VPS, run `python main.py` behind nginx/Traefik
on a subdomain like `pad.cntrlflix.com`. The app binds to `0.0.0.0` already.

## Notes on the URL

The URL itself is the only access control. If someone knows the URL, they can
read and edit the pad. So:

- For private notes, use a long random name: `pad-x9q3-jk2m-zz7p`
- For shared notes, anything memorable works: `team-standup`

This is the same model dontpad/envpad use.

## Storage

Notes are stored in `sharepad.db` (SQLite) next to `main.py`. Each pad is one
row. Capped at 1 MB per pad. To wipe everything, delete the file.

## Architecture

- **Backend**: FastAPI + WebSockets + SQLite (WAL mode)
- **Frontend**: Single-file HTML per route, no build step, no framework
- **Sync**: Last-writer-wins per WebSocket message (debounced 350ms client-side)

This is intentionally simple. For production-grade collaborative editing
you'd want operational transforms (CRDTs like Y.js), but that's overkill for
notepad-style sharing where conflicts are rare and the human can resolve them.

## File layout

```
sharepad/
├── main.py              # FastAPI app
├── requirements.txt
├── README.md
└── static/
    ├── home.html        # Landing page
    └── pad.html         # The pad editor
```

## Extending

Easy additions if you want them:
- **Markdown rendering**: split the textarea, show rendered HTML on the right
- **Password-protected pads**: add a `password_hash` column, prompt on first visit
- **Auto-expire**: add a TTL column, periodic cleanup job
- **Diff history**: add a `pad_revisions` table, snapshot on every save
- **Export**: download as `.md` or `.txt`
