#!/usr/bin/env bash
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$PROJECT_ROOT/backend"

# Ensure .env exists
if [ ! -f "$PROJECT_ROOT/.env" ]; then
  cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
  echo "Created .env — please add your ANTHROPIC_API_KEY to .env, then re-run."
  exit 1
fi

cd "$BACKEND"

# ── Prefer uv (fast) if available, otherwise fall back to pip ───────────
if command -v uv &>/dev/null; then
  echo "Using uv to install dependencies…"
  uv venv --python python3 .venv 2>/dev/null || true
  source .venv/bin/activate
  uv pip install -r requirements.txt --python .venv/bin/python3
else
  if [ ! -d ".venv" ]; then
    echo "Creating virtual environment…"
    python3 -m venv .venv
  fi
  source .venv/bin/activate
  # Skip upgrading pip to avoid hanging on network calls
  pip3 install -r requirements.txt
fi

echo ""
echo "✅  Starting AI Data Analysis Assistant → http://localhost:8000"
echo ""

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
