"""
app/rag_commit.py
=================

Commit ONE finalized chat transcript into the persistent RAG store so the
RAG assistant / search_chat_logs can recall it.

Called from app/chat_store/store._finalize_locked when a chat is marked to
be saved to memory (per-chat toggle or the commitOnSave default).
"""

from pathlib import Path

from app import paths
from rag.ingest import ingest_file


def commit_transcript(transcript: Path) -> str:
    """Index a single transcript file into the RAG store.

    Returns a short status string for logging / the UI. Uses upsert ids, so
    re-committing the same chat version simply overwrites its segments.
    """
    file_path = Path(transcript)
    chunks = ingest_file(str(file_path))
    if not chunks:
        return f"RAG commit: {file_path.name} had no indexable content."

    from rag.search import RAGStorage

    storage = RAGStorage(persist_dir=str(paths.RAG_DB_DIR))
    storage.add_chunks(chunks)
    print(f"[RAG-COMMIT] Indexed {len(chunks)} segment(s) from {file_path.name}")
    return f"RAG commit: indexed {len(chunks)} segment(s) from {file_path.name}."


def purge_store() -> str:
    """Delete the RAG store so it starts empty (chroma sqlite + fallback)."""
    import shutil

    store_dir = Path(paths.RAG_DB_DIR)
    removed = []
    if store_dir.exists():
        for child in store_dir.iterdir():
            if child.name in ("chroma.sqlite3", "fallback_vector_db.json"):
                if child.is_file():
                    child.unlink(missing_ok=True)
                    removed.append(child.name)
        # Remove empty leftover segment dirs left by chroma.
        for child in store_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
    if removed:
        return f"RAG store purged: removed {', '.join(removed)} from {store_dir}. The store is now empty."
    # If there was no store yet, make sure the folder exists so a fresh
    # (empty) collection is created on next use.
    store_dir.mkdir(parents=True, exist_ok=True)
    return f"RAG store at {store_dir} was already empty."


def rebuild_store() -> str:
    """Re-index every transcript in the chat records folder into the store."""
    from rag.ingest import ingest_directory
    from rag.search import RAGStorage

    chunks = ingest_directory(str(paths.CHAT_RECORDS_DIR))
    if not chunks:
        return "RAG rebuild: no transcripts found to index."

    storage = RAGStorage(persist_dir=str(paths.RAG_DB_DIR))
    storage.add_chunks(chunks)
    print(f"[RAG-REBUILD] Indexed {len(chunks)} segment(s)")
    return f"RAG rebuild: indexed {len(chunks)} segment(s)."


def status() -> dict:
    """Store path + chunk count (0 when empty)."""
    count = _chunk_count()
    return {
        "path": str(paths.RAG_DB_DIR),
        "chunks": count,
        "config": paths.rag_config(),
        "about": paths.about(),
    }


def _chunk_count() -> int:
    """Number of stored embeddings, read directly from chroma.sqlite3."""
    import sqlite3

    db_file = Path(paths.RAG_DB_DIR) / "chroma.sqlite3"
    if not db_file.exists():
        return 0
    try:
        with sqlite3.connect(str(db_file)) as conn:
            row = conn.execute("SELECT count(*) FROM embeddings").fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        fallback = Path(paths.RAG_DB_DIR) / "fallback_vector_db.json"
        if fallback.exists():
            import json

            try:
                return len(json.loads(fallback.read_text(encoding="utf-8")).get("documents", []))
            except (OSError, json.JSONDecodeError):
                return 0
        return 0