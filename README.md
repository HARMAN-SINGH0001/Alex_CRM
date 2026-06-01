# Alex Sales Chatbot CRM

A Flask-based AI sales assistant and CRM dashboard built to manage customer conversations, analyze interest, and send outbound messages.

## What it does

- Starts a local Flask dashboard at `http://127.0.0.1:5000`
- Creates and stores conversations in an SQLite database
- Sends outbound greeting messages and logs them locally
- Supports webhook-based inbound replies via `POST /webhook`
- Uses OpenRouter-compatible chat completion API for AI-driven analysis and reply logic
- Handles English, Hindi, and Punjabi language flows with sales-focused prompts

## Key features

- Conversation CRUD: create, list, view, delete
- Auto/manual chat mode for each conversation
- Local test chat endpoint for instant replies
- Bulk customer import from CSV
- Interest analysis and recommendation scoring
- In-memory log visible in the dashboard
- ngrok support for exposing webhook endpoint

## Project structure

- `app.py` — application entry point, starts Flask and ngrok
- `routes.py` — all Flask API routes and webhook handler
- `config.py` — OpenRouter client, SQLite DB init, logging, and message sender
- `agent.py` — AI analysis, language detection, reply scheduling and guardrails
- `prompts.py` — system prompt for Alex's sales personality and conversation rules
- `templates/index.html` — dashboard UI
- `wati_chat.db` — SQLite database file created automatically

## Requirements

- Python 3.10+ (recommended)
- `flask`
- `pyngrok`
- `openai`

## Installation

```bash
python -m venv venv
venv\Scripts\activate
pip install flask pyngrok openai
```

## Configuration

Set your OpenRouter API key as an environment variable before starting the app:

```bash
set OPENROUTER_API_KEY=your_openrouter_api_key
```

Or save the key in the app settings if you have a settings panel.

If you use ngrok, the app will attempt to start a tunnel automatically. Optionally, save `ngrok` auth token into the database from the settings panel in the dashboard.

## Run the app

```bash
python app.py
```

Then open:

```text
http://127.0.0.1:5000
```

If ngrok starts successfully, the webhook URL will also be printed in the terminal.

## Usage

- Add a new conversation with phone number, name, and occupation
- The app sends a local outbound greeting and logs it
- View conversation history and AI analysis
- Switch between `auto` and `manual` mode
- Send manual messages through `/api/manual-send`
- Use `/webhook` to receive inbound messages from providers

## Notes

- `send_message()` in `config.py` currently logs outbound messages locally only.
- Replace `send_message()` with a real SMS/WhatsApp provider integration for production use.
- The system prompt in `prompts.py` is tuned for a sales consultant persona named Alex.

## Recommended enhancements

- Add real message delivery via WhatsApp/SMS provider
- Improve authentication for dashboard and API endpoints
- Add a proper `requirements.txt` or `pyproject.toml`
- Add tests for route and agent logic

## License

Add your preferred license here.
