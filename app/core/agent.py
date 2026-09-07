"""
app/core/agent.py
=================

The reusable runtime agent.

    AgentProfile - the agent's identity and prompt sections (from config)
    Agent        - the generic think / act / observe loop

The Agent does not know what KIND of agent it is (research, coding, chat...).
Its behavior comes entirely from its AgentProfile and the tools it was given.
"""

import inspect
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List

from app.core.llm import ask_llm
from app.tools.state import FileSession


# ==========================================================================
# AGENT PROFILE
# --------------------------------------------------------------------------
# The identity and behavior of an agent. Metadata fields come from
# agent.json; the section fields come from agent.md.
# ==========================================================================

@dataclass
class AgentProfile:
    """Identity + behavior of one agent.

    From agent.json:    id, name, description, mode
    From agent.md:      role, purpose, personality, boundaries,
                        communication, principles, decision_style,
                        plus any extra '## sections' (extras)
    Composed at build:  system_prompt
    """

    id: str = ""
    name: str = ""
    description: str = ""
    mode: str = "chat"

    system_prompt: str = ""

    # Prompt sections from agent.md
    role: str = ""
    purpose: str = ""
    personality: str = ""
    boundaries: str = ""
    communication: str = ""
    principles: str = ""
    decision_style: str = ""

    # Documentation only (NOT included in the system prompt)
    priorities: str = ""

    extras: dict = field(default_factory=dict)


# ==========================================================================
# THE GENERIC AGENT OBJECT
# --------------------------------------------------------------------------
# The Agent maintains conversation history and interacts with the LLM
# backend through structured messages. When tools are attached, the LLM
# can request tool calls, which flow through act() -> observe() and a
# follow-up LLM round.
# ==========================================================================

