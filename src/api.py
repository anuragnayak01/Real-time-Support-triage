"""
api.py — FastAPI backend for the Real-Time Support Triager (Part 3 → 4).

Pipeline per request:
  1. graph.py   — sentiment_node + feature_node (parallel) -> db_node -> alert_node
  2. agents.py  — CrewAI RAG retrieval -> response drafting
  3. Re-upsert ticket into SQLite with retrieved_solution + response filled in
  4. ticketing.py — file a Jira-style JSON card for Critical tickets
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from state import new_ticket
from graph import build_graph
from agents import run_crew
from tools import Tool_Database_Upserter
from ticketing import file_ticket

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Real-Time Support Triager", version="0.2.0")

_graph = build_graph()  # compiled once at startup, reused across requests

# api.py lives in src/ → parent = src/ → parent.parent = project root
BASE_DIR       = Path(__file__).parent.parent
DB_PATH        = BASE_DIR / "tickets.db"
DASHBOARD_HTML = BASE_DIR / "dashboard" / "index.html"

# ── Schema ────────────────────────────────────────────────────────────────────

class TriageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Raw customer message")
    channel: str = Field(..., description="intercom | email | app_store")

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


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

    ticket_after_graph = _graph.invoke(ticket)
    final_ticket = run_crew(ticket_after_graph)
    Tool_Database_Upserter(final_ticket)

    filed_path = file_ticket(final_ticket)
    if filed_path:
        print(f"[api.py] Critical ticket card written → {filed_path}")

    return final_ticket


@app.get("/tickets/recent")
def recent_tickets(limit: int = 20):
    """Latest N tickets from SQLite — consumed by the dashboard."""
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM tickets ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serve the live Plotly dashboard HTML."""
    if not DASHBOARD_HTML.exists():
        raise HTTPException(
            status_code=404,
            detail="dashboard/index.html not found — commit it to the repo."
        )
    return DASHBOARD_HTML.read_text(encoding="utf-8")