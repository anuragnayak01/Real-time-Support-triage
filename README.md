# Real-Time Support Triager

AI pipeline that classifies customer support messages, retrieves similar past solutions, and drafts personalized replies — deployed on Render.

**Live:** [insightpulse-2vvm.onrender.com/docs](https://insightpulse-2vvm.onrender.com/docs) · **Dashboard:** [/dashboard](https://insightpulse-2vvm.onrender.com/dashboard)

## Stack

LangGraph · CrewAI · FastAPI · ChromaDB · Groq LLaMA-3.3-70B · SQLite · Plotly

## How It Works

1. Message comes in via `POST /triage` (intercom / email / app_store)
2. LangGraph runs sentiment analysis + feature extraction **in parallel**
3. CrewAI Agent 1 searches 25 historical tickets via vector similarity
4. CrewAI Agent 2 drafts a personalized customer reply
5. Ticket saved to SQLite · Critical tickets → Slack alert + Jira-style JSON card

## Setup

```bash
pip install -r requirements.txt
echo GROQ_API_KEY=gsk_... > .env
python src/knowledge_base.py   # build RAG index once
cd src && uvicorn api:app --reload
```

Open `http://localhost:8000/docs` to test.

## Deploy

Render → Web Service → connect repo

| Field | Value |
|---|---|
| Build | `poetry run pip install -r requirements.txt && poetry run python src/knowledge_base.py` |
| Start | `cd src && uvicorn api:app --host 0.0.0.0 --port $PORT` |
| Env vars | `GROQ_API_KEY`, `PYTHON_VERSION=3.11.9` |

## Author

Anurag Nayak · [github.com/anuragnayak01](https://github.com/anuragnayak01)
