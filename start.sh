#!/usr/bin/env sh
# Start PDF RAG Agent on http://127.0.0.1:5000 (macOS / Linux).
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "First run: creating the virtual environment and installing packages..."
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env. Add your GROQ_API_KEY to it, then run ./start.sh again."
  exit 1
fi

echo "PDF RAG Agent is starting at http://127.0.0.1:5000 (Ctrl+C to stop)"
( sleep 5; (command -v open >/dev/null && open http://127.0.0.1:5000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:5000) || true ) &
exec .venv/bin/python wsgi.py
