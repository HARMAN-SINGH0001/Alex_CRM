# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Alex CRM** — a WhatsApp sales automation system with an AI agent named "Alex" that handles inbound customer conversations in English, Punjabi, and Hindi. Built with Flask + SQLite + OpenRouter LLM.

## Running the App

```powershell
python app.py
```

The server starts on port 5000. On boot it:
1. Initializes SQLite tables (`chat_history.db`)
2. Loads API keys/tokens from the database
3. Starts an ngrok tunnel to expose the webhook endpoint publicly
4. Begins accepting WhatsApp webhook events

There are no formal dependency files. Install dependencies manually:

```powershell
pip install flask openai pyngrok
```

## Architecture

The codebase is a single-file-per-concern Flask monolith:

| File | Role |
|---|---|
| `app.py` | Entry point — boot sequence, DB init, ngrok setup, Flask start |
| `config.py` | OpenRouter client, SQLite connection, logging, `send_message()` helper |
| `routes.py` | All 18+ Flask API endpoints (conversations, chat, webhook, CSV upload, settings) |
| `agent.py` | AI reply engine — language detection, guardrails, conversation analysis, LLM calls |
| `prompts.py` | System prompt defining Alex's persona, sales script, and pricing details |
| `templates/index.html` | Single-page dashboard — WhatsApp-like UI for operators |

## Database

SQLite file: `chat_history.db`

Three tables:
- **conversations** — one row per phone number; tracks `name`, `occupation`, `interest_level`, `interest_score`, `language_lock`, `chat_mode`
- **messages** — chat history; `role` is `user` or `assistant`
- **settings** — key/value store for `openrouter_api_key` and `ngrok_token`

Schema is defined and auto-created in `app.py:init_db()`.

## LLM Integration

Uses the OpenAI SDK pointed at OpenRouter (`https://openrouter.ai/api/v1`). The client is initialized in `config.py`. Model fallback chain in `agent.py`:

1. `openai/gpt-4o-mini` (primary)
2. `openai/gpt-3.5-turbo`
3. `meta-llama/llama-3.3-70b-instruct` (free tier)

The API key is currently hardcoded in `config.py` — it can also be updated at runtime via the `/api/settings` endpoint and is persisted to the `settings` table.

## Key Agent Behaviors

- **Message batching**: Inbound messages are debounced with a 3-second window per conversation using per-conversation threading locks — prevents duplicate replies to rapid multi-message sends.
- **Guardrails** (`agent.py`): Deterministic pattern-matching intercepts medical questions, off-topic queries, abusive messages, and disengagement signals before they reach the LLM.
- **Language detection**: Conversation language (English/Punjabi/Hindi) is auto-detected and locked per conversation via `language_lock` column.
- **Chat modes**: Conversations can be in `bot` (AI-managed) or `manual` (operator-managed) mode. The dashboard can toggle this.

## API Endpoints (routes.py)

Key endpoints:
- `POST /webhook` — receives inbound WhatsApp messages
- `GET /api/conversations` — list all conversations with metadata
- `GET /api/messages/<phone>` — fetch message history
- `POST /api/send` — operator manually sends a message
- `POST /api/upload-csv` — bulk import contacts
- `GET/POST /api/settings` — read/update ngrok token and API key
- `POST /api/analyze/<phone>` — trigger AI interest analysis for a conversation
