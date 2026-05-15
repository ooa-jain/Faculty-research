#!/bin/bash
# ── FDP Survey — Development Start Script ─────────────────────────────────────

echo "Starting FDP Survey in DEVELOPMENT mode..."

if [ -d "venv" ]; then
    source venv/bin/activate
fi

pip install -r requirements.txt --quiet

# Dev: single threaded but with debug
python app.py
