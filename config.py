# -*- coding: utf-8 -*-
"""
config.py — App-wide setup: OpenRouter client, database path, logging, and message sender.
Everything other modules import to do their job lives here.
"""

import os, sqlite3, threading
from datetime import datetime

# Shared lock — guards _conv_locks and _conv_timers dicts in agent.py
_meta_lock = threading.Lock()
from openai import OpenAI

# ── OpenRouter client ────────────────────────────────────────────────
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

def make_openrouter_client(api_key: str):
    return OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/v1",
    )

client = make_openrouter_client(OPENROUTER_API_KEY)

# Models to try in order — falls back on rate-limit/error
GROQ_MODELS = [
    "openai/gpt-4o-mini",        # primary — fast, cheap, follows instructions well
    "openai/gpt-3.5-turbo",      # fallback 1
    "meta-llama/llama-3.3-70b-instruct",  # fallback 2 (free tier)
]

def groq_chat(messages, max_tokens=None, temperature=None, top_p=None, timeout=30):
    """Call OpenRouter with automatic model fallback on rate-limit errors."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OpenRouter API key not configured. Set OPENROUTER_API_KEY or save the key in app settings.")

    kwargs = {"messages": messages}
    if max_tokens  is not None: kwargs["max_tokens"]  = max_tokens
    if temperature is not None: kwargs["temperature"] = temperature
    if top_p       is not None: kwargs["top_p"]       = top_p
    last_err = None
    for model in GROQ_MODELS:
        try:
            kwargs["model"] = model
            return client.chat.completions.create(**kwargs, timeout=timeout)
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower() or "quota" in err.lower():
                _log(f"[openrouter] {model} rate-limited — trying next model")
                last_err = e
                continue
            raise
    raise last_err


# Local messaging mode. Outbound messages are stored in the CRM log only.
WEBHOOK_URL = ""       # set by pyngrok at startup in app.py


# ── Database ──────────────────────────────────────────────────────────
DB = "wati_chat.db"


# ── In-memory log (last 50 entries, shown in dashboard) ──────────────
send_log = []

def _log(msg):
    ts    = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    print(entry)
    send_log.append(entry)
    if len(send_log) > 50:
        send_log.pop(0)


# ── Message sender ────────────────────────────────────────────────────
def send_message(to_number, message):
    _log(f"LOCAL ONLY — outbound message saved for {to_number}: {message[:80]}")


# ── Database init + settings loader ──────────────────────────────────
def init_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                phone_number TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                occupation TEXT DEFAULT '',
                interest_level TEXT DEFAULT 'New',
                interest_score INTEGER DEFAULT 0,
                interest_signals TEXT DEFAULT '[]',
                recommendation TEXT DEFAULT 'Just started — wait for customer reply.',
                created_at TEXT,
                updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT,
                role TEXT,
                content TEXT,
                timestamp TEXT,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Migrate existing DB — add columns if missing
        for col, typ, default in [
            ("name",                  "TEXT",    "''"),
            ("occupation",            "TEXT",    "''"),
            ("chat_mode",             "TEXT",    "'auto'"),
            ("last_processed_msg_id", "INTEGER", "0"),
            ("language_lock",         "TEXT",    "''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {typ} DEFAULT {default}")
            except Exception:
                pass
        conn.commit()


def load_settings_from_db():
    """Load OpenRouter + ngrok credentials from DB, fall back to env vars."""
    global OPENROUTER_API_KEY, client
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    cfg = {r[0]: r[1] for r in rows}

    # Reload OpenRouter client if key saved in DB
    or_key = cfg.get("openrouter_api_key", "") or OPENROUTER_API_KEY
    if or_key:
        OPENROUTER_API_KEY = or_key
        client = make_openrouter_client(OPENROUTER_API_KEY)
        print(f"[OpenRouter] Configured — key: {OPENROUTER_API_KEY[:12]}...")
    else:
        print("[OpenRouter] WARNING: No OPENROUTER_API_KEY found. Set the environment variable or save the key in settings.")

    ngrok_token = cfg.get("ngrok_token", "")
    if ngrok_token:
        try:
            from pyngrok import conf as _nc
            _nc.get_default().auth_token = ngrok_token
        except Exception:
            pass
