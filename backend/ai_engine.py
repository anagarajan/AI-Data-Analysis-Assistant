import json
import re
from typing import List, Optional

import anthropic
import pandas as pd


_MODEL = "claude-opus-4-6"

_QUERY_SYSTEM_TEMPLATE = """You are an expert data analyst AI assistant. You help non-technical users explore datasets through natural language.

Dataset Information:
{schema_text}

Sample Data (first 5 rows):
{sample_data}

CRITICAL: Respond ONLY with a valid JSON object — no markdown, no code fences, no surrounding text.

Required JSON format:
{{
  "explanation": "Clear 2-4 sentence explanation of what you found. Include specific numbers and insights. Written for non-technical users.",
  "code": "Python pandas code. Use 'df' as dataframe, 'pd' as pandas, 'np' as numpy. Store final answer in variable named 'result'. Null if no computation needed.",
  "visualization": null or one of: "bar", "line", "pie", "scatter", "histogram", "box",
  "result_type": "table" or "scalar" or "series" or "text"
}}

Code rules:
- Always store the answer in 'result'
- No imports needed (pd and np are pre-imported)
- Keep code to 1-5 lines typically
- Examples:
  * result = df.groupby('Region')['Sales'].sum().sort_values(ascending=False)
  * result = df[df['Status'] == 'Active']['Revenue'].mean()
  * result = df.describe()

Visualization guide — use null when a table or number is clearer:
- "bar": comparing categories, top-N rankings
- "line": time-series trends
- "pie": proportions with fewer than 10 categories
- "histogram": value distributions
- "scatter": correlation between two numeric columns
- "box": statistical spread comparisons

If the question cannot be answered with data analysis (e.g. a general question), set code to null, visualization to null, and result_type to "text"."""


class AIEngine:
    def __init__(self):
        self._client = anthropic.AsyncAnthropic()

    # ------------------------------------------------------------------
    # Initial dataset summary (plain text, not JSON)
    # ------------------------------------------------------------------

    async def generate_initial_summary(self, schema_info: dict) -> str:
        schema_text = _format_schema(schema_info)

        response = await self._client.messages.create(
            model=_MODEL,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"I uploaded a dataset. Here is the schema:\n\n{schema_text}\n\n"
                        "Write a brief, friendly welcome message (3-5 sentences) that:\n"
                        "1. Describes what the dataset appears to be about\n"
                        "2. Mentions key stats (rows, columns)\n"
                        "3. Highlights 2-3 interesting columns\n"
                        "4. Suggests 2-3 example questions the user could ask\n\n"
                        "Keep it conversational and concise. Do NOT use bullet points."
                    ),
                }
            ],
        )
        return next(
            (b.text for b in response.content if b.type == "text"),
            "Dataset loaded! Feel free to ask questions about your data.",
        )

    # ------------------------------------------------------------------
    # Natural language query processing
    # ------------------------------------------------------------------

    async def process_query(
        self,
        query: str,
        schema_info: dict,
        history: List[dict],
        df: pd.DataFrame,
    ) -> dict:
        system_prompt = _QUERY_SYSTEM_TEMPLATE.format(
            schema_text=_format_schema(schema_info),
            sample_data=_format_sample(df),
        )

        # Keep last 16 messages (8 turns) to avoid token bloat
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in history[-16:]
        ]
        messages.append({"role": "user", "content": query})

        try:
            async with self._client.messages.stream(
                model=_MODEL,
                max_tokens=2048,
                system=system_prompt,
                messages=messages,
            ) as stream:
                final = await stream.get_final_message()

            text = next(
                (b.text for b in final.content if b.type == "text"), ""
            ).strip()

            return _parse_json_response(text)

        except Exception as exc:
            return {
                "explanation": f"I encountered an error: {exc}",
                "code": None,
                "visualization": None,
                "result_type": "text",
            }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _format_schema(schema_info: dict) -> str:
    lines = [
        f"File: {schema_info['filename']}",
        f"Rows: {schema_info['rows']:,}  |  Columns: {schema_info['columns']}",
        "",
        "Columns:",
    ]
    for col in schema_info["column_info"]:
        line = f"  • {col['name']} ({col['type']})"
        if col.get("min") is not None:
            line += f"  range [{col['min']:.2f} – {col['max']:.2f}], avg {col['mean']:.2f}"
        elif col.get("unique_values") is not None:
            samples = col.get("sample_values", [])[:3]
            sample_str = f"  e.g. {', '.join(str(s) for s in samples)}" if samples else ""
            line += f"  {col['unique_values']} unique values{sample_str}"
        if col["null_count"] > 0:
            line += f"  ({col['null_count']} nulls)"
        lines.append(line)
    return "\n".join(lines)


def _format_sample(df: pd.DataFrame) -> str:
    try:
        return df.head(5).to_string(index=False, max_cols=12)
    except Exception:
        return "(unavailable)"


def _parse_json_response(text: str) -> dict:
    """Extract and parse JSON from Claude's response robustly."""
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    # Find outermost JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    # Fallback: treat entire text as an explanation
    return {
        "explanation": text or "I could not process that query. Please try rephrasing.",
        "code": None,
        "visualization": None,
        "result_type": "text",
    }
