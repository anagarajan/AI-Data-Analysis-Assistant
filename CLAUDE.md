# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Setup & Running

```bash
# First time: copy and fill in your API key
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY

# Start the app (creates venv, installs deps, starts server)
./start.sh
```

App runs at **http://localhost:8000**

Manual startup (if venv already set up):
```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload
```

Install/update dependencies:
```bash
cd backend && pip3 install -r requirements.txt
```

## Architecture

**Stack:** Python (FastAPI) backend + single-page HTML/JS frontend. No build step.

```
AI-Data-Analysis-Assistant/
├── backend/
│   ├── main.py             # FastAPI app, all API routes, rate limiting
│   ├── session_manager.py  # In-memory session store with TTL expiry
│   ├── data_analyzer.py    # pandas loading, sandboxed code execution
│   ├── ai_engine.py        # Anthropic SDK — claude-opus-4-6
│   └── requirements.txt
├── frontend/
│   └── index.html          # Single-page UI (vanilla JS, Chart.js via CDN, no build)
├── .env                    # ANTHROPIC_API_KEY (gitignored)
└── start.sh                # Bootstrap script (uses uv if available, else pip3)
```

### Request Flow

1. **Upload** (`POST /api/upload`): CSV/Excel → pandas DataFrame stored in memory, schema extracted → Claude generates a plain-text welcome summary
2. **Query** (`POST /api/query`): natural language → Claude returns JSON `{explanation, code, visualization, result_type}` → backend `exec()`s the pandas code on the stored DataFrame → response returned to frontend → **frontend renders charts using Chart.js**

### Key Design Decisions

- **Claude response format:** For queries, Claude is instructed via system prompt to return *only* a JSON object (no markdown fences). `ai_engine._parse_json_response()` handles extraction robustly (strips fences, finds outermost `{}`).
- **Code execution sandbox:** `data_analyzer.execute_code()` runs Claude-generated pandas code in a restricted namespace. `__builtins__` is replaced with `_SAFE_BUILTINS` — an explicit allowlist of ~30 safe builtins that excludes `__import__`, `open`, `eval`, `exec`, `compile`, `globals`, `locals`. The generated code receives `{df, pd, np}` and must store its output in a variable named `result`.
- **Execution timeout:** Code runs in a `ThreadPoolExecutor` thread with a 15-second timeout (`future.result(timeout=15)`). Timed-out futures are cancelled and an error dict is returned.
- **Chart rendering:** Matplotlib is **not used** (broken on Python 3.14 — infinite recursion). Charts are rendered entirely on the frontend using **Chart.js 4.4.0** (CDN). The backend returns `viz_type` (a string like `"bar"`) and the frontend's `renderChart()` builds the Chart.js config from `result` data.
- **Session state:** All state is in-memory. Sessions expire after 2 hours of inactivity (`_SESSION_TTL_S`). `SessionManager.cleanup_expired()` runs every 15 minutes via a background `asyncio` task. `DataAnalyzer.drop_session()` frees DataFrame memory on expiry.
- **History cap:** Each session stores up to 40 messages (`_MAX_HISTORY`); content trimmed to 8,000 chars per message.
- **Static file serving:** `StaticFiles` mounts *after* all `/api/...` routes to avoid the catch-all intercepting API calls.
- **Streaming:** `ai_engine.py` uses `async with self._client.messages.stream(...) as stream` + `await stream.get_final_message()`. The `thinking` parameter is **not used** — SDK 0.42.0 does not support it on `.stream()`.

### Rate Limiting

Sliding-window in-memory rate limiter (`_RateLimiter` in `main.py`):
- **Queries:** 20 per minute per session
- **Uploads:** 5 per 5 minutes per IP

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload CSV/Excel, returns `{session_id, schema, summary}` |
| POST | `/api/query` | NL query, returns `{explanation, result, viz_type}` |
| GET | `/api/session/{id}` | Get session schema + history |

### Claude Prompt Contract

`ai_engine.py` sends a system prompt containing the dataset schema and 5 sample rows, then expects Claude to return:

```json
{
  "explanation": "...",
  "code": "result = df.groupby('Region')['Sales'].sum()",
  "visualization": "bar",
  "result_type": "table"
}
```

`code` and `visualization` may be `null` for purely conversational answers. The `result` variable in the generated code must be a `pd.DataFrame`, `pd.Series`, or scalar.

### Frontend Chart Types

Chart.js type mapping from `viz_type`: `bar→bar`, `line→line`, `pie→pie`, `scatter→scatter`, `histogram→bar`, `box→bar`. For `series` results: index becomes labels, values become the dataset. For `table` results: first column becomes labels, each numeric column becomes a dataset.
