"""
api.py — FastAPI backend for the Real-Time Support Triager (Part 3 → 4).

Single endpoint: POST /triage
  Input:  {"message": str, "channel": str}
  Output: full TicketState JSON — sentiment, priority, retrieved_solution,
          response, etc.

Pipeline per request:
  1. graph.py   — sentiment_node + feature_node (parallel) -> db_node
                   (initial SQLite write) -> alert_node (Slack if Critical)
  2. agents.py  — CrewAI Context Liaison (RAG retrieval) -> Response
                   Synthesizer (drafts reply)
  3. Re-upsert the ticket into SQLite now that retrieved_solution and
     response are filled in.
  4. ticketing.py — file a Jira-style JSON card for Critical tickets.

Part 4 (n8n) calls this endpoint via HTTP Request node.
"""

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from state import new_ticket
from graph import build_graph
from agents import run_crew
from tools import Tool_Database_Upserter
from ticketing import file_ticket         # ← Part 4 addition

app = FastAPI(title="Real-Time Support Triager", version="0.2.0")

_graph = build_graph()  # compiled once at startup, reused across requests


class TriageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Raw customer message")
    channel: str = Field(..., description="intercom | email | app_store")


@app.post("/triage")
def triage(req: TriageRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message cannot be empty")

    ticket_id = f"T{uuid.uuid4().hex[:8]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    ticket = new_ticket(
        ticket_id=ticket_id,
        message=req.message,
        channel=req.channel,
        timestamp=timestamp,
    )

    # Stage 1: sentiment + feature extraction, initial DB write, Slack alert
    ticket_after_graph = _graph.invoke(ticket)

    # Stage 2: RAG retrieval + response drafting
    final_ticket = run_crew(ticket_after_graph)

    # Stage 3: Re-upsert now that retrieved_solution + response are filled in
    Tool_Database_Upserter(final_ticket)

    # Stage 4: File a Jira-style JSON card for Critical tickets (Part 4)
    filed_path = file_ticket(final_ticket)
    if filed_path:
        print(f"[api.py] Critical ticket card written → {filed_path}")

    return final_ticket


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tickets/recent")
def recent_tickets(limit: int = 20):
    """
    Convenience endpoint so the n8n dashboard node (or Streamlit) can pull
    the latest N tickets without going straight to SQLite.
    """
    import sqlite3
    from pathlib import Path

    db_path = Path(__file__).parent.parent / "tickets.db"
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tickets ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
from fastapi.responses import HTMLResponse

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    with open(Path(__file__).parent.parent / "dashboard" / "index.html") as f:
        return f.read()