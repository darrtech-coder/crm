#!/bin/bash
set -e

echo "=== Setting up Python virtual environment ==="
if ! command -v python3 &> /dev/null; then
  echo "❌ Python3 not found. Please install Python 3.11+."
  exit 1
fi

if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
source venv/bin/activate

echo "=== Installing requirements ==="
python -m pip install --upgrade pip
pip install -r requirements.txt

echo "=== Checking Redis installation ==="
if ! command -v redis-server &> /dev/null; then
  echo "⚠️ Redis not found. Installing..."

  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &> /dev/null; then
      sudo apt-get update && sudo apt-get install -y redis-server
    elif command -v yum &> /dev/null; then
      sudo yum install -y redis
    else
      echo "❌ Could not install Redis automatically. Please install manually."
      exit 1
    fi
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v brew &> /dev/null; then
      brew install redis
    else
      echo "❌ Homebrew not found. Install Redis manually (brew install redis)."
      exit 1
    fi
  fi
fi

echo "=== Checking if Redis is running ==="
if pgrep redis-server > /dev/null; then
  echo "✅ Redis is already running."
else
  echo "▶️ Starting Redis..."
  redis-server --daemonize yes
  echo "✅ Redis started."
fi

echo "=== Running database migrations ==="
export FLASK_APP=wsgi.py
export FLASK_ENV=development

flask db init || echo "(migrations already initialized)"
flask db migrate -m "update schema"
flask db upgrade

echo "=== Starting Flask server ==="
flask run --reload