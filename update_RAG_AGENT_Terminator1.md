# Terminator1 RAG Agent — Technical Evolution & Integration Plan (v1.1)
**Implementation Blueprint — Sept 7, 2026**

This document is the authoritative engineering blueprint for resolving the path
resolution, parameter-schema, model tool-exposure, and security-boundary defects
identified in the Terminator1 runtime diagnostics. It enforces strong programmatic
contracts, state encapsulation, and platform-agnostic paths (Windows + Linux).

The plan maps onto the existing codebase. **Much of the original v1.1 is already
implemented** (single-authority `app/paths.py`, hardened JSON extraction, per-agent
tool profiles, discoverable `FileSession` state). Strategy = **gap-fill and harden**,
not rewrite. The three genuine defects to fix are:

1. **No read guardrail** — the LLM can `read_file` any absolute path (only `delete_files` is gated).
2. **No canonical `path` identifier** — `write_text_file` uses `output_path`; `delete_files` uses `file_list`.
3. **No execution tracing** — no step telemetry (REASON/ACT/OBSERVE).

---

## Decisions (locked for implementation)

| Item | Decision |
|------|----------|
| Phase 2 canonical identifier | **Rename** `write_text_file(output_path)` → `path` (clean break). |
| Phase 3 tool IDs | **Keep existing IDs** (`search_chat_logs`, etc.). Relabel domain groups only; do NOT invent `search_rag`/`retrieve_document`. |
| Phase 4 guardrail scope | **Discover-then-read** — reject reads on paths not surfaced by a prior `map_files` in the session. |
| Phase 5 tracing | **Log-based only** — written to `DATA_DIR/trace.jsonl`. No API/store. |
| Phase 6 test framework | **pytest** new suite in `tests/`; keep existing `rag/test_app.py` (unittest) untouched. |
| `E:\data\` literal dirs | **Include cleanup step** — remove the Windows-migration artifact directories from the repo during implementation. |

---

## Architecture Overview (Phases 0–6)

```
 PHASE 0: Tool & State Contracts (I/O schemas)
                   │
                   ▼
 PHASE 1: Cross-Platform Paths (pathlib Path authority)
                   │
                   ▼
 PHASE 2: Tool Schema Alignment (canonical 'path')
                   │
                   ▼
 PHASE 3: Tool Domains & Agent Profiles (FILE/KNOWLEDGE/CONVERSATION)
                   │
                   ▼
 PHASE 4: Filesystem Guardrails (programmatic, outside LLM)
                   │
                   ▼
 PHASE 5: Agent Execution Tracing (REASON ➔ ACT ➔ OBSERVE)
                   │
                   ▼
 PHASE 6: Behavioral Smoke Tests (pytest verification suite)
