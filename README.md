# AI Data Analysis Assistant

An AI-powered data analysis tool that lets you explore datasets using plain English — no SQL, no coding required.

![Python](https://img.shields.io/badge/Python-3.10+-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green) ![Claude](https://img.shields.io/badge/AI-Claude%20Opus%204.6-purple)

## Features

- **Upload** CSV or Excel files (up to 50 MB, 500K rows)
- **Ask questions** in plain English — "What are the top 10 countries by revenue?"
- **Get instant results** as tables, numbers, or interactive charts (bar, line, pie, scatter, histogram, box)
- **Conversational** — follow-up questions maintain context from previous answers

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com)

## Setup

```bash
# 1. Clone or download the project
cd AI-Data-Analysis-Assistant

# 2. Add your API key
cp .env.example .env
# Edit .env and set: ANTHROPIC_API_KEY=your_key_here

# 3. Start the app
./start.sh
```

Open **http://localhost:8000** in your browser.

> The first run installs dependencies automatically (~1 minute). Subsequent starts are instant.

## Usage

1. **Upload** a CSV or Excel file using the sidebar
2. **Read** the AI-generated summary of your dataset
3. **Ask** any question about your data, for example:
   - "Show me total sales by region as a bar chart"
   - "What is the average order value per month?"
   - "Which product category has the highest return rate?"
   - "Show me the distribution of customer ages"
4. **Follow up** — ask drill-down questions based on previous answers

## Supported File Formats

| Format | Extension |
|--------|-----------|
| CSV | `.csv` |
| Excel | `.xlsx`, `.xls` |

## Project Structure

```
AI-Data-Analysis-Assistant/
├── backend/
│   ├── main.py            # FastAPI app and API routes
│   ├── ai_engine.py       # Claude API integration
│   ├── data_analyzer.py   # pandas data processing and sandboxed code execution
│   ├── session_manager.py # In-memory session management
│   └── requirements.txt
├── frontend/
│   └── index.html         # Single-page UI
├── .env.example
├── start.sh
└── README.md
```

## Limitations

- Sessions are stored in memory and expire after **2 hours** of inactivity — re-upload your file to start a new session
- Restarting the server clears all sessions
- Maximum **20 queries per minute** per session
- Maximum **5 file uploads per 5 minutes** per IP

## Troubleshooting

**"Session not found"** — The server was restarted or the session expired. Refresh the page and re-upload your file.

**"Credit balance too low"** — Add credits at [console.anthropic.com](https://console.anthropic.com) → Plans & Billing.

**Upload fails** — Ensure the file is a valid CSV or Excel format and under 50 MB.
