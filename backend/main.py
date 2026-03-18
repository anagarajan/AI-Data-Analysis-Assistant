import os
import time
import asyncio
from collections import deque
from typing import Dict

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
import uvicorn

from session_manager import SessionManager
from data_analyzer import DataAnalyzer
from ai_engine import AIEngine

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="AI Data Analysis Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

_sessions = SessionManager()
_analyzer = DataAnalyzer()
_ai = AIEngine()

# ---------------------------------------------------------------------------
# Rate limiter  (sliding-window, in-memory)
# ---------------------------------------------------------------------------

_MAX_QUERIES_PER_MIN = 20    # per session
_MAX_UPLOADS_PER_5MIN = 5    # per IP

class _RateLimiter:
    def __init__(self):
        self._windows: Dict[str, deque] = {}

    def is_allowed(self, key: str, max_calls: int, window_s: int) -> bool:
        now = time.time()
        dq = self._windows.setdefault(key, deque())
        while dq and dq[0] < now - window_s:
            dq.popleft()
        if len(dq) >= max_calls:
            return False
        dq.append(now)
        return True

    def cleanup(self):
        # Drop entries with no recent calls
        empty = [k for k, dq in self._windows.items() if not dq]
        for k in empty:
            del self._windows[k]

_limiter = _RateLimiter()

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")

# ---------------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def _start_cleanup():
    async def _loop():
        while True:
            await asyncio.sleep(15 * 60)  # every 15 minutes
            removed = _sessions.cleanup_expired()
            _limiter.cleanup()
            if removed:
                print(f"[cleanup] Removed {removed} expired session(s). Active: {_sessions.active_count}")
    asyncio.create_task(_loop())

# ---------------------------------------------------------------------------
# Global error handler  — never leak internal details
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    print(f"[ERROR] Unhandled: {type(exc).__name__}: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal error occurred. Please try again."},
    )

# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls"}
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB

@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    ip = _client_ip(request)
    if not _limiter.is_allowed(f"upload:{ip}", _MAX_UPLOADS_PER_5MIN, 5 * 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many uploads. Please wait a few minutes.")

    filename = (file.filename or "dataset").strip()
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only CSV and Excel files (.csv, .xlsx, .xls) are supported.")

    content = await file.read()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail=f"File too large (max {_MAX_FILE_BYTES // 1024 // 1024} MB).")
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        session_id, schema_info = _analyzer.load_dataset(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=422, detail="Could not parse the file. Ensure it is a valid CSV or Excel file.")

    _sessions.create_session(session_id, schema_info)

    try:
        summary = await _ai.generate_initial_summary(schema_info)
    except Exception:
        summary = f"Dataset loaded: {schema_info['rows']:,} rows × {schema_info['columns']} columns."

    _sessions.add_message(session_id, "assistant", summary)
    return {"session_id": session_id, "schema": schema_info, "summary": summary}


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    session_id: str
    query: str

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 36:
            raise ValueError("Invalid session_id.")
        return v

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Query cannot be empty.")
        if len(v) > 2_000:
            raise ValueError("Query is too long (max 2,000 characters).")
        return v


@app.post("/api/query")
async def process_query(req: QueryRequest):
    session = _sessions.get_session(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired. Please re-upload your file.")

    if not _limiter.is_allowed(f"query:{req.session_id}", _MAX_QUERIES_PER_MIN, 60):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail=f"Too many queries. Limit is {_MAX_QUERIES_PER_MIN} per minute.")

    _sessions.add_message(req.session_id, "user", req.query)
    history = _sessions.get_history(req.session_id)[:-1]

    df = _analyzer.get_dataframe(req.session_id)

    try:
        ai_resp = await _ai.process_query(
            query=req.query,
            schema_info=session["schema"],
            history=history,
            df=df,
        )
    except Exception:
        raise HTTPException(status_code=502, detail="AI service unavailable. Please try again.")

    result_data = None
    if ai_resp.get("code"):
        result_data = _analyzer.execute_code(
            code=ai_resp["code"],
            session_id=req.session_id,
        )

    _sessions.add_message(req.session_id, "assistant", ai_resp.get("explanation", ""))

    return {
        "explanation": ai_resp.get("explanation", ""),
        "result": result_data,
        "viz_type": ai_resp.get("visualization"),
    }


# ---------------------------------------------------------------------------
# Session info  (schema + history — no raw data)
# ---------------------------------------------------------------------------

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    if len(session_id) != 36:
        raise HTTPException(status_code=400, detail="Invalid session ID.")
    session = _sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return {"schema": session["schema"], "history": session["history"]}


# ---------------------------------------------------------------------------
# Serve frontend  (registered last so /api/ routes take priority)
# ---------------------------------------------------------------------------

_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")
app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
