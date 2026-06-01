# -*- coding: utf-8 -*-
"""
routes.py — All Flask API endpoints and the webhook.

Each route is one clear action. Import this module in app.py after
creating the Flask app so all routes register correctly.

Route map:
  GET  /                              → serve the dashboard HTML
  GET  /api/conversations             → list all conversations
  POST /api/conversations             → create new conversation + send opening message
  GET  /api/conversations/<id>        → get messages + analysis for one conversation
  DELETE /api/conversations/<id>      → delete a conversation
  POST /api/conversations/<id>/mode   → switch auto ↔ manual mode
  POST /api/chat                      → test-mode chat (local, instant reply)
  POST /api/analyze/<id>              → run fresh AI interest analysis
  POST /api/resend-opening/<id>       → resend Alex's greeting locally
  POST /api/manual-send               → operator sends a message manually
  POST /api/upload-csv                → bulk-add customers from CSV file
  POST /webhook                       → Inbound provider calls this when customer replies
  GET  /api/logs                      → last 50 log lines
  GET  /api/diagnose                  → live health check (ngrok, OpenRouter)
  GET  /api/config                    → current settings
  POST /api/config                    → save ngrok credentials
  POST /api/test-send                 → preview a test outbound message
"""

import sqlite3, uuid, json, csv, io, time, re, random
from datetime import datetime
from flask import request, jsonify, render_template

import config
from config  import DB, send_message, _log, send_log, groq_chat
from prompts import SYSTEM_PROMPT
from agent   import analyze_interest, detect_and_save_occupation, schedule_reply, guardrail_reply


def get_opening_message() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        options = ("Good morning! How's it going?", "Morning! Hope your day's off to a good start", "Hey, good morning!")
    elif 12 <= hour < 17:
        options = ("Hey! Hope your day's going well", "Hey there! How's your afternoon?", "Hi! What's going on?")
    elif 17 <= hour < 21:
        options = ("Hey! Hope the evening's going well", "Evening! How was your day?", "Hey! How's it going?")
    else:
        options = ("Hey! Hope you're having a good night", "Hey! How's your night going?", "Hi! Still up?")
    return random.choice(options)


