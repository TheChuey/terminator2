"""
scripts/rebuild_rag.py
======================

Manage the persistent RAG store (data/rag_db/chroma.sqlite3 by default; the
actual location + behavior comes from the configured app settings).

Subcommands:
    build             (default) re-index every transcript in the chat records
                      folder into the store (idempotent - safe to re-run).
    purge             delete the RAG store so it starts empty. Transcripts and
                      chat records are untouched.
    status            show the store location + how many segments are indexed
                      (0 means empty).

Usage:
    python scripts/rebuild_rag.py [build|purge|status]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import paths  # noqa: E402
from app.rag_commit import purge_store, rebuild_store, status  # noqa: E402


def main() -> int:
    command = (sys.argv[1] if len(sys.argv) > 1 else "build").lower()

    if command == "build":
        print(rebuild_store())
        return 0
    if command == "purge":
        print(purge_store())
        return 0
    if command == "status":
        info = status()
        print(f"RAG store:   {info['path']}")
        print(f"Chunks:      {info['chunks']}")
        print(f"Commit on save: {info['config']['commitOnSave']}")
        print(f"Auto-ingest:    {info['config']['autoIngest']}")
        print(f"Chat records:   {paths.CHAT_RECORDS_DIR}")
        return 0

    print(f"Unknown command '{command}'. Use build, purge or status.")
    return 1


if __name__ == "__main__":
    sys.exit(main())