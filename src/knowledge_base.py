"""
knowledge_base.py — RAG Knowledge Base for the Real-Time Support Triager (Part 1).

Builds a searchable vector index over data/historical_tickets.csv and exposes
retrieve_similar_tickets(query, top_k) for Agent 1 (Context Liaison, Part 3)
to call at runtime.

Implements parent-child ("small-to-big") retrieval:
  - CHILD node  = just the `problem` sentence -> this is what gets embedded
                  and matched against the incoming query. Keeping the
                  embedded text short and focused gives more precise
                  similarity matches than embedding the whole record.
  - PARENT data = the full ticket record (problem + solution + issue_type +
                  module), attached as node metadata -> this is what gets
                  returned to the agent so it has full context to draft a
                  response, even though matching only used the child text.

Vector store: ChromaDB (persistent, local, no cloud) via LlamaIndex's
native Chroma connector.
Embedding model: BAAI/bge-small-en-v1.5 (lightweight, fast, no API key).
"""

import os
import pandas as pd
import chromadb

from llama_index.core import VectorStoreIndex, StorageContext
from llama_index.core.schema import TextNode
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.embeddings.huggingface import HuggingFaceEmbedding

CSV_PATH = "data/historical_tickets.csv"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "support_tickets"
EMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"

_embed_model = None  # lazy singleton so we only load the model once per process


def get_embed_model() -> HuggingFaceEmbedding:
    global _embed_model
    if _embed_model is None:
        _embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)
    return _embed_model


def build_knowledge_base(csv_path: str = CSV_PATH) -> VectorStoreIndex:
    """
    Reads the seed CSV, builds child nodes (problem text only, for
    embedding) carrying parent metadata (full record, for context),
    embeds them, and upserts into a persistent local ChromaDB collection.

    Run this once whenever historical_tickets.csv changes.
    """
    df = pd.read_csv(csv_path)
    required_cols = {"problem", "solution", "issue_type", "module"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    # Build child nodes: embed only the `problem` text, but stash the full
    # parent record in metadata so retrieval can return complete context.
    nodes = []
    for i, row in df.iterrows():
        node = TextNode(
            text=row["problem"],  # <- child text, used for embedding/matching
            id_=f"ticket_{i}",
            metadata={
                "problem": row["problem"],
                "solution": row["solution"],
                "issue_type": row["issue_type"],
                "module": row["module"],
            },
        )
        nodes.append(node)

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    # Start clean each build so re-running doesn't accumulate stale/duplicate
    # vectors from previous versions of the CSV.
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    chroma_collection = client.create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex(
        nodes,
        storage_context=storage_context,
        embed_model=get_embed_model(),
    )

    print(f"✅ Knowledge base built: {len(df)} tickets indexed.")
    return index


def _load_index() -> VectorStoreIndex:
    """Reconnects to the persisted ChromaDB collection without rebuilding it."""
    if not os.path.isdir(CHROMA_PATH):
        raise FileNotFoundError(
            f"No knowledge base found at {CHROMA_PATH}. Run build_knowledge_base() first."
        )
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    chroma_collection = client.get_collection(COLLECTION_NAME)
    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    return VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=get_embed_model(),
    )


def retrieve_similar_tickets(query: str, top_k: int = 3) -> list[dict]:
    """
    Embeds `query`, searches the child (problem) embeddings for the closest
    matches, and returns the parent (full record) context for each match.

    Returns a list of dicts: {problem, solution, issue_type, module, similarity}
    ordered by descending similarity. This is the function Agent 1 (Context
    Liaison, Part 3) will call at runtime.
    """
    index = _load_index()
    retriever = index.as_retriever(similarity_top_k=top_k)
    results = retriever.retrieve(query)

    retrieved = []
    for r in results:
        meta = r.node.metadata
        retrieved.append({
            "problem": meta["problem"],
            "solution": meta["solution"],
            "issue_type": meta["issue_type"],
            "module": meta["module"],
            "similarity": r.score,
        })
    return retrieved


if __name__ == "__main__":
    build_knowledge_base()

    # Standalone test — matches the "done when" criterion from the build plan.
    test_query = "My app keeps crashing when I upload a file"
    print(f"\nQuery: \"{test_query}\"\n")
    results = retrieve_similar_tickets(test_query)
    for r in results:
        print(f"[{r['similarity']:.2f}] {r['problem']} -> {r['solution']}")