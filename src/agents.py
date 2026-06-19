"""
agents.py — CrewAI agents for the Real-Time Support Triager (Part 3).

Two agents, run sequentially:
  Agent 1 (Context Liaison)      — searches historical tickets (Part 1's RAG
                                    index) for the closest matching solution.
  Agent 2 (Response Synthesizer) — drafts a personalized customer reply using
                                    the retrieved solution + the ticket's
                                    sentiment/priority (from Part 2's graph).

Exposes run_crew(ticket: TicketState) -> TicketState with retrieved_solution
and response filled in. Called by api.py's /triage endpoint.
"""

import os

from crewai import Agent, Task, Crew, Process, LLM
from crewai.tools import tool

from knowledge_base import retrieve_similar_tickets
from state import TicketState, new_ticket

GROQ_MODEL = "groq/llama-3.3-70b-versatile"  # CrewAI LLM format: "provider/model"


def get_crew_llm() -> LLM:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "GROQ_API_KEY not set. Make sure .env exists in the project root "
            "(see tools.py) — agents.py relies on the same key."
        )
    return LLM(model=GROQ_MODEL, temperature=0.3, api_key=api_key)


@tool("Historical Ticket Search")
def search_historical_tickets(query: str) -> str:
    """Searches historical support tickets for the closest matching past
    problem and its solution. Input should be the customer's problem
    description in plain text."""
    results = retrieve_similar_tickets(query, top_k=3)
    if not results:
        return "No similar historical tickets found."
    lines = [
        f"- [{r['similarity']:.2f}] Problem: {r['problem']} | "
        f"Solution: {r['solution']} | issue_type: {r['issue_type']} | "
        f"module: {r['module']}"
        for r in results
    ]
    return "\n".join(lines)


def build_crew(ticket: TicketState) -> Crew:
    llm = get_crew_llm()

    context_liaison = Agent(
        role="Context Liaison",
        goal="Find the most relevant historical solution for the customer's reported problem.",
        backstory=(
            "A senior support engineer with perfect recall of every ticket the "
            "team has ever resolved. Always searches before answering instead "
            "of guessing."
        ),
        tools=[search_historical_tickets],
        llm=llm,
        verbose=False,
    )

    response_synthesizer = Agent(
        role="Response Synthesizer",
        goal=(
            "Draft a clear, empathetic, personalized reply to the customer "
            "using the retrieved historical solution."
        ),
        backstory=(
            "A customer-facing writer who turns technical fixes into warm, "
            "concise replies. Never sounds like a form letter, never invents "
            "facts that weren't in the retrieved solution."
        ),
        llm=llm,
        verbose=False,
    )

    search_task = Task(
        description=(
            f'The customer reported: "{ticket["message"]}"\n'
            f"Classified as issue_type={ticket.get('issue_type')}, "
            f"module={ticket.get('module')}, priority={ticket.get('priority')}.\n\n"
            "Search historical tickets for the closest matching problem and "
            "return the single best solution as plain text (one or two "
            "sentences). If nothing matches well (low similarity), say so "
            "plainly instead of forcing a weak match."
        ),
        expected_output="The single best-fit historical solution, as plain text.",
        agent=context_liaison,
    )

    response_task = Task(
        description=(
            f"The customer's sentiment is {ticket.get('sentiment')} "
            f"(score {ticket.get('sentiment_score')}) and priority is "
            f"{ticket.get('priority')}.\n"
            f'Original message: "{ticket["message"]}"\n\n'
            "Using the retrieved historical solution from the previous task, "
            "write a short, personalized reply to the customer (3-5 "
            "sentences). Acknowledge their issue, match tone to their "
            "sentiment (more apologetic/urgent for negative sentiment or "
            "High/Critical priority), and give the concrete fix. Do not "
            "invent facts that weren't in the retrieved solution. Sign off "
            "as 'The Support Team'."
        ),
        expected_output="A customer-facing reply, 3-5 sentences, plain text, ready to send.",
        agent=response_synthesizer,
        context=[search_task],
    )

    return Crew(
        agents=[context_liaison, response_synthesizer],
        tasks=[search_task, response_task],
        process=Process.sequential,
        verbose=False,
    )


def run_crew(ticket: TicketState) -> TicketState:
    """
    Runs the 2-agent crew against a ticket that has already been through
    graph.py (i.e. sentiment/issue_type/module/priority are populated).
    Returns the ticket with retrieved_solution and response filled in.
    """
    crew = build_crew(ticket)
    result = crew.kickoff()

    task_outputs = getattr(result, "tasks_output", [])
    retrieved_solution = str(task_outputs[0].raw) if len(task_outputs) > 0 else ""
    response = str(task_outputs[-1].raw) if len(task_outputs) > 0 else str(result)

    updated = dict(ticket)
    updated["retrieved_solution"] = retrieved_solution
    updated["response"] = response
    return updated  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Standalone test — run a single pre-classified ticket through the crew
# without needing graph.py or the API. Mirrors the build-plan's "done when"
# pattern from earlier parts.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_ticket = new_ticket(
        ticket_id="T3001",
        message="My app keeps crashing every time I try to upload a file. Very annoying.",
        channel="email",
        timestamp="2026-06-19T12:00:00Z",
    )
    test_ticket["sentiment"] = "negative"
    test_ticket["sentiment_score"] = -0.6
    test_ticket["issue_type"] = "crash"
    test_ticket["module"] = "upload"
    test_ticket["priority"] = "High"

    final = run_crew(test_ticket)
    print("Retrieved solution:\n", final["retrieved_solution"])
    print("\nDrafted response:\n", final["response"])