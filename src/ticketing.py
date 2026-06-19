"""
src/ticketing.py — Mock Jira/Trello ticketing output (Part 4).

For every Critical-priority ticket that comes through the pipeline, this
module writes a structured JSON file to <project_root>/tickets/.

File naming:  tickets/<ticket_id>.json
JSON schema mirrors a minimal Jira issue so it's easy to swap this out for
a real Jira/Trello API call later.

Usage (called from api.py after run_crew):
    from ticketing import file_ticket
    file_ticket(final_ticket)   # no-op for non-Critical tickets
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from state import TicketState

# tickets/ folder lives at the project root (one level above src/)
TICKETS_DIR = Path(__file__).parent.parent / "tickets"


def file_ticket(ticket: TicketState) -> str | None:
    """
    Write a Critical ticket to TICKETS_DIR as a Jira-style JSON file.

    Returns the path to the written file, or None if the ticket is not
    Critical (non-Critical tickets are intentionally skipped — they're
    already in SQLite and don't need a separate ticket card).
    """
    if ticket.get("priority") != "Critical":
        return None

    TICKETS_DIR.mkdir(parents=True, exist_ok=True)

    ticket_id = ticket.get("ticket_id", "UNKNOWN")

    jira_issue = {
        "key":    ticket_id,
        "fields": {
            "summary":     _truncate(ticket.get("message", ""), 120),
            "description": {
                "customer_message":  ticket.get("message", ""),
                "retrieved_solution": ticket.get("retrieved_solution", ""),
                "drafted_response":  ticket.get("response", ""),
            },
            "issuetype": {
                "name": _to_jira_issuetype(ticket.get("issue_type", "")),
            },
            "priority": {
                "name": "Highest",              # Jira's top priority maps to Critical
            },
            "labels": [
                ticket.get("channel", "unknown"),
                ticket.get("module",  "unknown"),
                ticket.get("sentiment", "unknown"),
            ],
            "customfield_sentiment_score": ticket.get("sentiment_score"),
            "customfield_channel":         ticket.get("channel"),
            "customfield_module":          ticket.get("module"),
            "customfield_issue_type":      ticket.get("issue_type"),
            "status": {
                "name": "Open",
            },
            "assignee": None,               # unassigned until triaged by a human
            "reporter": {
                "name": "support-triager-bot",
            },
            "created": ticket.get(
                "timestamp",
                datetime.now(timezone.utc).isoformat(),
            ),
            "updated": datetime.now(timezone.utc).isoformat(),
        },
    }

    out_path = TICKETS_DIR / f"{ticket_id}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(jira_issue, f, indent=2, ensure_ascii=False)

    print(f"[Ticketing] Filed Critical ticket → {out_path}")
    return str(out_path)


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len - 1] + "…"


def _to_jira_issuetype(issue_type: str) -> str:
    """
    Map our snake_case issue_type taxonomy to Jira's built-in issue type names.
    Falls back to 'Bug' for anything unrecognised — safe default for most projects.
    """
    mapping = {
        "crash":             "Bug",
        "login_failure":     "Bug",
        "payment_issue":     "Bug",
        "billing_issue":     "Bug",
        "otp_issue":         "Bug",
        "settings_bug":      "Bug",
        "notification_issue": "Bug",
        "export_issue":      "Bug",
        "performance":       "Bug",
        "account_locked":    "Bug",
        "wrong_order":       "Task",
        "search_bug":        "Bug",
        "profile_issue":     "Bug",
        "general_inquiry":   "Task",
    }
    return mapping.get(issue_type, "Bug")


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from state import new_ticket

    test = new_ticket(
        ticket_id="T_CRIT_001",
        message="I was charged twice for my subscription and now I can't log in. Extremely urgent.",
        channel="intercom",
        timestamp="2026-06-20T10:00:00Z",
    )
    test["sentiment"]          = "highly_negative"
    test["sentiment_score"]    = -0.95
    test["issue_type"]         = "billing_issue"
    test["module"]             = "billing"
    test["priority"]           = "Critical"
    test["retrieved_solution"] = "Issue duplicate charge — refund via Stripe dashboard."
    test["response"]           = "We're very sorry for the double charge. We've initiated a full refund. The Support Team"

    path = file_ticket(test)
    print(f"Ticket filed at: {path}")

    # Non-critical should be silently skipped
    test_low = dict(test)
    test_low["ticket_id"] = "T_LOW_001"
    test_low["priority"]  = "Low"
    result = file_ticket(test_low)   # type: ignore[arg-type]
    assert result is None, "Low-priority ticket should not be filed"
    print("Non-Critical ticket correctly skipped.")