def register_routes(app):
    """Attach all routes to the Flask app. Called once in app.py."""

    # ── Dashboard ─────────────────────────────────────────────────────
    @app.route("/")
    def index():
        return render_template("index.html")


    # ── List conversations ─────────────────────────────────────────────
    @app.route("/api/conversations", methods=["GET"])
    def get_conversations():
        with sqlite3.connect(DB) as conn:
            rows = conn.execute("""
                SELECT c.id, c.phone_number, c.name, c.occupation, c.interest_level,
                       c.interest_score, c.recommendation, c.updated_at,
                       (SELECT content  FROM messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1),
                       (SELECT role     FROM messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1),
                       (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id),
                       c.chat_mode
                FROM conversations c ORDER BY c.updated_at DESC
            """).fetchall()
        return jsonify([{
            "id": r[0], "phone_number": r[1], "name": r[2] or "",
            "occupation": r[3] or "", "interest_level": r[4],
            "interest_score": r[5], "recommendation": r[6], "updated_at": r[7],
            "last_message": r[8] or "", "last_message_role": r[9] or "",
            "message_count": r[10] or 0, "chat_mode": r[11] or "auto"
        } for r in rows])


    # ── Create conversation ────────────────────────────────────────────
    @app.route("/api/conversations", methods=["POST"])
    def new_conversation():
        data       = request.json
        phone      = data.get("phone_number", "").strip()
        name       = data.get("name", "").strip()
        occupation = data.get("occupation", "").strip()
        if not phone:
            return jsonify({"error": "phone_number is required"}), 400

        conv_id = str(uuid.uuid4())
        now     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ts      = datetime.now().strftime("%I:%M %p")

        try:
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO conversations (id, phone_number, name, occupation, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                    (conv_id, phone, name, occupation, now, now)
                )
                opening = get_opening_message()
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (conv_id, opening, ts)
                )
                conn.commit()
            send_message(phone, opening)
            return jsonify({"id": conv_id, "phone_number": phone, "opening_message": opening, "timestamp": ts})

        except sqlite3.IntegrityError:
            # Phone already exists — just return the existing conversation id
            with sqlite3.connect(DB) as conn:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE phone_number=?", (phone,)
                ).fetchone()
                existing_id = row[0]
            return jsonify({"id": existing_id, "phone_number": phone, "existing": True})


    # ── Get messages + analysis for one conversation ───────────────────
    @app.route("/api/conversations/<conv_id>", methods=["GET"])
    def get_messages(conv_id):
        with sqlite3.connect(DB) as conn:
            msgs = conn.execute(
                "SELECT role, content, timestamp FROM messages WHERE conversation_id=? ORDER BY id ASC",
                (conv_id,)
            ).fetchall()
            info = conn.execute(
                "SELECT phone_number, name, occupation, interest_level, interest_score, interest_signals, recommendation, chat_mode FROM conversations WHERE id=?",
                (conv_id,)
            ).fetchone()

        analysis = {}
        if info:
            try:    signals = json.loads(info[5]) if info[5] else []
            except: signals = []
            analysis = {
                "phone_number": info[0] or "", "name": info[1] or "",
                "occupation": info[2] or "", "level": info[3] or "New",
                "score": info[4] or 0, "signals": signals,
                "recommendation": info[6] or ""
            }
        return jsonify({
            "messages":  [{"role": r[0], "content": r[1] or "", "timestamp": r[2] or ""} for r in (msgs or [])],
            "chat_mode": info[7] if info else "auto",
            "analysis":  analysis
        })


    # ── Delete conversation ────────────────────────────────────────────
    @app.route("/api/conversations/<conv_id>", methods=["DELETE"])
    def delete_conversation(conv_id):
        with sqlite3.connect(DB) as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
            conn.commit()
        return jsonify({"ok": True})


    # ── Switch auto ↔ manual mode ──────────────────────────────────────
    @app.route("/api/conversations/<conv_id>/mode", methods=["POST"])
    def set_mode(conv_id):
        mode = (request.json or {}).get("mode", "auto")
        if mode not in ("auto", "manual"):
            return jsonify({"error": "mode must be auto or manual"}), 400
        with sqlite3.connect(DB) as conn:
            conn.execute("UPDATE conversations SET chat_mode=? WHERE id=?", (mode, conv_id))
            conn.commit()
        return jsonify({"ok": True, "mode": mode})


    # ── Test-mode chat (local only) ───────────────────────────────────
    @app.route("/api/chat", methods=["POST"])
    def chat():
        data         = request.json
        conv_id      = (data.get("conversation_id") or "").strip()
        customer_msg = (data.get("message")         or "").strip()
        if not conv_id or not customer_msg:
            return jsonify({"error": "conversation_id and message required"}), 400

        now_ts = datetime.now().strftime("%I:%M %p")
        now_db = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB) as conn:
            conv_row = conn.execute("SELECT id, occupation, chat_mode FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not conv_row:
                return jsonify({"error": "Conversation not found"}), 404
            occupation = conv_row[1] or ""
            chat_mode = conv_row[2] or "auto"
            history = list(reversed(conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 40",
                (conv_id,)
            ).fetchall()))
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
                (conv_id, customer_msg, now_ts)
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_db, conv_id))
            conn.commit()

        if chat_mode == "manual":
            return jsonify({"reply": "", "manual": True})

        recent_user_msgs = [content for role, content in history if role == "user"] + [customer_msg]
        guarded = guardrail_reply(conv_id, customer_msg, recent_user_msgs, occupation)
        if guarded:
            reply, switch_manual = guarded
            reply_ts = datetime.now().strftime("%I:%M %p")
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (conv_id, reply, reply_ts)
                )
                if switch_manual:
                    conn.execute(
                        "UPDATE conversations SET updated_at=?, chat_mode='manual' WHERE id=?",
                        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), conv_id)
                    )
                else:
                    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                                 (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), conv_id))
                conn.commit()
            return jsonify({"reply": reply, "timestamp": reply_ts, "guardrail": True})

        from agent import _get_conversation_language, _LANG_INSTRUCTION, _sanitize_english, _sanitize_punjabi, _sanitize_hindi
        lang      = _get_conversation_language(conv_id)
        lang_lock = _LANG_INSTRUCTION.get(lang, _LANG_INSTRUCTION["english"])
        system_prompt = SYSTEM_PROMPT
        if occupation:
            system_prompt += f"\n\nKnown customer business/occupation: {occupation}. Use this only as context; do not invent details."
        messages = [{"role": "system", "content": system_prompt + "\n\n" + lang_lock}]
        for role, content in history:
            messages.append({"role": "user" if role=="user" else "assistant", "content": content})
        messages.append({"role": "system", "content": f"[REMINDER] {lang_lock}"})
        messages.append({"role": "user", "content": customer_msg})

        try:
            response = groq_chat(messages=messages, temperature=0.6, top_p=0.9, max_tokens=45, timeout=20)
            reply    = (response.choices[0].message.content or "").strip() or "hmm give me a sec 😄"
            if lang == "english":
                reply = _sanitize_english(reply)
            elif lang == "punjabi":
                reply = _sanitize_punjabi(reply)
            elif lang == "hindi":
                reply = _sanitize_hindi(reply)
            _medical_pattern = re.compile(
                r'\b(remedies?|ginger|antacid|painkillers?|ibuprofen|paracetamol|heat pack|ice pack|'
                r'pain reliever|doctor|physician|medical|tablet|medicine|treatment|diagnos\w*|symptoms?)\b',
                re.I
            )
            if _medical_pattern.search(reply):
                if lang == "punjabi":
                    reply = "eh mera area ni aa. sehat wali gal layi doctor naal gal karni vadia rahegi. baad ch gal karange."
                elif lang == "hindi":
                    reply = "yeh mera area nahi hai. health wali baat ke liye doctor se baat karna better rahega. baad mein baat karenge."
                else:
                    reply = "That is not my area. For health stuff, it is better to check with a doctor. We can talk later."
            time.sleep(random.uniform(2, 3))

            reply_ts = datetime.now().strftime("%I:%M %p")
            with sqlite3.connect(DB) as conn:
                conn.execute(
                    "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                    (conv_id, reply, reply_ts)
                )
                conn.execute("UPDATE conversations SET updated_at=? WHERE id=?",
                             (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), conv_id))
                conn.commit()

            if not occupation:
                try: detect_and_save_occupation(conv_id)
                except Exception: pass

            with sqlite3.connect(DB) as conn:
                msg_count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conv_id,)
                ).fetchone()[0]

            analysis_result = None
            try: analysis_result = analyze_interest(conv_id)
            except Exception: pass

            return jsonify({"reply": reply, "timestamp": reply_ts, "analysis": analysis_result})

        except Exception as e:
            _log(f"[test-chat] Error: {e}")
            try:
                with sqlite3.connect(DB) as conn:
                    last = conn.execute(
                        "SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                        (conv_id,)
                    ).fetchone()
                    if last:
                        conn.execute("DELETE FROM messages WHERE id=?", (last[0],))
                    conn.commit()
            except Exception:
                pass
            return jsonify({"error": f"Alex is unavailable right now — {e}"}), 500


    # ── Fresh interest analysis ────────────────────────────────────────
    @app.route("/api/analyze/<conv_id>", methods=["POST"])
    def run_analysis(conv_id):
        with sqlite3.connect(DB) as conn:
            if not conn.execute("SELECT id FROM conversations WHERE id=?", (conv_id,)).fetchone():
                return jsonify({"error": "Conversation not found"}), 404

        result = analyze_interest(conv_id)
        if result is None:
            with sqlite3.connect(DB) as conn:
                info = conn.execute(
                    "SELECT interest_level, interest_score, interest_signals, recommendation FROM conversations WHERE id=?",
                    (conv_id,)
                ).fetchone()
            if info:
                return jsonify({
                    "level": info[0], "score": info[1],
                    "signals": json.loads(info[2]) if info[2] else [],
                    "recommendation": info[3] or "Send more messages to get an analysis."
                })
            return jsonify({"error": "Not enough messages to analyze yet"}), 400
        return jsonify(result)


    # ── Resend opening message locally ────────────────────────────
    @app.route("/api/resend-opening/<conv_id>", methods=["POST"])
    def resend_opening(conv_id):
        with sqlite3.connect(DB) as conn:
            row = conn.execute("SELECT phone_number FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not row:
                return jsonify({"error": "Conversation not found"}), 404
            phone = row[0]

        opening = get_opening_message()
        ts      = datetime.now().strftime("%I:%M %p")
        now_db  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(DB) as conn:
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                (conv_id, opening, ts)
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_db, conv_id))
            conn.commit()
        send_message(phone, opening)
        return jsonify({"ok": True, "message": opening, "timestamp": ts})


    # ── Operator sends manual message ──────────────────────────────────
    @app.route("/api/manual-send", methods=["POST"])
    def manual_send():
        data    = request.json
        conv_id = (data.get("conversation_id") or "").strip()
        message = (data.get("message")         or "").strip()
        if not conv_id or not message:
            return jsonify({"error": "conversation_id and message required"}), 400

        with sqlite3.connect(DB) as conn:
            row = conn.execute("SELECT phone_number FROM conversations WHERE id=?", (conv_id,)).fetchone()
            if not row:
                return jsonify({"error": "Conversation not found"}), 404
            phone  = row[0]
            ts     = datetime.now().strftime("%I:%M %p")
            now_db = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                (conv_id, message, ts)
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_db, conv_id))
            conn.commit()
        send_message(phone, message)
        return jsonify({"ok": True, "timestamp": ts})


    # ── Bulk CSV upload ────────────────────────────────────────────────
    @app.route("/api/upload-csv", methods=["POST"])
    def upload_csv():
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file uploaded"}), 400

        reader = csv.DictReader(io.StringIO(file.read().decode("utf-8", errors="ignore")))
        added, skipped = [], []

        for row in reader:
            phone = (row.get("phone_number") or row.get("phone") or "").strip()
            if not phone:
                continue
            name       = (row.get("name") or "").strip()
            occupation = (row.get("occupation") or row.get("job") or row.get("work") or "").strip()
            conv_id    = str(uuid.uuid4())
            now        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ts         = datetime.now().strftime("%I:%M %p")
            try:
                with sqlite3.connect(DB) as conn:
                    conn.execute(
                        "INSERT INTO conversations (id, phone_number, name, occupation, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (conv_id, phone, name, occupation, now, now)
                    )
                    opening = get_opening_message()
                    conn.execute(
                        "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'assistant', ?, ?)",
                        (conv_id, opening, ts)
                    )
                    conn.commit()
                added.append({"phone_number": phone, "name": name})
            except sqlite3.IntegrityError:
                skipped.append(phone)

        return jsonify({"added": len(added), "skipped": len(skipped), "details": added})


    # ── Inbound webhook — customer replies ────────────────────────────
    @app.route("/webhook", methods=["POST"])
    def webhook():
        data = request.get_json(silent=True) or {}
        from_number = (
            data.get("from")
            or data.get("phone_number")
            or request.form.get("From", "")
        ).replace("whatsapp:", "").strip()
        body = (data.get("body") or data.get("message") or request.form.get("Body", "")).strip()
        if not from_number or not body:
            return "", 200

        now_ts = datetime.now().strftime("%I:%M %p")
        now_db = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB) as conn:
            row = conn.execute(
                "SELECT id, chat_mode FROM conversations WHERE phone_number=?", (from_number,)
            ).fetchone()
            if row:
                conv_id, chat_mode = row[0], row[1] or "auto"
            else:
                conv_id, chat_mode = str(uuid.uuid4()), "auto"
                conn.execute(
                    "INSERT INTO conversations (id, phone_number, chat_mode, created_at, updated_at) VALUES (?,?,?,?,?)",
                    (conv_id, from_number, "auto", now_db, now_db)
                )
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, 'user', ?, ?)",
                (conv_id, body, now_ts)
            )
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now_db, conv_id))
            conn.commit()

        if chat_mode == "auto":
            schedule_reply(conv_id, from_number)
        else:
            _log(f"[Manual] Message from {from_number} saved — awaiting operator reply")
        return "", 200


    # ── Logs ──────────────────────────────────────────────────────────
    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        return jsonify({"logs": list(reversed(send_log))})


    # ── Health check ──────────────────────────────────────────────────
    @app.route("/api/diagnose", methods=["GET"])
    def diagnose():
        results = []

        wh = config.WEBHOOK_URL
        results.append({"check": "ngrok tunnel", "ok": bool(wh),
                         "detail": f"Running - {wh}" if wh else "Not running. Restart app.py to start the tunnel."})

        try:
            config.client.models.list()
            results.append({"check": "OpenRouter AI", "ok": True, "detail": "API key is valid."})
        except Exception as e:
            results.append({"check": "OpenRouter AI", "ok": False, "detail": str(e)})

        return jsonify({"ok": all(r["ok"] is not False for r in results), "checks": results})


    # ── Get current config ─────────────────────────────────────────────
    @app.route("/api/config", methods=["GET"])
    def get_config():
        with sqlite3.connect(DB) as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        cfg = {r[0]: r[1] for r in rows}
        return jsonify({
            "webhook_url":       config.WEBHOOK_URL,
            "ngrok_token_set":   bool(cfg.get("ngrok_token", "")),
        })


    # ── Save config ────────────────────────────────────────────────────
    @app.route("/api/config", methods=["POST"])
    def save_config():
        data     = request.json or {}
        ngrok_tk = (data.get("ngrok_token", "") or "").strip()

        with sqlite3.connect(DB) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", ("ngrok_token", ngrok_tk))
            conn.commit()

        if ngrok_tk:
            try:
                from pyngrok import conf as _nc
                _nc.get_default().auth_token = ngrok_tk
            except Exception:
                pass

        return jsonify({"ok": True, "message": "Settings saved."})


    # ── Test outbound preview ─────────────────────────────────────────
    @app.route("/api/test-send", methods=["POST"])
    def test_send():
        data  = request.json or {}
        phone = (data.get("phone", "") or "").strip()
        if not phone:
            return jsonify({"error": "Phone number required"}), 400
        send_message(phone, "Test from Alex CRM - local outbound preview.")
        return jsonify({"ok": True, "message": f"Local preview logged for {phone}"})
