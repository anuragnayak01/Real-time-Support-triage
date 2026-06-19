"""
tools.py — Reusable tool functions for the Real-Time Support Triager (Part 2).

These are plain Python functions, not yet wrapped as CrewAI Tool objects
(that wrapping happens in Part 3 when Agent 1 / Agent 2 need to call them).
For now, src/graph.py imports and calls them directly inside LangGraph nodes.

Three tools:
  1. Tool_Sentiment_Analyzer  — LLM call -> sentiment label + score (-1.0 to 1.0)
  2. Tool_Slack_Alert_Dispatcher — posts to Slack webhook, only fires for Critical
  3. Tool_Database_Upserter   — writes/updates a ticket row in SQLite

Sentiment + feature extraction both use Groq (fast, free-tier, OpenAI-compatible)
via langchain_groq, with structured output so we get back clean typed objects
instead of parsing free-text LLM responses.
"""

import os
import sqlite3
from typing import Literal

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq

from state import TicketState

load_dotenv()  # reads .env in the project root (GROQ_API_KEY, SLACK_WEBHOOK_URL, etc.)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_MODEL = "llama-3.3-70b-versatile"
DB_PATH = "tickets.db"
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

_llm = None  # lazy singleton, same pattern as knowledge_base.py's _embed_model


def get_llm() -> ChatGroq:
    global _llm
    if _llm is None:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GROQ_API_KEY not set. Set it before running graph.py, e.g.\n"
                "  PowerShell: $env:GROQ_API_KEY='your-key-here'\n"
                "  bash:       export GROQ_API_KEY='your-key-here'"
            )
        _llm = ChatGroq(model=GROQ_MODEL, temperature=0)
    return _llm


# ---------------------------------------------------------------------------
# Structured output schemas — these are what the LLM is forced to return,
# so sentiment/feature nodes in graph.py get typed objects, not raw strings.
# ---------------------------------------------------------------------------

class SentimentResult(BaseModel):
    sentiment: Literal["positive", "neutral", "negative", "highly_negative"] = Field(
        description="Overall emotional tone of the customer message"
    )
    sentiment_score: float = Field(
        description="Emotional intensity from -1.0 (extremely negative/angry) "
        "to +1.0 (extremely positive/happy). 0.0 is neutral."
    )


KNOWN_ISSUE_TYPES = [
    "login_failure",       # can't log in / authentication broken
    "crash",               # app crashes or freezes
    "payment_issue",       # charged but order/transaction didn't go through, refund needed
    "billing_issue",       # subscription/recurring-charge problems (auto-renew, cancel didn't save)
    "otp_issue",           # OTP / verification code not arriving
    "settings_bug",        # a setting doesn't save or behaves incorrectly (NOT a how-to question)
    "notification_issue",  # push/notification delivery broken
    "export_issue",        # exporting/downloading data fails
    "performance",         # slow loading, lag
    "account_locked",      # locked out after failed attempts / security lockout
    "wrong_order",         # received the WRONG physical item / package (fulfillment only)
    "search_bug",          # search or filters broken
    "profile_issue",       # profile photo/info won't update
    "general_inquiry",     # a question with no actual bug/problem — "how do I...", "where is..."
]


class FeatureResult(BaseModel):
    issue_type: str = Field(
        description="Pick the closest fit from this exact taxonomy: "
        + ", ".join(KNOWN_ISSUE_TYPES)
        + ". Only invent a new snake_case category if truly none of these fit. "
        "IMPORTANT: if the message is a question or how-to request with no "
        "actual bug being reported (e.g. 'where do I change X', 'how does Y work'), "
        "use 'general_inquiry' — do not pick a *_bug category just because the "
        "topic involves a feature."
    )
    module: str = Field(
        description="Which product area this belongs to, e.g. 'authentication', "
        "'billing', 'upload', 'ui', 'settings', 'data', 'network', "
        "'fulfillment', 'search', 'profile'."
    )
    priority: Literal["Low", "High", "Critical"] = Field(
        description="Critical = money/security/total access loss (payment "
        "failures, account lockout, data loss, can't log in at all). "
        "High = a feature is broken and blocking the user but there's a "
        "workaround. Low = cosmetic, minor annoyance, or a question."
    )


# ---------------------------------------------------------------------------
# Tool 1: Sentiment Analyzer
# ---------------------------------------------------------------------------

