import io
import math
import uuid
import concurrent.futures
from datetime import datetime, date
from typing import Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Safe builtins for code execution sandbox
# Explicitly allowlist only what pandas analysis code needs.
# __import__, open, eval, exec, compile, globals, locals, etc. are excluded.
# ---------------------------------------------------------------------------
_SAFE_BUILTINS: dict = {
    # constants
    "None": None, "True": True, "False": False,
    # type constructors
    "bool": bool, "int": int, "float": float, "str": str,
    "list": list, "dict": dict, "tuple": tuple, "set": set,
    # iteration / functional
    "range": range, "enumerate": enumerate, "zip": zip,
    "map": map, "filter": filter, "sorted": sorted, "reversed": reversed,
    # math / comparison
    "abs": abs, "round": round, "sum": sum, "min": min, "max": max,
    "divmod": divmod, "pow": pow,
    # inspection (read-only, no attribute setting)
    "len": len, "type": type, "isinstance": isinstance, "issubclass": issubclass,
    "repr": repr, "hash": hash,
    # string helpers
    "chr": chr, "ord": ord, "format": format,
    # exceptions needed by pandas/numpy internally
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "KeyError": KeyError, "IndexError": IndexError, "AttributeError": AttributeError,
    "StopIteration": StopIteration,
}

_MAX_ROWS = 500_000          # rows accepted on upload
_EXEC_TIMEOUT_S = 15         # seconds before code execution is killed
_RESULT_ROW_LIMIT = 500      # max rows returned in a result


def _serialize_value(val) -> Any:
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        v = float(val)
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(val, np.bool_):
        return bool(val)
    if isinstance(val, pd.Timestamp):
        return val.isoformat()
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, np.ndarray):
        return val.tolist()
    return val


class DataAnalyzer:
    def __init__(self):
        self._dataframes: Dict[str, pd.DataFrame] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="exec"
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_dataset(self, content: bytes, filename: str) -> Tuple[str, dict]:
        session_id = str(uuid.uuid4())
        name = filename.lower()

        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise ValueError(f"Unsupported file format: {filename}")

        if len(df) > _MAX_ROWS:
            raise ValueError(
                f"Dataset too large ({len(df):,} rows). Maximum is {_MAX_ROWS:,} rows."
            )

        df.columns = [str(c) for c in df.columns]
        self._dataframes[session_id] = df
        return session_id, self._get_schema_info(df, filename)

    def get_dataframe(self, session_id: str) -> Optional[pd.DataFrame]:
        return self._dataframes.get(session_id)

    def drop_session(self, session_id: str):
        """Free dataframe memory when a session expires."""
        self._dataframes.pop(session_id, None)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _get_schema_info(self, df: pd.DataFrame, filename: str) -> dict:
        columns = []
        for col in df.columns:
            info: dict = {
                "name": col,
                "type": str(df[col].dtype),
                "non_null_count": int(df[col].notna().sum()),
                "null_count": int(df[col].isna().sum()),
            }
            if pd.api.types.is_numeric_dtype(df[col]) and df[col].notna().any():
                info["min"] = _serialize_value(df[col].min())
                info["max"] = _serialize_value(df[col].max())
                info["mean"] = _serialize_value(df[col].mean())
            elif pd.api.types.is_string_dtype(df[col]) or df[col].dtype == object:
                info["unique_values"] = int(df[col].nunique())
                info["sample_values"] = [
                    str(v) for v in df[col].dropna().unique()[:5].tolist()
                ]
            columns.append(info)

        return {
            "filename": filename,
            "rows": len(df),
            "columns": len(df.columns),
            "column_info": columns,
            "sample_data": self._serialize_rows(df.head(5)),
        }

    # ------------------------------------------------------------------
    # Code execution  (sandboxed + timeout)
    # ------------------------------------------------------------------

    def execute_code(self, code: str, session_id: str) -> Optional[dict]:
        df = self._dataframes.get(session_id)
        if df is None:
            return {"type": "error", "message": "Session not found."}

        if len(code) > 4_000:
            return {"type": "error", "message": "Generated code is too long."}

        namespace: dict = {
            "__builtins__": _SAFE_BUILTINS,
            "df": df.copy(),
            "pd": pd,
            "np": np,
        }

        future = self._executor.submit(self._run_exec, code, namespace)
        try:
            result = future.result(timeout=_EXEC_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            future.cancel()
            return {"type": "error", "message": f"Query timed out ({_EXEC_TIMEOUT_S}s limit)."}
        except Exception as exc:
            # Return a sanitised message — no internal paths or tracebacks
            return {"type": "error", "message": self._safe_error(exc)}

        if result is None:
            return None

        return self._format_result(result)

    @staticmethod
    def _run_exec(code: str, namespace: dict):
        exec(compile(code, "<analyst>", "exec"), namespace)  # noqa: S102
        return namespace.get("result")

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        """Return a user-friendly, non-leaking error message."""
        msg = str(exc)
        # Strip file paths
        import re
        msg = re.sub(r'(/[^\s]+)+', '<path>', msg)
        return msg[:300]

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def _format_result(self, result) -> dict:
        if isinstance(result, pd.DataFrame):
            truncated = result.head(_RESULT_ROW_LIMIT)
            return {
                "type": "table",
                "columns": [str(c) for c in truncated.columns],
                "data": self._serialize_rows(truncated),
                "total_rows": len(result),
            }
        if isinstance(result, pd.Series):
            truncated = result.head(_RESULT_ROW_LIMIT)
            return {
                "type": "series",
                "name": str(result.name) if result.name is not None else "Value",
                "index": [_serialize_value(i) for i in truncated.index.tolist()],
                "values": [_serialize_value(v) for v in truncated.tolist()],
                "total_rows": len(result),
            }
        return {"type": "scalar", "value": str(result)[:1_000]}

    def _serialize_rows(self, df: pd.DataFrame) -> list:
        return [
            [_serialize_value(cell) for cell in row]
            for row in df.values.tolist()
        ]
