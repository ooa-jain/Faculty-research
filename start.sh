#!/bin/bash
# ── FDP Survey — Production Start Script ──────────────────────────────────────

echo "Starting FDP Survey Application..."

# Create logs directory if it doesn't exist
mkdir -p logs

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "Virtual environment activated."
fi

# Install/update dependencies
pip install -r requirements.txt --quiet

# Start with Gunicorn
echo "Starting Gunicorn with $(python -c 'import multiprocessing; print(multiprocessing.cpu_count() * 2 + 1)') workers..."
gunicorn -c gunicorn.conf.py app:app