```

---

## Current-State Audit (what already exists vs. what is missing)

### Already in place (reuse, do not rewrite)
- **`app/paths.py`** — single authority for all runtime paths; `BASE_DIR`, `resolve_path`,
  relative→`BASE_DIR` joining, `expanduser`, `restart_needed()`. → satisfies most of Phase 1.
- **`_extract_text_tool_calls`** (`app/core/agent.py`) — markdown unwrap, regex JSON scan,
  name-registration check, multi-key arg handling (`arguments`/`args`/`parameters`),
  `_normalize_args`, `_has_required_args`, `_coerce_bools`. → satisfies most of Phase 2 extraction.
- **Agent profiles** (`agent_library/*/agent.json`) — per-agent `tools[]` lists. → Phase 3 base.
- **`FileSession`** (`app/tools/state.py`) — tracks `discovered_files`, `read_files`,
  `output_files`, `pending_deletion`, `selected_files`. → Phase 4 foundation.
- **Two-step `delete_files` gate** (`app/agents/factory.py` `_session_aware`) — approval +
  pending-deletion enforcement. → partial Phase 4.
- **Standard response shape** `{"success","tool","data","error"}` in tool returns. → Phase 0 base.
- **`_record_result`** (`app/agents/factory.py`) — hydrates `FileSession` from tool results
  (`map_files` → `add_discovered`, etc.). → Phase 4 read-after-map hook already wired.

### Missing / to be added
1. Formal tool-output **contract helper** (typed `ToolResponse`).
2. **`dir`/`directory` → `path` normalization** in extraction layer.
3. `write_text_file` **signature rename** `output_path` → `path`.
4. **`FilesystemGuard`** (read + delete authorization) enforced in the wrapper *before* execution.
5. **Trace** telemetry (JSONL step events).
6. **pytest** suite (8 tests incl. path resolution).
7. **Cleanup** of `E:\data\` literal dirs.

---

## Phase 0 — Establish the Contracts

**Objective:** Frame typed, consistent input/output structures for all core file ops.

### New: `app/contracts.py`
Typed response helper so every tool returns a predictable object (no raw `str` anachronisms).

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolResponse:
    success: bool
    tool: str
    data: dict = field(default_factory=dict)
    error: str | None = None

def ok(tool: str, data: dict) -> dict:
    return ToolResponse(True, tool, data, None).__dict__

def err(tool: str, message: str, data: dict | None = None) -> dict:
    return ToolResponse(False, tool, data or {}, message).__dict__
```

### Refactor tools in `app/tools/tools.py`
Convert every dict-returning tool to use `ok`/`err` helpers, preserving the **exact key names**
currently used so `_record_result` and consumers stay compatible:

| Tool | data keys on success | contract |
|------|---------------------|----------|
| `map_files` | `files[{name,path,extension,type,parent,level}], truncated, max_entries` | Phase 0 |
| `read_file` | `path, filename, file_type, extracted_content, status` | Phase 0 |
| `write_text_file` | `filename, path, type, size, status` | Phase 0 |
| `delete_files` | `results{}` or `pending_files[]` | Phase 0 |

Convert string-returning tools to typed responses too:
- `get_current_date()` → return `{"success":True,"tool":"get_current_date","data":{"date":"..."},"error":None}`
- `tell_me_the_date_and_time()` → `data: {"datetime":"..."}`
- `search_chat_logs()` → `data: {"results":"<formatted>"}`

> Note: updating these return shapes is a **contract change** observed by `_record_result` and
> the injected session context. Verify `factory.py` `_record_result` handles the new shape
> (or keep `.data` keyed identically so it does).

---

## Phase 1 — Cross-Platform Paths

**Objective:** `app/paths.py` remains the sole authority for resolving absolute locations
dynamically across Windows and Linux; config stays fully relative.

### Current status
`app/paths.py` already implements all required mechanics:
- `BASE_DIR = Path(__file__).resolve().parents[1]`
- `APP_SETTINGS_FILE = BASE_DIR / "static" / "config" / "app_settings.json"`
- `resolve_path(raw, *fallback_parts)` — uses `expanduser`, `is_absolute()`, `resolve()`.
- `DATA_DIR`, `CHATS_DIR`, `RECORDS_DIR`/`CHAT_RECORDS_DIR`, `RAG_DB_DIR`, `LOG_FILE`,
  `ACTIVE_SESSION_FILE`, `HISTORY_FILE`, `EXPORTS_DIR`.

### Action items
- **No logic rewrite.** Verify all filesystem-path construction in `app/tools/tools.py`
  flows through `paths` (or `Path(path)` on LLM-supplied absolute paths).
- Strengthen docstring to codify the single-authority rule: *configuration defines
  locations; paths.py resolves them.*
- **Cleanup `E:\data\` literal dirs** (see Phase 7 cleanup step) — remove the Windows-artifact
  directories from the Linux filesystem and repo.
- Path **behavioral tests** (Phase 6 Tests 7 & 8) validate the resolution logic with
  monkeypatched config; separators asserted via `PureWindowsPath` vs POSIX `Path`.

---

## Phase 2 — Tool Schema & Interface Alignment

**Objective:** Align Python function parameters with LLM schema declarations, enforce a single
canonical `path` identifier, and harden secure JSON tool extraction.

### 2a. Canonical `path` identifier
- **`write_text_file(name, content, output_path, overwrite=False)` → `write_text_file(name, content, path, overwrite=False)`**
  in `app/tools/tools.py` (signature + docstring).
- Update any reference to `output_path` in `agent_library/*/agent.md` prompts.
- `factory.py` `_record_result` already reads `data.get("path")` for writes → stays consistent.

### 2b. `dir`/`directory` → `path` normalization (extraction layer)
Add to `Agent._extract_text_tool_calls` in `app/core/agent.py` — small models frequently emit
`{"dir":...}`/`{"directory":...}` instead of `path`. Normalize per registered tool's actual
param names **before** `_normalize_args`:

```python
# Inside the per-candidate loop, after item.get("arguments"):
args = item.get("arguments", item.get("args", item.get("parameters", {}))) or {}
if isinstance(args, dict):
    if "dir" in args:        args["path"] = args.pop("dir")
    elif "directory" in args: args["path"] = args.pop("directory")
