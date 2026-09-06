"""
app/paths.py
============

Single source of truth for every runtime folder/file the app reads or
writes. Paths are configurable from the UI (stored in
static/config/app_settings.json via /api/settings) plus the small
config/settings.json server file:

    app_settings.json keys:
        dataDir        base data folder (default "data").
                       Relative -> project root; absolute -> used as-is.
        chatSavePath   where saved chat transcripts (.txt) are written.
                       Empty -> <dataDir>/chatlog/agent-text-records
        ragDbPath      where the RAG store (chroma.sqlite3) lives.
                       Empty -> <dataDir>/rag_db
        rag            { commitOnSave: bool, autoIngest: bool }

Everything else is derived from these so changing "data folder" moves the
chatlog, transcripts, history, exports and RAG store together.

Path config is resolved at import time - save settings in the UI, then
restart the server for dataDir/ragDbPath/chatSavePath changes to apply.
`restart_needed()` tells callers when a restart is required.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
APP_SETTINGS_FILE = BASE_DIR / "static" / "config" / "app_settings.json"
SERVER_SETTINGS_FILE = BASE_DIR / "config" / "settings.json"

_EMPTY = (None, "", "")


def _get_text(value):
    """Normalize a config value to str; '' for empty/missing."""
    if value in _EMPTY:
        return ""
    return str(value).strip()


def _bool(value):
    return bool(value)


def _load_app_settings() -> dict:
    try:
        return json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _load_server_settings() -> dict:
    try:
        return json.loads(SERVER_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_path(raw, *fallback_parts) -> Path:
    """Base folder for a configured location.

    `raw` is a folder path and is used verbatim when present (absolute paths
    are used as-is so the data/RAG store can live outside the project;
    relative paths resolve against the project root). When `raw` is empty,
    fall back to BASE_DIR/<fallback_parts>.
    """
    text = _get_text(raw)
    if not text:
        return BASE_DIR.joinpath(*fallback_parts).resolve()
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _rooted(raw, default: Path) -> Path:
    """Like resolve_path, but the fallback is an already-resolved Path."""
    text = _get_text(raw)
    if not text:
        return default
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


# --------------------------------------------------------------------------
# Runtime values (resolved once at import - restart server after editing).
# --------------------------------------------------------------------------

_cfg = _load_app_settings()
_server_cfg = _load_server_settings()

# Snapshot of the stored *path* settings at import. The UI can compare
# against the live file to tell the user a restart is required.
_PATH_KEYS = ("dataDir", "chatSavePath", "ragDbPath")
_path_keys_at_import = {key: _get_text(_cfg.get(key)) for key in _PATH_KEYS}

# Base data folder (dataDir overrides the default "data").
DATA_DIR = resolve_path(_cfg.get("dataDir"), "data")

# Chat log folder + transcripts (chatSavePath overrides the sub-folder).
CHATS_DIR = DATA_DIR / "chatlog"
CHAT_SAVE_PATH = _get_text(_cfg.get("chatSavePath"))
RECORDS_DIR = _rooted(CHAT_SAVE_PATH, DATA_DIR / "chatlog" / "agent-text-records")
CHAT_RECORDS_DIR = RECORDS_DIR  # alias used by the RAG search tool

# RAG store (ragDbPath overrides <dataDir>/rag_db).
RAG_DB_DIR = _rooted(_cfg.get("ragDbPath"), DATA_DIR / "rag_db")

# Chat log metadata + active session.
LOG_FILE = CHATS_DIR / "chatRecord.jsonl"
ACTIVE_SESSION_FILE = CHATS_DIR / ".active-chat.json"

# History + exports (server.py).
HISTORY_FILE = DATA_DIR / "history.json"
EXPORTS_DIR = DATA_DIR / "exports"


# --------------------------------------------------------------------------
# RAG behavior switches (read live, so the UI changes apply immediately).
# --------------------------------------------------------------------------

def rag_config(default_commit: bool | None = None) -> dict:
    """The current RAG behavior settings: commitOnSave + autoIngest.

    `default_commit`: when supplied, an explicit per-chat flag overrides it;
    otherwise the stored commitOnSave value is returned.
    """
    rag = _cfg.get("rag") or {}
    commit = _bool(rag.get("commitOnSave", False))
    if default_commit is not None:
        commit = bool(default_commit)
    return {
        "commitOnSave": commit,
        "autoIngest": _bool(rag.get("autoIngest", True)),
    }


def restart_needed() -> bool:
    """True when stored path settings changed since import - the running
    server is still using the old resolved locations until a restart."""
    current = _load_app_settings()
    for key in _PATH_KEYS:
        if _get_text(current.get(key)) != _path_keys_at_import.get(key):
            return True
    return False


def about() -> dict:
    """Human-readable summary of the resolved locations (for the config UI
    and the /api/rag/status endpoint)."""
    return {
        "data_dir": str(DATA_DIR),
        "chat_records_dir": str(RECORDS_DIR),
        "chat_log_file": str(LOG_FILE),
        "active_session_file": str(ACTIVE_SESSION_FILE),
        "history_file": str(HISTORY_FILE),
        "exports_dir": str(EXPORTS_DIR),
        "rag_db_dir": str(RAG_DB_DIR),
        "rag": rag_config(),
    }