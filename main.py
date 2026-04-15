"""SharePad — a self-hosted dontpad/envpad alternative.

URL = pad name. Anyone with the URL can read/write. Real-time sync via
WebSockets. SQLite for persistence. No accounts, no signup.

Run:
    pip install fastapi uvicorn[standard]
    python main.py
Then open http://localhost:8000/your-pad-name in your browser.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, Set

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).parent
DB_PATH = APP_DIR / "sharepad.db"
STATIC_DIR = APP_DIR / "static"

# Pad names: letters, digits, dashes, underscores, slashes (so /work/notes/today works)
VALID_PAD = re.compile(r"^[A-Za-z0-9_\-/]{1,200}$")
RESERVED = {"api", "ws", "static", "_health", "favicon.ico"}


def db():
    """Per-request SQLite connection. SQLite is fine for this workload."""
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pads (
                name        TEXT PRIMARY KEY,
                content     TEXT NOT NULL DEFAULT '',
                updated_at  REAL NOT NULL DEFAULT 0,
                version     INTEGER NOT NULL DEFAULT 0
            )
        """)


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class PadHub:
    """Tracks live WebSocket connections per pad and broadcasts updates."""

    def __init__(self):
        self.rooms: Dict[str, Set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def join(self, pad: str, ws: WebSocket):
        async with self.lock:
            self.rooms.setdefault(pad, set()).add(ws)

    async def leave(self, pad: str, ws: WebSocket):
        async with self.lock:
            room = self.rooms.get(pad)
            if room:
                room.discard(ws)
                if not room:
                    self.rooms.pop(pad, None)

    async def broadcast(self, pad: str, message: dict, exclude: WebSocket | None = None):
        room = list(self.rooms.get(pad, set()))
        text = json.dumps(message)
        for ws in room:
            if ws is exclude:
                continue
            try:
                await ws.send_text(text)
            except Exception:
                # Client may have disconnected; cleanup happens on next loop.
                pass

    def viewer_count(self, pad: str) -> int:
        return len(self.rooms.get(pad, set()))


hub = PadHub()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="SharePad", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_pad(name: str) -> dict:
    with db() as c:
        row = c.execute(
            "SELECT name, content, updated_at, version FROM pads WHERE name=?",
            (name,),
        ).fetchone()
    if not row:
        return {"name": name, "content": "", "updated_at": 0, "version": 0}
    return {"name": row[0], "content": row[1], "updated_at": row[2], "version": row[3]}


def save_pad(name: str, content: str) -> dict:
    now = time.time()
    with db() as c:
        c.execute(
            """INSERT INTO pads(name, content, updated_at, version)
               VALUES(?, ?, ?, 1)
               ON CONFLICT(name) DO UPDATE SET
                 content=excluded.content,
                 updated_at=excluded.updated_at,
                 version=pads.version+1""",
            (name, content, now),
        )
        row = c.execute(
            "SELECT version FROM pads WHERE name=?", (name,)
        ).fetchone()
    return {"version": row[0], "updated_at": now}


def valid_pad_name(name: str) -> bool:
    if not VALID_PAD.match(name):
        return False
    if name.split("/")[0] in RESERVED:
        return False
    return True


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------

@app.get("/_health")
def health():
    return {"ok": True}


@app.get("/api/pad/{pad_name:path}")
def api_get(pad_name: str):
    if not valid_pad_name(pad_name):
        raise HTTPException(400, "Invalid pad name")
    pad = get_pad(pad_name)
    pad["viewers"] = hub.viewer_count(pad_name)
    return pad


@app.put("/api/pad/{pad_name:path}")
async def api_put(pad_name: str, request: Request):
    if not valid_pad_name(pad_name):
        raise HTTPException(400, "Invalid pad name")
    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(400, "content must be a string")
    if len(content) > 1_000_000:  # 1 MB cap per pad
        raise HTTPException(413, "Pad too large (1 MB limit)")
    meta = save_pad(pad_name, content)
    await hub.broadcast(pad_name, {
        "type": "update",
        "content": content,
        "version": meta["version"],
        "updated_at": meta["updated_at"],
    })
    return meta


# ---------------------------------------------------------------------------
# WebSocket endpoint — real-time sync
# ---------------------------------------------------------------------------

@app.websocket("/ws/{pad_name:path}")
async def ws_pad(ws: WebSocket, pad_name: str):
    if not valid_pad_name(pad_name):
        await ws.close(code=4000)
        return

    await ws.accept()
    await hub.join(pad_name, ws)

    # Send initial state
    pad = get_pad(pad_name)
    await ws.send_text(json.dumps({
        "type": "init",
        "content": pad["content"],
        "version": pad["version"],
        "updated_at": pad["updated_at"],
        "viewers": hub.viewer_count(pad_name),
    }))

    # Notify others that viewer count changed
    await hub.broadcast(pad_name, {
        "type": "viewers",
        "viewers": hub.viewer_count(pad_name),
    }, exclude=ws)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "edit":
                content = msg.get("content", "")
                if not isinstance(content, str) or len(content) > 1_000_000:
                    continue
                meta = save_pad(pad_name, content)
                await hub.broadcast(pad_name, {
                    "type": "update",
                    "content": content,
                    "version": meta["version"],
                    "updated_at": meta["updated_at"],
                }, exclude=ws)
            elif msg.get("type") == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
    except WebSocketDisconnect:
        pass
    finally:
        await hub.leave(pad_name, ws)
        await hub.broadcast(pad_name, {
            "type": "viewers",
            "viewers": hub.viewer_count(pad_name),
        })


# ---------------------------------------------------------------------------
# UI routes
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def home():
    return (STATIC_DIR / "home.html").read_text(encoding="utf-8")


@app.get("/{pad_name:path}", response_class=HTMLResponse)
def pad_page(pad_name: str):
    if pad_name in RESERVED or pad_name.startswith(("api/", "ws/", "static/")):
        return JSONResponse({"error": "Reserved path"}, status_code=404)
    if not valid_pad_name(pad_name):
        return JSONResponse({"error": "Invalid pad name"}, status_code=400)
    html = (STATIC_DIR / "pad.html").read_text(encoding="utf-8")
    return html.replace("__PAD_NAME__", pad_name)


if __name__ == "__main__":
    import uvicorn
    # Bind to 0.0.0.0 so other devices on your LAN can also reach it.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