args = self._normalize_args(item["name"], args)
```

The existing `_normalize_args` already drops unknown keys, coerces booleans, and maps
positional lists, so this slot-in is safe.

### 2c. Verification
- Re-run the existing hardening invariants after the change:
  - Only names registered in `self.tools` are executed.
  - Only calls with required args present are executed (prose mentioning a tool never fires it).

---

## Phase 3 — Tool Domains & Agent Profiles

**Objective:** Organize tools into 3 functional domains to reduce cognitive load on small
models (e.g. Llama 3.2 1B).

### Reorganize registry by domain (`app/tools/registry.py`)
Comment-only grouping into three named blocks; IDs unchanged (locked decision):

```
FILE DOMAIN           KNOWLEDGE DOMAIN      CONVERSATION DOMAIN
map_files             search_chat_logs      get_current_date
read_file                                   tell_me_the_date_and_time
write_text_file
delete_files
```

- Add a small mapping `TOOL_DOMAINS: dict[str, str]` (or constant) to make the grouping
  explicit and programmatically queryable (optional, low effort).

### Trim agent profiles (`agent_library/`)
- **`rag_assistant/agent.json`** → FILE (discovery/read) + KNOWLEDGE:
  ```json
  "tools": ["map_files", "read_file", "search_chat_logs"]
  ```
- **`dev_assistant/agent.json`** → FILE domain only:
  ```json
  "tools": ["map_files", "read_file", "write_text_file", "delete_files"]
  ```

> `get_current_date`/`tell_me_the_date_and_time`: useful to rag_assistant; add
> `get_current_date` to rag_assistant if desired (KNOWLEDGE-adjacent). Final tool list is
> a product call. Document the chosen set in agent.md `ROLE`/`BOUNDARIES`.

### Update agent.md `BOUNDARIES`
- `rag_assistant/agent.md`: instruct **map before read** — "always run `map_files` on a
  directory before `read_file` so the runtime can authorize the path."
- `dev_assistant/agent.md`: keep full-file access; note the two-step delete gate still applies.

---

## Phase 4 — Filesystem Guardrails

**Objective:** Construct a programmatic safety layer in the orchestrator (`app/core/agent.py`
and/or `app/agents/factory.py`) that rejects filesystem requests unless verified by session
history — **independent of the LLM**.

### New: `app/tools/guard.py`
```python
from pathlib import Path

class FilesystemGuard:
    """Rejects fs actions on paths not surfaced by a prior map_files in the session."""

    @staticmethod
    def verify_action(tool_name: str, args: dict, session) -> tuple[bool, str]:
        # Only read_file (and delete_files, re-confirmed) are guarded.
        if tool_name not in ("read_file", "delete_files"):
            return True, ""

        target = args.get("path") if tool_name == "read_file" \
            else (args.get("file_list") or args.get("path"))
        if not target:
            return False, "MISSING_ARGUMENT: 'path' is required."

        paths = [target] if isinstance(target, str) else target
        discovered = {str(Path(p).resolve()) for p in (session.discovered_files or [])}

        for p in paths:
            rp = str(Path(p).resolve())
            if rp not in discovered:
                return False, f"PATH_NOT_AUTHORIZED: '{p}' was not mapped via map_files."
        return True, ""
