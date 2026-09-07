# Tool Registry Repair & File Toolkit Integration — Change Log

## Date
September 6, 2026 (ROUND 4: September 7, 2026; ROUND 5: September 7, 2026)

## Summary
Repaired the broken `app/tools/registry.py`, restored cross-compatibility with
the agent factory (`app/agents/factory.py`), re-registered legacy tools so
existing agents work again, integrated the new `FileSession` state manager
into the agent runtime, and (Round 2) fixed the root cause of malformed tool
calls, silent empty replies, unbounded directory scans, and session-context
bloat. (Round 4) consolidated the entire tools package and rebuilt the read
tool on IBM Docling. (Round 5) gave the chat UI a one-click RAG-memory reset
and harmonized the config page's RAG-store dialogs.

---

## ROUND 5 — Clear RAG memory from the chat header (September 7, 2026)

### The change
`static/chat.html` gained a "∅ Clear Memory" danger button in the header
(`sc-rag-clear`, next to Delete). It confirms with **"Are you sure you want
to clear the RAG memory?\nAll RAG DB entries will be reset to zero."**, then
POSTs to the existing `/api/rag/reset` endpoint (same store-wipe the config
page's "Forget everything" uses) and reports the result via the save-status
line. Only the index store is reset — transcript files and chat records stay.

### Why it exists
Earlier diagnosis showed the persistent chroma store answers
`search_chat_logs` even after the feeds they came from are deleted, so the
agent "remembers" stale chats. The right recovery was already
`scripts/rebuild_rag.py purge` / "Forget everything" in Configuration —
but only from the config page. ROUND 5 puts the same wipe one click away in
the place the user sees the symptom (the chat window).

### Round 5 changes
| File | Change |
|------|--------|
| `static/chat.html` | Header button `sc-rag-clear` ("∅ Clear Memory", danger style) after Delete; `ragClearButton` added to the DOM registry; new `handleClearRagMemory()` (confirm → `POST /api/rag/reset` → `setSaveStatus`); listener wired beside `handleDeleteChat`. |
| `static/js/ui/config-form.js` | "Forget everything" confirm now reads the identical "reset to zero" wording; fixed the RAG status line (it read `st.path`/`st.chunks` but `/api/rag/status` returns `{"status": {...}}`, so it always showed `undefined`). |

### Verified (Round 5)
- `node --check` passes on `config-form.js` and both inline scripts of
  `chat.html`.
- Grep confirms the new IDs (`sc-rag-clear`, `ragClearButton`,
  `handleClearRagMemory`) and the shared confirm text exist in both files.
- Endpoints unchanged — the new button reuses `POST /api/rag/reset`.
- Snapshot regenerated (49 files / 11 changelogs).

---

## ROUND 4 — Consolidation & Docling (September 7, 2026)

### The big change
The `app/tools/` package was collapsed from 13 files to 3, ending with
**exactly 7 registered tools** — the audit cleared out every duplicate,
stub, and dead code path:

| Before (12 tools, 13 files) | After (7 tools, 3 files) |
|---|---|
| `map_files` (file_map.py) | `map_files` (tools.py) |
| `read_file` (file_reader.py) | `read_file` (tools.py) — now IBM Docling, OCR on |
| `write_text_file` (file_writer.py) | `write_text_file` (tools.py) |
| `delete_files` (file_delete.py) | `delete_files` (tools.py) |
| `get_current_date` / `tell_me_the_date_and_time` (datetime_tools.py) | both in tools.py |
| `search_chat_logs` (search.py) | `search_chat_logs` (tools.py) |
| `write_file`, `read_pdf` (files.py legacy) | REMOVED — covered by write_text_file + Docling read_file |
| `create_folder`, `create_file`, `setup_venv` (workspace.py) | REMOVED — write_text_file mkdir -p's folders |
| `FileSession` (file_session.py) | `FileSession` (state.py) |
| `web.py` (empty), `test_agent_toolkit.py` (broken imports) | REMOVED |

### New layout
```
app/tools/
├── state.py        # ALL classes → FileSession
├── tools.py        # ALL 7 tools, AI-facing docstrings (Ollama schema source)
└── registry.py     # unchanged public API (get/list/resolve/get_session)
```

### Read tool → IBM Docling
`read_file(path, ocr=True)` reads plain-text/code files straight off disk
(fast path) and routes everything else (PDF/DOCX/PPTX/XLSX/HTML/images)
through `docling`'s `DocumentConverter`, exporting clean markdown via
`result.document.export_to_markdown()`. OCR is on by default so scanned
PDFs work. The converter is cached per process (one-time HF model download
on first use). `requirements.txt` gained `docling>=2.59.0` (first release
with Python 3.14 support).

### Verified (Round 4)
- `compileall` clean; registry resolves exactly the 7 kept IDs.
- PDF smoke test: generated 1-page PDF → `## Hello Docling World`.
- `build_agent` for basic_chat (0 tools), rag_assistant (6),
  dev_assistant (7) — all build.
- `rag.test_app` 5/5; snapshot regenerated (49 files / 10 changelogs).
- No live imports of the deleted tool modules (only the historical
  changelog archives still mention them).

---

## ROUND 2 — Stability & Root-Cause Fixes (September 6, 2026)

### Root cause of the original bug (retrospective)
The first reported failure `map_files() got an unexpected keyword argument 'args'`
was NOT the model misbehaving. It was caused by `_session_aware()` wrapping every
tool in `def wrapper(*args, **kwargs)`. Ollama builds each tool's JSON schema
from the function signature via `convert_function_to_tool` (ollama `_utils.py`),
so the schema advertised only `args`/`kwargs` as required string params — and the
model correctly followed that schema. The subsequent "defensive" `*args, **kwargs`
tool edits compounded the problem by hiding the real parameters entirely.

### Round 2 changes
| File | Change |
|------|--------|
| `app/tools/file_map.py` | Typed signature `map_files(path, max_depth=8, max_entries=5000)` + `Args:` docstrings; rewrote with `os.walk` so ignored dirs (`.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `.idea`, `.vscode`) are pruned and depth/entry caps are enforced — no more unbounded recursion. |
| `app/tools/file_reader.py` | Typed signature `read_file(path)` + `Args:` docstring. |
| `app/tools/file_writer.py` | Typed signature `write_text_file(name, content, output_path, overwrite=False)` + `Args:` docstrings. |
| `app/tools/file_delete.py` | Typed signature `delete_files(file_list, approved=False)` + `Args:` docstrings. |
| `app/agents/factory.py` | `_session_aware()` now uses `@functools.wraps(func)` and copies `__signature__`/`__annotations__`, so the tool schema sees the real signature. |
| `app/core/agent.py` | Added `_normalize_args()` runtime defense layer used by both `act()` and `_extract_text_tool_calls()`. |

### The normalization layer (`_normalize_args`)
Centralizes handling of the malformed shapes small local models send:
- JSON-string `arguments` → parsed
- `{"args": ...}` / `{"arguments": ...}` single-key wrappers → unwrapped
- positional list → bound to the tool's params by `inspect.signature`
- string booleans → coerced to real `bool` ONLY on params typed `bool`
  (so `name="yes"` is never mangled, but `approved="false"` becomes `False` —
  this preserves the safe-deletion two-step protocol)
- stray/unknown keys (e.g. `path` on a no-arg tool) → dropped, never crash

### Silent-empty-reply fix (`think()`)
`think()` previously executed tool calls exactly once, did a single follow-up
LLM round, and returned whatever `content` it got — often `""` when the model
replied with another tool call. Now it runs a bounded loop
(`MAX_TOOL_ROUNDS = 6`): every follow-up tool call is executed, observed, and
re-prompted until the model produces real text; if it never does, a visible
fallback message is returned instead of an empty string.

### Session-context dedup (`_inject_session_context`)
Now replaces the previous `CURRENT FILE SESSION STATE` block instead of
appending a new one every round, preventing duplicate-context history growth.

### Verified
- Correct Ollama schemas for all four tools (real param names + descriptions).
- All malformed argument shapes normalize and execute without errors.
- `delete_files` string-`"False"` does NOT delete; string-`"True"` does.
- Bounded loop terminates with fallback (no silent empty response).
- Tool-call → text-answer flow returns the final answer.
- `rag_assistant` 6/6, `dev_assistant` 9/9, `basic_chat` 0 tools.
- `app/tools/test_agent_toolkit.py` passes; `server.py` imports (26 routes).

---

## ROUND 3 — Narrated tool calls now actually execute (September 6, 2026)

### Problem
In a real session with `llama3.1:8b`, the model never executed the file tools.
Instead of using Ollama's native `tool_calls` field, it wrote tool-call-shaped
JSON **inside its text reply**, which the runtime then ignored or mis-parsed:

1. `{"name": "read_file", "parameters": {"path": ...}}` — extraction only read
   `arguments`/`args`, so the `parameters` key produced a call with **empty
   args**: `read_file()` errored with "Missing required argument: 'path'" and
   the model hallucinated file contents in the absence of real tool results.
2. A 13-element **JSON array** of `read_file` calls — `json.loads()` succeeded,
   but only `dict` candidates were accepted, so the whole "read everything" step
   executed **zero** calls.
3. `delete_files(..., "approved": "False")` with empty args meant `pending_deletion`
   was never registered, so the final user "yes" had the model *inventing* paths
   to delete.
4. The write step was emitted as Python code (`write_text_file('x', log, 'dir')`),
   which extraction cannot see — nothing was written.

### Round 3 changes
| File | Change |
|------|--------|
| `app/core/agent.py` | `_extract_text_tool_calls()` now accepts **top-level JSON arrays** (each element is a candidate) and objects wrapped under `tool_calls`/`calls`/`functions`; the args key accepts **`parameters`** in addition to `arguments`/`args`; added a **validity gate** `_has_required_args()` so a call is only emitted when every required (no-default) parameter is present — narration that merely mentions a tool is never executed. |
| `app/agents/factory.py` | **Pending-only delete gate**: `delete_files(approved=True)` can only delete paths previously proposed (`approved=False`) and recorded in `session.pending_deletion`. Invented paths land in `data["rejected"]`; if nothing was pending the call is fully blocked. Pending list is pruned of deleted paths. |
| `agent_library/rag_assistant/agent.md` | New **"how to call tools"** guidance: emit only `{"name": "parameters"}` JSON (or a single JSON array), never Python/pseudo-code, never fabricated paths; only reference paths from session state. |

### Verified (Round 3)
- Extraction replays of the exact transcript shapes: single `parameters` call,
  single `arguments` call, 13-element array → 13 calls with real paths,
  `{"tool_calls": [...]}` wrapper, `delete_files` with string `"False"`, and
  correct rejection of: Python code, empty-args narration, and unknown tool names.
- Delete gate end-to-end with a real file: propose registers pending; confirm
  deletes the pending file but **rejects an invented path**; an invented-only
  confirm is fully blocked; `pending_deletion` is pruned after deletion.
- All agents still build (`rag_assistant` 6/6, `dev_assistant` 9/9,
  `basic_chat` 0, `problem_discovery_agent` 0); `server.py` imports;
  `app/tools/test_agent_toolkit.py` passes.

---

## Changes Made

### 1. `app/tools/registry.py` — full rewrite

**Problems fixed:**
- **Duplicate imports** — the file imported the same 4 modules three times
  (absolute, relative-with-fallback, bare). Collapsed to a single clean import
  block.
- **Missing `resolve_tools()`** — `factory.py` imports `resolve_tools` from this
  module, but the previous rewrite deleted it, causing an `ImportError` for any
  non-chat agent. Restored it (returns a `list[Callable]`, silently skips
  unknown IDs with a warning).
- **Missing `available_tool_ids()`** — legacy alias restored.
- **Redundant maps** — `_TOOL_MAP` and `TOOL_REGISTRY` held identical dicts.
  Collapsed into a single canonical `_TOOL_REGISTRY`.
- **Unregistered tools** — only the 4 new file tools were registered. All legacy
  tools are now registered again so `dev_assistant` and `rag_assistant` work.

**Current tool registry (7 tools — post Round 4):**
| ID                  | Source module       | Purpose                                   |
|---------------------|---------------------|-------------------------------------------|
| `map_files`         | `tools.py`          | Recursively maps a directory structure    |
| `read_file`         | `tools.py`          | Reads any file via IBM Docling (markdown, OCR on); plain text/code fast path |
| `write_text_file`   | `tools.py`          | Writes text files with overwrite guard    |
| `delete_files`      | `tools.py`          | Deletes files only after explicit approval|
| `get_current_date`  | `tools.py`          | Current date string                       |
| `tell_me_the_date_and_time` | `tools.py` | Current date + time string        |
| `search_chat_logs`  | `tools.py`          | RAG search over past chat transcripts     |

**Name conflict resolved (Round 3→4):** `read_file` previously existed in
both `files.py` (legacy, string return) and `file_reader.py` (enhanced, dict
return). The registry used the enhanced version. Round 4 deleted both legacy
modules outright — the single `read_file` in `tools.py` (Docling-powered,
same dict return shape) is the one and only reader.

### 2. `app/tools/file_session.py` — integrated into the runtime

`FileSession` is a working-state manager (discovered files, selected files,
read files, output files, pending deletions). It is NOT a tool itself but is now
wired in three places:

- **`registry.py`** — a shared `FileSession` instance plus `get_session()`.
- **`app/agents/factory.py`** — each `build_agent()` attaches the shared session
  to the `Agent` and wraps every resolved tool with `_session_aware()`, which
  records tool results into the session:
  - `map_files`       -> `add_discovered()`
  - `read_file`       -> `record_read()`
  - `write_text_file` -> `add_output()`
  - `delete_files`    -> `mark_for_deletion()` (on the pending/approval step)
- **`app/core/agent.py`** — the `Agent` now holds a `.session` and injects the
  session state into the conversation as a system message (`_inject_session_context`),
  so the model can see which files it has already discovered/read/written.

### 3. `app/agents/factory.py` — compatibility restored
- Re-imports `resolve_tools` (now valid again) and `get_session`.
- Tools are wrapped with `_session_aware` before being handed to the `Agent`.

### 4. `app/core/agent.py` — session-aware runtime
- `__init__` accepts an optional `session` (defaults to a fresh `FileSession`).
- `think()` calls `_inject_session_context()` before the first LLM round and
  again after executing tool calls, keeping the model informed of accumulated
  file state.

---

## Files Touched
| File | Action |
|------|--------|
| `app/tools/registry.py`      | Rewritten (imports cleaned, tools re-registered, `resolve_tools` restored) |
| `app/agents/factory.py`      | Session integration + `_session_aware` wrapper (Round 1), `functools.wraps` signature fix (Round 2) |
| `app/core/agent.py`          | Session property + context injection (Round 1); `_normalize_args`, bounded tool loop, single-slot session context (Round 2) |
| `app/tools/file_session.py`  | Integrated (unchanged logic, now consumed by runtime) |
| `app/tools/file_map.py`      | Typed signature + `Args:` docs; `os.walk` pruning, depth/entry caps (Round 2) |
| `app/tools/file_reader.py`   | Typed signature + `Args:` docs (Round 2) |
| `app/tools/file_writer.py`   | Typed signature + `Args:` docs (Round 2) |
| `app/tools/file_write.py`   | Typed signature + `Args:` docs (Round 2) |
| `app/tools/file_delete.py`   | Typed signature + `Args:` docs (Round 2) |
| `app/core/agent.py`          | Text-extraction: arrays + `parameters` key + required-args validity gate (Round 3) |
| `app/agents/factory.py`      | Pending-only delete gate + `_record_result` helper (Round 3) |
| `agent_library/rag_assistant/agent.md` | Tool-calling guidance — emit only `{"name","parameters"}` JSON, never narration/Python (Round 3) |

## Verification
- `python -c "import server"` — imports without error (26 routes registered).
- `build_agent('rag_assistant')` — 6/6 tools resolved.
- `build_agent('dev_assistant')` — 9/9 tools resolved.
- `build_agent('basic_chat')` — chat mode, no tools (unchanged).
- `app/tools/test_agent_toolkit.py` — passes end-to-end.
- Session integration verified: map_files -> discovered, write -> output,
  read -> read_files, delete(pending) -> pending_deletion.

## Notes for the Future (post Round 4)
- Adding a new tool: write the function in `app/tools/tools.py` (with a
  clear, AI-facing docstring — Ollama turns it into the schema), import it
  in `registry.py`, and add an entry to `_TOOL_REGISTRY`. No other code
  changes are needed.
- The docstring of each tool is the single most important thing for the LLM:
  it feeds both the Ollama tool schema and the AVAILABLE TOOLS prompt section.
- `FileSession` is process-wide/shared; if per-user isolation is needed later,
  instantiate a session per request instead of using the module-level singleton.
- `read_file` keeps the legacy data shape (`data["path"]`,
  `data["extracted_content"]`, `data["status"]`) — `factory._record_result`
  and session injection depend on it. Keep that shape if you evolve the tool.
- Docling needs network the first time a document is converted (HF model
  download, cached in `~/.cache/huggingface`). On machines without network,
  only the plain-text/code fast path of `read_file` will work.