# -*- coding: utf-8 -*-
"""
app.py — Entry point. Start the app by running: python app.py

Project structure:
  app.py      ← you are here — starts Flask + ngrok
  config.py   ← Groq client, DB setup, logging, message sender
  prompts.py  ← Alex's personality, sales script, industry knowledge
  agent.py    ← AI reply engine (analyze, detect occupation, process_and_reply)
  routes.py   ← All Flask API endpoints and inbound webhook
  templates/
    index.html ← The dashboard UI
"""

from flask import Flask
from config import init_db, load_settings_from_db
from routes import register_routes

app = Flask(__name__)

# ── Boot sequence ─────────────────────────────────────────────────────
init_db()               # create tables if they don't exist, run migrations
load_settings_from_db() # load OpenRouter + ngrok credentials from DB
register_routes(app)    # attach all API routes

# ── Start ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, io, config
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    # Start ngrok tunnel so an inbound provider can reach the webhook
    try:
        from pyngrok import ngrok as _ngrok
        tunnel = _ngrok.connect(5000)
        config.WEBHOOK_URL = tunnel.public_url + "/webhook"
        print(f"\n{'='*60}")
        print(f"  WEBHOOK URL:")
        print(f"  {config.WEBHOOK_URL}")
        print(f"{'='*60}\n")
    except Exception as e:
        print(f"[ngrok] Could not start tunnel: {e}")
        print("[ngrok] Set WEBHOOK_URL manually or use Settings panel.\n")

    app.run(debug=True, threaded=True, use_reloader=False)
