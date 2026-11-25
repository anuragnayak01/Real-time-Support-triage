"""
api.py — FastAPI backend for the Real-Time Support Triager.

Pipeline per request:
  1. graph.py   — sentiment_node + feature_node (parallel) -> db_node -> alert_node
  2. agents.py  — CrewAI RAG retrieval -> response drafting
  3. Re-upsert ticket into SQLite with retrieved_solution + response filled in
  4. ticketing.py — file a Jira-style JSON card for Critical tickets

Portal layer (portal.py): admin/customer login, complaint portal, support-inbox poller.
"""

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

# api.py lives in src/ → parent = src/ → parent.parent = project root
BASE_DIR       = Path(__file__).parent.parent
DB_PATH        = BASE_DIR / "tickets.db"
DASHBOARD_HTML = BASE_DIR / "dashboard" / "index.html"
os.environ.setdefault("TICKETS_DB", str(DB_PATH))  # portal tables live in the same DB file

from state import new_ticket
from graph import build_graph
from agents import run_crew
from tools import Tool_Database_Upserter
from ticketing import file_ticket
import portal

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Real-Time Support Triager", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_graph = build_graph()  # compiled once at startup, reused across requests


def _run_pipeline(text: str, source: str) -> dict:
    """Full AI pipeline for portal/email tickets; maps the result to portal fields."""
    t = new_ticket(
        ticket_id=f"T{uuid.uuid4().hex[:8]}",
        message=text,
        channel="email" if source == "email" else "intercom",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    t = run_crew(_graph.invoke(t))
    Tool_Database_Upserter(t)
    file_ticket(t)  # Jira-style card for Critical tickets

    get = lambda *ks: next((t[k] for k in ks if isinstance(t, dict) and t.get(k) not in (None, "")), None)
    # >>> If a field comes out wrong, check the key names in state.py and adjust these lists <<<
    sent = get("sentiment_score", "sentiment")
    if isinstance(sent, str):
        sent = {"negative": -0.7, "positive": 0.6}.get(sent.lower(), 0.0)
    pr = str(get("priority", "severity", "urgency") or "normal").lower()
    return dict(
        sentiment=round(float(sent or 0), 2),
        category=str(get("category", "issue_type", "feature", "features") or "General")[:40],
        priority=pr if pr in ("critical", "high") else "normal",
        reply=str(get("response", "draft_response", "reply") or "Thanks for contacting us, we are on it."),
    )


portal.set_analyzer(_run_pipeline)
portal.init_db()
app.include_router(portal.router)
portal.start_email_poller()

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
