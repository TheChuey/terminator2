"""
app/agents/factory.py
=====================

Constructs runtime Agents from agent definitions.

    build_agent(agent_id, model)
        ↓
    loader.load_definition()      (agent.md + agent.json)
        ↓
    registry: resolve tools       (IDs -> Python functions)
        ↓
    PromptManager.build()         (sections + tool docstrings -> system prompt)
        ↓
    Agent

The caller never needs to know where definitions live or how prompts are
composed. Chat-mode agents get an empty tool list, which disables the
tool loop entirely - same Agent class, behavior driven by configuration.
"""

from typing import Callable

from app.agents.loader import load_definition, AgentNotFoundError
from app.core.agent import Agent
from app.core.prompt import PromptManager
from app.tools.registry import resolve_tools, get_session


def _session_aware(func: Callable, session) -> Callable:
    """Wrap a tool so its results are recorded into the shared FileSession.

    Uses functools.wraps so inspect.signature() (and therefore the schema
    Ollama builds for tool calling) sees the REAL tool signature, not the
    wrapper's (*args, **kwargs).

    Standard tool response shape: {"success", "tool", "data": {...}, "error"}.
    Known data keys are translated into session state:
        files                   -> add_discovered(paths)
        path / path+content     -> record_read(...)
        filename/path (written) -> add_output(path)
        pending_files (delete)  -> mark_for_deletion(paths)

    Safety gate: delete_files(approved=True) can only delete paths that were
    previously PROPOSED (approved=False) and recorded in session.pending_deletion.
    Any path the model fabricates or invents is rejected instead of deleted.
    """
    import functools
    import inspect as _inspect

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if func.__name__ == "delete_files" and session is not None:
            bound, approved, file_list = _bind_delete_args(func, args, kwargs)
            if approved and file_list:
                pending = list(session.pending_deletion or [])
                proposed = [f for f in file_list if f in pending]
                rejected = [f for f in file_list if f not in pending]
                if not proposed:
                    return {
                        "success": False,
                        "tool": "delete_files",
                        "data": {},
                        "error": (
                            "Deletion blocked: none of these paths were previously "
                            "proposed for deletion. Run delete_files with "
                            "approved=False first."
                        ),
                    }
                bound.arguments["file_list"] = proposed
                try:
                    result = func(*bound.args, **bound.kwargs)
                except TypeError:
                    result = None
                if isinstance(result, dict):
                    data = result.get("data") or {}
                    data["rejected"] = rejected
                    if rejected and not result.get("error"):
                        result["error"] = "Some paths were not previously proposed and were skipped."
                    deleted = [k for k, v in data.get("results", {}).items() if v == "deleted"]
                    if deleted:
                        session.pending_deletion = [p for p in session.pending_deletion if p not in deleted]
                return result

        result = func(*args, **kwargs)
        _record_result(result, session)
        return result

    # Belt-and-braces: even if a future consumer uses follow_wrapped=False,
    # the wrapper advertises the real signature and annotations.
    wrapper.__signature__ = _inspect.signature(func)
    wrapper.__annotations__ = func.__annotations__
    return wrapper


def _bind_delete_args(func, args, kwargs):
    """Bind delete_files(*args, **kwargs) into (BoundArguments, approved, file_list)."""
    import inspect as _inspect
    try:
        bound = _inspect.signature(func).bind(*args, **kwargs)
        bound.apply_defaults()
    except TypeError:
        return None, False, []
    return bound, bool(bound.arguments.get("approved")), list(bound.arguments.get("file_list") or [])


def _record_result(result, session) -> None:
    """Translate a tool result dict into shared FileSession state."""
    if not (isinstance(result, dict) and session is not None):
        return
    from app.tools.state import FileSession
    data = result.get("data") or {}
    if result.get("tool") == "map_files":
        files = data.get("files") or []
        session.add_discovered([f["path"] for f in files])
    elif result.get("tool") == "read_file":
        session.record_read(data.get("path", ""), data.get("extracted_content", ""))
    elif result.get("tool") == "write_text_file" and data.get("path"):
        session.add_output(data["path"])
    elif result.get("tool") == "delete_files" and data.get("pending_files"):
        session.mark_for_deletion(data["pending_files"])


def build_agent(agent_id: str, model: str | None = None) -> Agent:
    """Build a ready-to-use Agent for the given agent_id.

    Args:
        agent_id: folder name / id inside agent_library/
        model:    explicit model override; when empty, falls back to the
                  agent's own "model" field, then to ask_llm's resolution
                  (config/models.json > first Ollama model).

    Raises AgentNotFoundError if the definition is missing.
    """
    definition = load_definition(agent_id)
    meta = definition["meta"]

    mode = (meta.get("mode") or "chat").lower()
    tool_ids = [] if mode == "chat" else (meta.get("tools") or [])
    tools: list[Callable] = resolve_tools(tool_ids)

    profile = PromptManager.build(definition, tools)

    resolved_model = model or meta.get("model") or None
    session = get_session()
    tools = [_session_aware(fn, session) for fn in tools]
    return Agent(model=resolved_model, tools=tools, profile=profile, session=session)


def replay_history(agent: Agent, history: list[dict] | None) -> None:
    """Replay prior frontend turns ({role, content}) into the agent's history."""
    for m in (history or []):
        role = "assistant" if m.get("role") == "ai" else m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        agent.messages.append({"role": role, "content": content})


__all__ = ["build_agent", "replay_history", "AgentNotFoundError"]
