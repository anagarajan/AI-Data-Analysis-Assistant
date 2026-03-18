import time
from typing import Dict, List, Optional

_SESSION_TTL_S = 2 * 60 * 60    # 2 hours of inactivity
_MAX_HISTORY = 40                # max messages kept per session


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, dict] = {}

    def create_session(self, session_id: str, schema_info: dict):
        self._sessions[session_id] = {
            "schema": schema_info,
            "history": [],
            "last_accessed": time.time(),
        }

    def get_session(self, session_id: str) -> Optional[dict]:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session["last_accessed"] > _SESSION_TTL_S:
            self._sessions.pop(session_id, None)
            return None
        session["last_accessed"] = time.time()
        return session

    def add_message(self, session_id: str, role: str, content: str):
        session = self._sessions.get(session_id)
        if session is None:
            return
        # Trim content to avoid storing huge strings
        content = content[:8_000]
        session["history"].append({"role": role, "content": content})
        # Keep only the most recent messages
        if len(session["history"]) > _MAX_HISTORY:
            session["history"] = session["history"][-_MAX_HISTORY:]
        session["last_accessed"] = time.time()

    def get_history(self, session_id: str) -> List[dict]:
        session = self._sessions.get(session_id)
        return session["history"] if session else []

    def expire_session(self, session_id: str):
        self._sessions.pop(session_id, None)

    def cleanup_expired(self) -> int:
        """Remove expired sessions. Returns count removed."""
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if now - s["last_accessed"] > _SESSION_TTL_S
        ]
        for sid in expired:
            self._sessions.pop(sid, None)
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