class Agent:
    """A generic AI agent that can think (ask the LLM), act (call a tool) and
    observe (record the tool's result back into the conversation)."""

    def __init__(self, model: str | None, tools: List[Callable], profile: AgentProfile, session: FileSession | None = None):
        """Store the model, tools, profile, and optional FileSession."""
        self.model = model
        self.profile = profile
        self.tools = {f.__name__: f for f in tools}
        self.messages: List[dict] = []
        self.session = session or FileSession()

    def _extract_text_tool_calls(self, content: str) -> List[dict]:
        """Find tool calls that a model wrote as plain-text JSON instead of using
        Ollama's native tool_calls field (a common quirk of small local models).

        Accepts bare JSON, ```json fenced blocks, a JSON object or ARRAY of
        objects embedded in prose, and objects wrapped under keys like
        "tool_calls" / "calls" / "functions". ONLY names present in self.tools
        are returned, and only when the call's required arguments are present
        (so prose that merely mention a tool is never executed).

        Tool-call objects may use "arguments", "args" OR "parameters" as the
        arguments key (small models differ).
        """
        text = (content or "").strip()
        if text.startswith("```"):  # unwrap markdown code fences
            text = text.strip("`")
            if text.lower().startswith("json"):
                text = text[4:]
            text = text.strip()

        candidates: List[dict] = []
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None

        if parsed is not None:
            # Top-level array of calls, object wrapped around a list of calls,
            # or a single call object.
            if isinstance(parsed, list):
                candidates.extend(parsed)
            elif isinstance(parsed, dict):
                found = False
                for wrap_key in ("tool_calls", "calls", "functions", "call"):
                    wrapped = parsed.get(wrap_key)
                    if isinstance(wrapped, list):
                        candidates.extend(wrapped)
                        found = True
                        break
                    if isinstance(wrapped, dict):
                        candidates.append(wrapped)
                        found = True
                        break
                if not found:
                    candidates.append(parsed)
        else:
            # one nesting level allowed so nested "arguments" objects are captured
            for match in re.finditer(r"\{(?:[^{}]|\{[^{}]*\})*\}", content or ""):
                try:
                    candidates.append(json.loads(match.group(0)))
                except json.JSONDecodeError:
                    continue

        calls: List[dict] = []
        for item in candidates:
            if not isinstance(item, dict) or item.get("name") not in self.tools:
                continue
            # Accept "arguments", "args", or "parameters" as the args key.
            args = item.get("arguments", item.get("args", item.get("parameters", {}))) or {}
            args = self._normalize_args(item["name"], args)
            if not self._has_required_args(item["name"], args):
                continue
            calls.append({"function": {"name": item["name"], "arguments": args}})
        return calls

    def _has_required_args(self, name: str, args: dict) -> bool:
        """True when every required (no-default) parameter of the tool is present
        in args. Prevents executing narration that merely mentions a tool."""
        fn = self.tools.get(name)
        if fn is None:
            return False
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return True
        required = {
            p.name for p in sig.parameters.values()
            if p.default is inspect.Parameter.empty
            and p.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            )
        }
        return required.issubset(args.keys())

    def _normalize_args(self, name: str, args) -> dict:
        """Coerce the many different argument shapes small local models send for
        tool calls into a clean dict of keyword args the tool actually accepts.

        Handles:
            - args as a JSON string: '{"path": "..."}'
            - single-key wrappers:   {"args": {...}}, {"arguments": {...}}
            - positional list:       ["E:\\..."], [name, content, path]
            - string booleans:       {"overwrite": "false"} -> False
            - anything non-dict:     gracefully -> {}
        """
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, TypeError):
                return {}

        if isinstance(args, dict) and len(args) == 1:
            if "args" in args:
                args = args["args"]
            elif "arguments" in args:
                args = args["arguments"]
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    return {}

        if isinstance(args, list):
            fn = self.tools.get(name)
            if fn is not None:
                try:
                    params = [
                        p for p in inspect.signature(fn).parameters.values()
                        if p.kind in (
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        )
                    ]
                    bound = {}
                    for param, value in zip(params, args):
                        if param.name not in bound:
                            bound[param.name] = value
                    return self._coerce_bools(bound, {p.name for p in params if p.annotation is bool})
                except (TypeError, ValueError):
                    pass
            return {}

        if not isinstance(args, dict):
            return {}

        # Drop any keys that aren't actual parameters of the tool, so stray
        # keys the model invents (e.g. "path" on a no-arg tool) never crash
        # the call. Tools exposing **kwargs keep everything.
        bool_params = set()
        fn = self.tools.get(name)
        if fn is not None:
            try:
                sig = inspect.signature(fn)
                if not any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                ):
                    valid = {p.name for p in sig.parameters.values() if p.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )}
                    args = {k: v for k, v in args.items() if k in valid}
                bool_params = {
                    p.name for p in sig.parameters.values() if p.annotation is bool
                }
            except (TypeError, ValueError):
                pass

        return self._coerce_bools(args, bool_params)

    @staticmethod
    def _coerce_bools(args: dict, bool_params: set) -> dict:
        """Turn string 'true'/'false'/'1'/'0' into real bools, but ONLY for
        parameters that are actually typed as bool (so a string param like
        name="yes" is never mangled)."""
        coerced = dict(args)
        for key, value in list(coerced.items()):
            if (
                key in bool_params
                and isinstance(value, str)
                and value.strip().lower() in ("true", "false", "yes", "no", "1", "0")
            ):
                coerced[key] = value.strip().lower() in ("true", "yes", "1")
        return coerced

    MAX_TOOL_ROUNDS = 6

    def think(self, user_input: str) -> str:
        """Add user input to history, send the conversation to the LLM, and return its reply."""
        if not self.messages or self.messages[0].get("role") != "system":
            self.messages.insert(0, {"role": "system", "content": self.profile.system_prompt})

        self._inject_session_context()

        self.messages.append({"role": "user", "content": user_input})

        tool_callables = list(self.tools.values()) if self.tools else None

        message = ask_llm(messages=self.messages, model=self.model, tools=tool_callables)
        self.messages.append(message)

        # Native tool_calls, or calls the model wrote as plain-text JSON.
        # Both paths flow through act()/observe() and a follow-up LLM round.
        # Keep looping while the model keeps issuing tool calls, so a chain of
        # tool calls always ends in a real text reply (never a silent "").
        tool_calls = message.get("tool_calls") or self._extract_text_tool_calls(message.get("content", ""))
        for _ in range(self.MAX_TOOL_ROUNDS):
            if not tool_calls:
                break
            origin = "native tool_calls" if message.get("tool_calls") else "TEXT reply"
            print(f"[Agent.think] Executing {len(tool_calls)} tool call(s) from {origin}.")
            for tool_call in tool_calls:
                result = self.act(tool_call)
                self.observe(tool_call["function"]["name"], result)

            self._inject_session_context()

            message = ask_llm(messages=self.messages, model=self.model, tools=tool_callables)
            self.messages.append(message)
            tool_calls = message.get("tool_calls") or self._extract_text_tool_calls(message.get("content", ""))

        content = message.get("content", "") or ""
        if not content.strip():
            print(f"[Agent.think] No text reply after {self.MAX_TOOL_ROUNDS} tool round(s); returning fallback.")
            return "(I ran my tools but did not produce a final answer. Please ask again.)"
        return content

    _SESSION_CONTEXT_ROLE = "system"
    _SESSION_CONTEXT_PREFIX = "CURRENT FILE SESSION STATE"

    def _inject_session_context(self) -> None:
        """Add current FileSession state as context for the model, replacing any
        previously injected block so history doesn't grow duplicate state."""
        if not self.session:
            return
        state = self.session.get_state()
        if not any(state.values()):
            return
        context_entries = []
        for key, value in state.items():
            if value:
                context_entries.append(f"  {key}: {value}")
        context = f"{self._SESSION_CONTEXT_PREFIX} (from previous tool calls):\n" + "\n".join(context_entries)

        # Replace any earlier context block instead of appending another one.
        for i, message in enumerate(self.messages):
            if (
                message.get("role") == self._SESSION_CONTEXT_ROLE
                and str(message.get("content", "")).startswith(self._SESSION_CONTEXT_PREFIX)
            ):
                self.messages[i]["content"] = context
                return
        self.messages.append({"role": self._SESSION_CONTEXT_ROLE, "content": context})

    def act(self, tool_call: dict) -> str:
        """Run one tool that the LLM asked for, using the name and args it chose."""
        name = tool_call.get("function", {}).get("name")
        args = self._normalize_args(name, tool_call.get("function", {}).get("arguments", {}))
        if name in self.tools:
            try:
                result = str(self.tools[name](**args))
                print(f"[Agent.act] Executed {name} -> {result[:100]}...")
                return result
            except Exception as e:
                print(f"[Agent.act] Error executing {name}: {e}")
                return f"Error executing tool: {e}"
        print(f"[Agent.act] Missing tool requested: {name}")
        return f"Error: {name} missing"

    def observe(self, name: str, result: str) -> None:
        """Record a tool's result back into the conversation history."""
        self.messages.append({"role": "tool", "content": result, "name": name})
