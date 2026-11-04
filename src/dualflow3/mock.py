"""LLM stand-ins for dry runs and tests. No API calls.

OracleLLM answers every agent call from the task's ground truth, so the
whole pipeline runs end to end offline. Optional `perturb` hooks inject
errors to exercise the measurement:

  planner_error(k, intended) -> Proposal   what ARS_P the Planner sends at step k (default: o*_k)
  executor_error(k, ars_p)   -> Proposal   what ARS_E the Executor proposes (default: ARS_P)
  flag(k, ars_p)             -> fields     which fields the Executor flags under negotiation (default: none)
  confirm_fixes: bool                      whether the Planner's confirmation returns o*_k (True) or keeps ARS_P

ScriptedLLM replays a fixed list of responses, for exact tests.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Sequence

from .llm import LLMResponse
from .proposal import Proposal
from .tasks import Task

_EXECUTED_RE = re.compile(r"^\d+\. ", re.MULTILINE)


def _resp(obj: dict) -> LLMResponse:
    return LLMResponse(json.dumps(obj), "oracle", "oracle", 0, 0, 0, 0.0, None, None)


def _steps_done(payload: dict) -> int:
    ctx = payload.get("runtime_context", "")
    tail = ctx.split("Already executed in this task", 1)
    return len(_EXECUTED_RE.findall(tail[1])) if len(tail) == 2 else 0


class OracleLLM:
    def __init__(self, task: Task, *,
                 planner_error: Callable[[int, Proposal], Proposal] | None = None,
                 executor_error: Callable[[int, Proposal], Proposal] | None = None,
                 flag: Callable[[int, Proposal], Sequence[str]] | None = None,
                 confirm_fixes: bool = True) -> None:
        self.task = task
        self.planner_error = planner_error or (lambda k, o: o)
        self.executor_error = executor_error or (lambda k, p: p)
        self.flag = flag or (lambda k, p: ())
        self.confirm_fixes = confirm_fixes
        self.calls: list[tuple[str, dict]] = []

    def generate(self, *, instructions: str, input_text: str) -> LLMResponse:
        payload = json.loads(input_text)
        k = _steps_done(payload)
        role = instructions.split("\n", 1)[0]
        self.calls.append((role, payload))
        intended = self.task.intended[k] if k < self.task.length else None

        if instructions.startswith("You are the planner agent. You carry out"):            # decompose
            if intended is None:
                return _resp({"done": True})
            ars = self.planner_error(k, intended)
            return _resp({"done": False, "delegation": f"[oracle] step {k + 1}: {ars.action} {ars.resource}",
                          **ars.to_dict()})
        if instructions.startswith("You are the planner agent. The executor agent received"):   # confirm
            ars = intended if (self.confirm_fixes and intended is not None) else Proposal.from_dict(payload["fields"])
            return _resp({**ars.to_dict(), "answer": "[oracle] confirmed"})
        if instructions.startswith("You are the planner agent carrying out"):                # restate
            return _resp((intended or Proposal("read", "", "internal")).to_dict())
        if instructions.startswith("You are the executor agent"):
            ars_p = Proposal.from_dict(payload["fields"])
            if "decision" in instructions and "planner_answer" not in payload:               # propose_or_flag
                fields = list(self.flag(k, ars_p))
                if fields:
                    return _resp({"decision": "ask", "question": "[oracle] which?", "fields": fields})
                return _resp({"decision": "execute", **self.executor_error(k, ars_p).to_dict()})
            return _resp(self.executor_error(k, ars_p).to_dict())                           # propose / finalize
        raise ValueError(f"OracleLLM: unknown instructions: {role!r}")


class ScriptedLLM:
    def __init__(self, texts: Sequence[str]) -> None:
        self.texts = list(texts)
        self.i = 0

    def generate(self, *, instructions: str, input_text: str) -> LLMResponse:
        if self.i >= len(self.texts):
            raise AssertionError("ScriptedLLM: no more responses")
        t = self.texts[self.i]
        self.i += 1
        return LLMResponse(t, "scripted", "scripted", 0, 0, 0, 0.0, None, None)
