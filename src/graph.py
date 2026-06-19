"""
graph.py — LangGraph core engine for the Real-Time Support Triager (Part 2).

Flow:
              ┌─> sentiment_node ─┐
    START ────┤                   ├──> db_node ──> alert_node ──> END
              └─> feature_node ───┘

sentiment_node and feature_node both read only `message` from TicketState
and write disjoint keys (sentiment/sentiment_score vs issue_type/module/
priority), so LangGraph runs them as true parallel branches in the same
superstep and merges their outputs into one TicketState automatically —
no manual merge node needed.

db_node fans-in (only runs once BOTH parallel branches finish) and writes
the ticket to SQLite. alert_node runs after and fires a Slack alert only
if priority == "Critical".

retrieved_solution and response stay empty here — Part 3 (CrewAI agents)
fills those in and re-upserts the row.
"""

from langgraph.graph import StateGraph, START, END

from state import TicketState, new_ticket
from tools import (
    Tool_Sentiment_Analyzer,
    Tool_Feature_Extractor,
    Tool_Slack_Alert_Dispatcher,
    Tool_Database_Upserter,
)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def sentiment_node(state: TicketState) -> dict:
    result = Tool_Sentiment_Analyzer(state["message"])
    return {
        "sentiment": result.sentiment,
        "sentiment_score": result.sentiment_score,
    }


def feature_node(state: TicketState) -> dict:
    result = Tool_Feature_Extractor(state["message"])
    return {
        "issue_type": result.issue_type,
        "module": result.module,
        "priority": result.priority,
    }


def db_node(state: TicketState) -> dict:
    Tool_Database_Upserter(state)
    return {}


def alert_node(state: TicketState) -> dict:
    Tool_Slack_Alert_Dispatcher(state)
    return {}


# ---------------------------------------------------------------------------
# Build the graph
# ---------------------------------------------------------------------------

def build_graph():
    builder = StateGraph(TicketState)

    builder.add_node("sentiment_node", sentiment_node)
    builder.add_node("feature_node", feature_node)
    builder.add_node("db_node", db_node)
    builder.add_node("alert_node", alert_node)

    # Parallel fan-out from START
    builder.add_edge(START, "sentiment_node")
    builder.add_edge(START, "feature_node")

    # Fan-in: db_node only runs once both branches complete
    builder.add_edge("sentiment_node", "db_node")
    builder.add_edge("feature_node", "db_node")

    builder.add_edge("db_node", "alert_node")
    builder.add_edge("alert_node", END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Standalone test — matches the "done when" criterion from the build plan:
# 5-6 hardcoded sample messages, correct sentiment/priority, Slack fires
# only on Critical, rows land in SQLite.
# ---------------------------------------------------------------------------

SAMPLE_MESSAGES = [
    ("T2001", "intercom", "I LOVE the new update, everything works so smoothly now!"),
    ("T2002", "email", "My app keeps crashing every time I try to upload a file. Very annoying."),
    ("T2003", "app_store", "I was charged twice for my subscription and now I can't log in at all. This is unacceptable, fix it now."),
    ("T2004", "intercom", "Quick question — where do I change my notification settings?"),
    ("T2005", "email", "Payment was deducted but my order never went through. I need this resolved, it's urgent."),
    ("T2006", "app_store", "Dark mode keeps resetting every time I close the app, kind of annoying but not a big deal."),
]


if __name__ == "__main__":
    graph = build_graph()

    for ticket_id, channel, message in SAMPLE_MESSAGES:
        ticket = new_ticket(
            ticket_id=ticket_id,
            message=message,
            channel=channel,
            timestamp="2026-06-19T12:00:00Z",
        )
        final_state = graph.invoke(ticket)
        print(
            f"[{final_state['ticket_id']}] "
            f"sentiment={final_state['sentiment']} ({final_state['sentiment_score']:+.2f}) | "
            f"priority={final_state['priority']} | "
            f"issue_type={final_state['issue_type']} | "
            f"module={final_state['module']}"
        )

    print("\nDone. Check tickets.db (e.g. via DB Browser for SQLite) to confirm all 6 rows landed.")