def Tool_Sentiment_Analyzer(message: str) -> SentimentResult:
    """
    Scores the emotional tone/intensity of a raw customer message.
    Called by sentiment_node in graph.py.
    """
    llm = get_llm().with_structured_output(SentimentResult)
    prompt = (
        "You are a customer support sentiment classifier. Read the message "
        "and score its emotional tone precisely. Angry/frustrated language, "
        "ALL CAPS, exclamation marks, and words like 'broken', 'furious', "
        "'unacceptable' push toward highly_negative. A calm question or "
        "neutral bug report is 'neutral', not negative.\n\n"
        f"Message: {message}"
    )
    return llm.invoke(prompt)


# ---------------------------------------------------------------------------
# Tool 2: Feature Extractor (issue_type, module, priority)
# Not in the original 3-tool list from architecture.md, but graph.py's
# feature_node needs somewhere to put this logic — kept here alongside the
# other LLM-backed tool so graph.py stays thin and import-only.
# ---------------------------------------------------------------------------

def Tool_Feature_Extractor(message: str) -> FeatureResult:
    """
    Extracts issue_type, module, and priority from a raw customer message.
    Called by feature_node in graph.py.
    """
    llm = get_llm().with_structured_output(FeatureResult)
    prompt = (
        "You are a customer support ticket classifier. Read the message and "
        "extract the issue type, the product module it relates to, and the "
        "priority level. Be careful to distinguish an actual reported bug "
        "from a simple question or how-to request — questions with no "
        "broken behavior described should be 'general_inquiry', never a "
        "'*_bug' category.\n\n"
        f"Message: {message}"
    )
    return llm.invoke(prompt)


# ---------------------------------------------------------------------------
# Tool 3: Slack Alert Dispatcher
# ---------------------------------------------------------------------------

def Tool_Slack_Alert_Dispatcher(ticket: TicketState) -> bool:
    """
    Posts an alert to Slack ONLY if ticket['priority'] == 'Critical'.
    Returns True if an alert was sent (or would have been, in no-webhook
    fallback mode), False if skipped because priority wasn't Critical.

    If SLACK_WEBHOOK_URL isn't set, falls back to printing to console so
    Part 2 can be tested end-to-end before Slack is actually configured.
    """
    if ticket.get("priority") != "Critical":
        return False

    text = (
        f":rotating_light: *Critical ticket* `{ticket.get('ticket_id')}`\n"
        f"*Channel:* {ticket.get('channel')}  *Module:* {ticket.get('module')}  "
        f"*Issue:* {ticket.get('issue_type')}\n"
        f"*Sentiment:* {ticket.get('sentiment')} ({ticket.get('sentiment_score')})\n"
        f"*Message:* {ticket.get('message')}"
    )

    if not SLACK_WEBHOOK_URL:
        print(f"[Slack fallback — no SLACK_WEBHOOK_URL set]\n{text}\n")
        return True

    resp = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    resp.raise_for_status()
    return True


# ---------------------------------------------------------------------------
# Tool 4: Database Upserter
# ---------------------------------------------------------------------------

def _init_db(db_path: str = DB_PATH) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id           TEXT PRIMARY KEY,
            message             TEXT,
            channel             TEXT,
            timestamp           TEXT,
            sentiment           TEXT,
            sentiment_score     REAL,
            issue_type          TEXT,
            module              TEXT,
            priority            TEXT,
            retrieved_solution  TEXT,
            response            TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def Tool_Database_Upserter(ticket: TicketState, db_path: str = DB_PATH) -> None:
    """
    Writes (or overwrites, by ticket_id) one ticket row into SQLite.
    Fields not yet populated (retrieved_solution, response — added in
    Part 3) are written as NULL.
    """
    _init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO tickets (
            ticket_id, message, channel, timestamp,
            sentiment, sentiment_score, issue_type, module, priority,
            retrieved_solution, response
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticket_id) DO UPDATE SET
            message=excluded.message,
            channel=excluded.channel,
            timestamp=excluded.timestamp,
            sentiment=excluded.sentiment,
            sentiment_score=excluded.sentiment_score,
            issue_type=excluded.issue_type,
            module=excluded.module,
            priority=excluded.priority,
            retrieved_solution=excluded.retrieved_solution,
            response=excluded.response
        """,
        (
            ticket.get("ticket_id"),
            ticket.get("message"),
            ticket.get("channel"),
            ticket.get("timestamp"),
            ticket.get("sentiment"),
            ticket.get("sentiment_score"),
            ticket.get("issue_type"),
            ticket.get("module"),
            ticket.get("priority"),
            ticket.get("retrieved_solution"),
            ticket.get("response"),
        ),
    )
    conn.commit()
    conn.close()