"""
state.py — Shared data contract for the Real-Time Support Triager.

Every node in the LangGraph pipeline (Part 2), every CrewAI agent (Part 3),
and the FastAPI layer (Part 3) reads from and writes to this single
TicketState shape. Keeping it centralized here means nothing downstream
needs to guess at field names or types.

Fields are grouped by which stage of the pipeline populates them:
  - Intake (n8n / FastAPI):      ticket_id, message, channel, timestamp
  - Sentiment Node (LangGraph):  sentiment, sentiment_score
  - Feature Node (LangGraph):    issue_type, module, priority
  - Agent 1 - Context Liaison:   retrieved_solution
  - Agent 2 - Response Synth.:   response
"""

from typing import TypedDict, Literal

# Allowed values, kept here so Part 2/3 can import them instead of
# hardcoding strings (avoids typos like "Hgih" silently breaking routing).
Sentiment = Literal["positive", "neutral", "negative", "highly_negative"]
Priority = Literal["Low", "High", "Critical"]


class TicketState(TypedDict, total=False):
    # --- Intake fields (always present from the moment a ticket is created) ---
    ticket_id: str
    message: str
    channel: str          # "intercom" | "email" | "app_store"
    timestamp: str         # ISO 8601, e.g. "2024-01-01T10:00:00Z"

    # --- Sentiment Node output (added in parallel branch 1) ---
    sentiment: Sentiment
    sentiment_score: float  # -1.0 (very negative) to +1.0 (very positive)

    # --- Feature Node output (added in parallel branch 2) ---
    issue_type: str        # e.g. "login_failure", "crash", "payment_issue"
    module: str             # e.g. "authentication", "billing", "upload"
    priority: Priority

    # --- Agent 1: Context Liaison output (RAG retrieval) ---
    retrieved_solution: str

    # --- Agent 2: Response Synthesizer output ---
    response: str


def new_ticket(ticket_id: str, message: str, channel: str, timestamp: str) -> TicketState:
    """
    Convenience constructor for the intake stage. Only fills the fields
    known at intake time — everything else gets added as the ticket
    flows through the graph and agents.
    """
    return TicketState(
        ticket_id=ticket_id,
        message=message,
        channel=channel,
        timestamp=timestamp,
    )


if __name__ == "__main__":
    # Quick smoke test — not a real test suite, just confirms the shape works.
    t = new_ticket(
        ticket_id="T1001",
        message="Your update broke my login and I'm very angry.",
        channel="intercom",
        timestamp="2024-01-01T10:00:00Z",
    )
    print("New ticket created:")
    print(t)