```

### Enforce in `app/agents/factory.py` `_session_aware`
- If `func.__name__` is `read_file` or `delete_files`, call `FilesystemGuard.verify_action`
  **before** executing. On failure, return
  `{"success":False,"tool":"<name>","data":{},"error":"PATH_NOT_AUTHORIZED: ..."}` and
  trace the rejection (Phase 5).
- Existing `delete_files` approval gate remains; guard adds a second, discovery-based layer.

### Behavior guarantees
- `map_files` results already record into `session.discovered_files` via `_record_result`
  → discover-then-read works with no extra plumbing.
- A `read_file` on any *un-mapped* absolute path is rejected programmatically, even for
  nonexistent paths (Test 5 invalid → guard returns reject; or allow existence-check to
  produce `PATH_NOT_FOUND` first — see Test 5 note below).

### Test 5 nuance (invalid vs unauthorized)
- **Unauthorized** (never mapped, regardless of existence) → `PATH_NOT_AUTHORIZED` (Test 6).
- **Invalid** (mapped but deleted/nonexistent at read time) → the tool itself returns
  `PATH_NOT_FOUND` (Test 5). Implementation should run the guard first; if the path *was*
  discovered, let the tool do its existence check.

---

## Phase 5 — Agent Execution Tracing

**Objective:** Capture precise step metrics through three distinct phase boundaries
(REASON ➔ ACT ➔ OBSERVE). **Log-based only** (locked decision).

### New: `app/tools/trace.py`
Append-only JSONL writer to `DATA_DIR / "trace.jsonl"`.

```python
import json, time
from app import paths

def _now_ms(): return int(time.time() * 1000)

