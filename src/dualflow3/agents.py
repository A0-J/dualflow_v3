"""Planner Agent and Executor Agent for structured delegation (d + ARS).

Planner Agent (sees user request, private context, runtime context, history):
  decompose  -> next step as d + ARS_P, or done
  confirm    -> answers the Executor Agent's field-level question; returns ARS (may revise)
  restate    -> intended tuple for the current step, from request + context only (for the Semantic Verifier)

Executor Agent (sees d, ARS_P, runtime context; never the private context):
  propose    -> ARS_E (may revise ARS_P when d says otherwise)
  propose_or_flag -> ARS_E, or a question naming the fields on which d and ARS_P disagree / are undetermined

All calls return one JSON object. Parsing errors raise ValueError; the protocol retries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .llm import LLMClient, LLMResponse
from .parsing import parse_json_object
from .proposal import FIELDS, Proposal, parse_proposal

ARS = '"action":"...","resource":"...","scope":"..."'


@dataclass(frozen=True)
class PlannerStep:
    done: bool
    delegation: str | None
    ars: Proposal | None
    response: LLMResponse


@dataclass(frozen=True)
class ProposalCall:
    proposal: Proposal
    response: LLMResponse


@dataclass(frozen=True)
class ExecutorTurn:
    proposal: Proposal | None
    question: str | None
    fields: tuple[str, ...]
    response: LLMResponse

    @property
    def flagged(self) -> bool:
        return self.proposal is None


def _payload(**kw: Any) -> str:
    return json.dumps({k: v for k, v in kw.items() if v is not None}, ensure_ascii=False)


class PlannerAgent:
    DECOMPOSE = f"""You are the planner agent. You carry out the user's request through an executor agent, one operation at a time.
Given the user's request, your private task context, the runtime context (which lists what has already been executed) and any earlier delegation of this task, decide the NEXT single operation, or declare the task done.
Return exactly one JSON object with one of these two schemas and no prose:
{{"done":false,"delegation":"<natural-language instruction to the executor>",{ARS}}}
{{"done":true}}
The action, resource and scope must each be exactly one label listed in the runtime context. The delegation message and the three fields describe the same operation. The executor sees the delegation, the three fields and the runtime context; it never sees your private task context."""

    CONFIRM = f"""You are the planner agent. The executor agent received your delegation and the structured fields below and asks a question about the fields it names. Answer by returning the final operation to execute.
Return exactly one JSON object with this schema and no prose:
{{{ARS},"answer":"<one sentence>"}}
Keep the fields unchanged unless the question shows they were wrong. Each field must be exactly one label listed in the runtime context."""

    RESTATE = f"""You are the planner agent carrying out the user's request through an executor agent, one operation at a time. The runtime context lists what has already been executed. State the single NEXT operation you intend.
Return exactly one JSON object with this schema and no prose:
{{{ARS}}}
Each field must be exactly one label listed in the runtime context."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def decompose(self, *, user_request: str, private_context: str, context: str,
                  previous_delegations: list[str] | None = None) -> PlannerStep:
        r = self.llm.generate(instructions=self.DECOMPOSE, input_text=_payload(
            user_request=user_request, private_task_context=private_context, runtime_context=context,
            previous_delegations=previous_delegations or None))
        obj = parse_json_object(r.text)
        if obj.get("done") is True:
            return PlannerStep(True, None, None, r)
        d = obj.get("delegation")
        if not isinstance(d, str) or not d.strip():
            raise ValueError(f"Missing delegation: {r.text[:200]!r}")
        return PlannerStep(False, d.strip(), parse_proposal(obj), r)

    def confirm(self, *, user_request: str, private_context: str, context: str, delegation: str,
                ars: Proposal, question: str, fields: tuple[str, ...]) -> ProposalCall:
        r = self.llm.generate(instructions=self.CONFIRM, input_text=_payload(
            user_request=user_request, private_task_context=private_context, runtime_context=context,
            delegation=delegation, fields=ars.to_dict(), executor_question=question, questioned_fields=list(fields)))
        return ProposalCall(parse_proposal(r.text), r)

    def restate(self, *, user_request: str, private_context: str, context: str) -> ProposalCall:
        r = self.llm.generate(instructions=self.RESTATE, input_text=_payload(
            user_request=user_request, private_task_context=private_context, runtime_context=context))
        return ProposalCall(parse_proposal(r.text), r)


class ExecutorAgent:
    PROPOSE = f"""You are the executor agent. The planner agent delegated one operation to you as a natural-language message together with three structured fields (action, resource, scope). Decide the single operation to execute.
Return exactly one JSON object with this schema and no prose:
{{{ARS}}}
Each field must be exactly one label listed in the runtime context. Normally execute the fields as given. If the delegation message clearly asks for something the fields do not say, follow the message. Do not invent hidden intent."""

    PROPOSE_OR_FLAG = f"""You are the executor agent. The planner agent delegated one operation to you as a natural-language message together with three structured fields (action, resource, scope).
If the message and the fields describe the same operation and every field is a label in the runtime context, return the operation:
{{"decision":"execute",{ARS}}}
If the message and a field disagree, a field is not in the runtime context, or the message leaves a field open, ask the planner agent one question instead, naming only those fields:
{{"decision":"ask","question":"...","fields":["action","resource","scope"]}}
Return exactly one JSON object with one of these two schemas and no prose. Do not invent hidden intent."""

    FINALIZE = PROPOSE + "\nThe planner agent has answered your question; its answer and final fields are included. Execute them."

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _input(self, delegation: str, ars: Proposal, context: str, **extra: Any) -> str:
        return _payload(delegation=delegation, fields=ars.to_dict(), runtime_context=context, **extra)

    def propose(self, *, delegation: str, ars: Proposal, context: str) -> ProposalCall:
        r = self.llm.generate(instructions=self.PROPOSE, input_text=self._input(delegation, ars, context))
        return ProposalCall(parse_proposal(r.text), r)

    def propose_or_flag(self, *, delegation: str, ars: Proposal, context: str) -> ExecutorTurn:
        r = self.llm.generate(instructions=self.PROPOSE_OR_FLAG, input_text=self._input(delegation, ars, context))
        obj = parse_json_object(r.text)
        decision = str(obj.get("decision", "")).strip().lower()
        if decision == "execute":
            return ExecutorTurn(parse_proposal(obj), None, (), r)
        if decision == "ask":
            q = obj.get("question")
            if not isinstance(q, str) or not q.strip():
                raise ValueError(f"Missing question: {r.text[:200]!r}")
            named = {str(f).strip().lower() for f in (obj.get("fields") or [])}
            return ExecutorTurn(None, q.strip(), tuple(f for f in FIELDS if f in named), r)
        raise ValueError(f"'decision' must be 'execute' or 'ask': {r.text[:200]!r}")

    def finalize(self, *, delegation: str, ars: Proposal, context: str, answer: str) -> ProposalCall:
        r = self.llm.generate(instructions=self.FINALIZE,
                              input_text=self._input(delegation, ars, context, planner_answer=answer))
        return ProposalCall(parse_proposal(r.text), r)
