
"""
app/tools/search.py
===================

Search tools for Terminator1. Hooks directly into the local RAG engine.
"""

import sys
from pathlib import Path

# Ensure the root of the project is on sys.path so we can import the 'rag' module
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app import paths

# Location of the RAG store + the transcripts that feed it come from the
# configured paths (UI-editable), not hardcoded values.
RAG_DB_DIR = paths.RAG_DB_DIR
CHAT_RECORDS_DIR = paths.CHAT_RECORDS_DIR

_rag_ingested = False


def _auto_ingest_enabled() -> bool:
    """The `rag.autoIngest` toggle: None of the UI config can bulk-ingest ALL
    transcripts when the store is empty. Off -> only explicit commits (per-chat
    "save to memory") or a rebuild populate the store."""
    return bool(paths.rag_config()["autoIngest"])


def _ensure_rag_ingested(storage) -> bool:
    """Index the chat transcripts into the persistent store once, when the
    store is empty AND auto-ingest is enabled.

    Returns True when this process has already ingested (or just successfully
    ingested) the transcripts; False when there was nothing to ingest or the
    feature is disabled.
    """
    global _rag_ingested
    if _rag_ingested:
        return True
    if not _auto_ingest_enabled():
        return False

    from rag.ingest import ingest_directory

    chunks = ingest_directory(str(CHAT_RECORDS_DIR))
    if chunks:
        storage.add_chunks(chunks)
        print(f"[RAG] Auto-ingested {len(chunks)} segment(s) from {CHAT_RECORDS_DIR}")
    _rag_ingested = True
    return bool(chunks)


def search_chat_logs(query: str) -> str:
    """Searches past chat transcripts for keywords and returns the most relevant matching segments.

    Args:
        query (str): The search keyword, term, or phrase to look up.
    """
    try:
        # Dynamically import to avoid any circular dependencies at startup
        from rag.search import RAGStorage

        # Point to the persistent database folder (configurable).
        storage = RAGStorage(persist_dir=str(RAG_DB_DIR))

        # Query the database
        results = storage.query(query, n_results=3)

        # Self-heal: an empty store means the transcripts were never indexed.
        # When rag.autoIngest is on, ingest them once, then query again.
        if not results:
            if _ensure_rag_ingested(storage):
                results = storage.query(query, n_results=3)

        if not results:
            return f"No matches found in your chat transcripts for the query: '{query}'."
            
        formatted_results = [f"--- RAG SEARCH RESULTS FOR: '{query}' ---"]
        for idx, r in enumerate(results):
            meta = r["metadata"]
            session_title = meta.get("session_title") or meta.get("source_file", "Untitled Chat")
            speaker = meta.get("speaker", "Unknown")
            date = meta.get("date", "Unknown Date")
            
            formatted_results.append(
                f"Result [{idx + 1}]:\n"
                f"  Session: {session_title}\n"
                f"  Speaker: {speaker} | Date: {date}\n"
                f"  Content: {r['text']}\n"
                f"----------------------------------------"
            )
            
        return "\n\n".join(formatted_results)
        
    except ImportError:
        return "Error: RAG engine modules not found. Ensure the 'rag/' folder is present in your project root."
    except Exception as e:
        return f"Error executing chat log search: {e}"