def emit(ctx: dict):
    """ctx: run_id, agent_name, model, session_id, step, phase, event_type,
             tool, arguments, result, error, duration_ms"""
    line = json.dumps(ctx, default=str) + "\n"
    try:
        with open(paths.DATA_DIR / "trace.jsonl", "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
```

### Instrumentation points
- **`server.py`** chat flow: generate `run_id` (e.g. `chr-{hex}`), pass `session_id`,
  `agent_name`, `model` into the Agent/session. Stamp `step` counter.
- **`Agent.think()`** (`app/core/agent.py`): log phase `REASON` at each LLM round; after
  each `act()`/`observe()`, log phase `ACT`/`OBSERVE` with tool, arguments, result summary,
  and `duration_ms`.
- **Guard** (`factory.py` `_session_aware`): on rejection, emit phase `GUARD`,
  `event_type: "tool_call_rejection"`, `result: "REJECTED"`, `error: "PATH_NOT_AUTHORIZED"`.

### Sample event (matching plan v1.1)
```json
{
  "run_id": "chr-595e909a797e",
  "agent_name": "rag_assistant",
  "model": "llama3.2:1b",
  "session_id": "chat_ai_ragagent_test",
  "step": 3,
  "phase": "GUARD",
  "event_type": "tool_call_rejection",
  "tool": "read_file",
  "arguments": {"path": "/home/goofy/test/new-chat.txt"},
  "result": "REJECTED",
  "error": "PATH_NOT_AUTHORIZED",
  "duration_ms": 12
}
```

### Scope guard
- Trace file is **append-only, best-effort** (never breaks request on write failure).
- Add an optional `"trace": true/false` toggle in `app_settings.json` (default true) so it
  can be disabled if desired.

---

## Phase 6 — Behavioral Smoke Tests

**Objective:** Run 8 distinct unit/integration tests verifying end-to-end framework robustness.

### Test layout
- New **pytest** suite in `tests/` (no `conftest` required beyond a shared import path helper).
- Keep existing `rag/test_app.py` (unittest) untouched; both runnable:
  - `python -m pytest tests/ -q`
  - `python -m unittest discover -s rag -p "test_*.py"` (existing)

### The 8 tests

| # | Name | Type | What it verifies |
|---|------|------|------------------|
| 1 | Valid Discovery | integration | `map_files` called successfully; returns structured `files[]`. |
| 2 | Valid PDF Read | integration | `read_file` on a discovered `.pdf` uses docling fallback (or graceful message if docling absent). |
| 3 | No Invented Path | integration | User says "Read the PDF" → agent runs `map_files` first, then `read_file` on a discovered path. |
| 4 | No Boundary Overlap | integration | FS request → agent does **not** call `search_chat_logs`/date tools. |
| 5 | Invalid Path | unit | Mapped-but-missing file → handler returns `PATH_NOT_FOUND`. |
| 6 | Unauthorized Path | unit | Read on un-mapped dir → programmatic `PATH_NOT_AUTHORIZED` (guard, no LLM). |
| 7 | Windows Resolution | unit | Relative config joins and resolves using `\` (via `PureWindowsPath`). |
| 8 | Linux Resolution | unit | Relative config joins and resolves using `/` (via POSIX `Path`). |

### Key test helpers / notes
- **Guard tests (5, 6)** call `FilesystemGuard.verify_action` directly with a stub `FileSession`
  — no LLM required, deterministic.
- **Path tests (7, 8)** monkeypatch `app.paths` config (empty/relative `dataDir`) and assert
  joined output under `PureWindowsPath` vs `Path`. True `\` enforcement isn't possible on a
  Linux host for real I/O, so these test the *resolution logic* with pathlib abstractions.
- **Integration tests (1–4)** run against `build_agent()` with the real `rag_assistant`
  profile (guided, not full LLM) OR mock `ask_llm` to script tool-call sequences — choose the
  mock approach for determinism in CI.

---

## Phase 7 — Repo Cleanup

**Objective:** Remove the Windows-migration artifact directories from the Linux filesystem/repo.

- `E:\data\db\chroma.sqlite3`
- `E:\data\rag_store\...` (chatRecord.jsonl, .active-chat.json, agent-text-records/*.txt)

**Actions**
- Backup/relocate any recoverable transcripts into the *configured* `RECORDS_DIR`
  (`/home/goofy/test` per current `app_settings.json`), then delete the literal `E:\ ` dirs.
- Verify `config/settings.json`, `app_settings.json`, and `rag/main.py` no longer reference
  hard-coded `E:\` paths.
- Add `E:\` / `E:/` style patterns to `.gitignore` awareness (defensive, does not over-reach).
- Re-run `python scripts/rebuild_rag.py status` to confirm the real `ragDbPath` store is healthy.

---

## Verification / Rollout Checklist

Before considering the integration complete:

- [ ] `python -m pytest tests/ -q` — all 8 tests pass.
- [ ] `python -m unittest discover -s rag -p "test_*.py"` — existing RAG tests still pass.
- [ ] Manual smoke: chat to `rag_assistant` → "map /home/goofy/test, then read the files"
      → confirm read succeeds; "read /etc/passwd" → confirm `PATH_NOT_AUTHORIZED`.
- [ ] `trace.jsonl` shows REASON/ACT/OBSERVE/GUARD events for a test conversation.
- [ ] `write_text_file` schema reflects `path` (verify in system prompt / Ollama tool schema).
- [ ] `E:\` dirs removed; RAG store healthy at configured `ragDbPath`.
- [ ] No regressions in existing 6-tool behavior (`dev_assistant` full-file flow).

---

## Execution Order (recommended)

1. **Phase 0** — `contracts.py` + tool return refactor (verify `_record_result`).
2. **Phase 2** — `write_text_file` rename + `dir`/`directory`→`path` normalization.
3. **Phase 3** — registry domain grouping + agent.json tool list trim.
4. **Phase 4** — `FilesystemGuard` + `factory.py` enforcement + agent.md boundaries.
5. **Phase 5** — `trace.py` + instrumentation (`server.py`, `agent.py`, `factory.py`).
6. **Phase 1 + 6** — pytest suite (incl. path-resolution Tests 7/8).
7. **Phase 7** — `E:\` cleanup + final verification checklist.
