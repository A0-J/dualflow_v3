"""The runtime environment: a domain's candidate space of actions, resources and scopes.

A domain defines
  actions    name -> ActionSpec(description, irreversible, neutral, derives)
             `derives` is a prefix: executing (a, r, s) creates resource `<derives><r>`
             (a PR from a branch, an anonymized copy of a file, a reservation from a flight)
  resources  id -> description, with look-alikes so that decomposition can go wrong
  scopes     label -> description; the scope field of ARS must be one of these

`Environment` is the per-task instance: it tracks what has been executed and
what derived resources now exist, and renders the runtime context text that
both agents see.

Domains are defined in domains.py, grounded in the scenario types that recent
agent benchmarks use (TheAgentCompany, Toolathlon, τ/τ²-bench, EntCollabBench):
software delivery, travel booking, HR operations and finance operations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .proposal import Proposal


@dataclass(frozen=True)
class ActionSpec:
    description: str
    irreversible: bool = False
    neutral: bool = False          # read-only: a harmless preparatory step, never a decomposition error
    derives: str | None = None     # prefix of the resource this action creates, if any


@dataclass(frozen=True)
class DomainSpec:
    name: str
    actions: dict[str, ActionSpec]
    resources: dict[str, str]
    scopes: dict[str, str]
    scope_note: str                # one sentence telling the agents how to fill the scope field

    @property
    def irreversible(self) -> frozenset[str]:
        return frozenset(a for a, s in self.actions.items() if s.irreversible)

    @property
    def neutral(self) -> frozenset[str]:
        return frozenset(a for a, s in self.actions.items() if s.neutral)

    @property
    def derived_prefixes(self) -> tuple[str, ...]:
        return tuple(sorted({s.derives for s in self.actions.values() if s.derives}))


@dataclass
class Environment:
    domain: DomainSpec
    resources: dict[str, str]                  # live resources (initial + derived − deleted)
    executed: list[Proposal] = field(default_factory=list)

    @classmethod
    def of(cls, domain: DomainSpec) -> "Environment":
        return cls(domain, dict(domain.resources))

    def copy(self) -> "Environment":
        return Environment(self.domain, dict(self.resources), list(self.executed))

    @property
    def irreversible(self) -> frozenset[str]:
        return self.domain.irreversible

    @property
    def neutral(self) -> frozenset[str]:
        return self.domain.neutral

    def scopes(self) -> list[str]:
        return list(self.domain.scopes)

    def _key(self, resource: str) -> str | None:
        """The stored id matching a canonical (lower-cased) resource, or None."""
        low = resource.lower()
        return next((r for r in self.resources if r.lower() == low), None)

    def derived_path(self, action: str, resource: str) -> str | None:
        spec = self.domain.actions.get(action)
        return f"{spec.derives}{resource}" if spec and spec.derives else None

    def apply(self, p: Proposal) -> str | None:
        """Record an executed operation; add its derived resource, if any. Returns the new id."""
        p = p.canonical()
        self.executed.append(p)
        key = self._key(p.resource)
        out = self.derived_path(p.action, key) if key else None
        if out:
            self.resources[out] = f"{p.action} of {self.resources[key]}"
        if p.action in ("delete", "delete_record") and key:   # a cancelled reservation remains a record
            self.resources.pop(key, None)
        return out

    def render(self) -> str:
        """The runtime context text given to both agents."""
        acts = "\n".join(f"- {a}: {s.description}" for a, s in self.domain.actions.items())
        res = "\n".join(f"- {r}: {d}" for r, d in self.resources.items())
        scopes = "\n".join(f"- {s}: {d}" for s, d in self.domain.scopes.items())
        done = ("\n\nAlready executed in this task (in order):\n" +
                "\n".join(f"{i + 1}. {e.action} {e.resource} ({e.scope})" for i, e in enumerate(self.executed))
                if self.executed else "")
        first_a = next(a for a, s in self.domain.actions.items() if s.neutral)
        first_r = next(iter(self.resources), "resource")
        first_s = next(iter(self.domain.scopes))
        return (f"Actions (use exactly one of these words):\n{acts}\n\n"
                f"Resources (resource must be one of these ids):\n{res}\n\n"
                f"Scope: {self.domain.scope_note}\nScope labels:\n{scopes}{done}\n\n"
                f'Example of a correctly formatted operation: {{"action": "{first_a}", "resource": "{first_r}", "scope": "{first_s}"}}')

    def valid(self, p: Proposal) -> list[str]:
        """Fields of p that name something not in this environment."""
        p = p.canonical()
        bad = []
        if p.action not in self.domain.actions:
            bad.append("action")
        if self._key(p.resource) is None:
            bad.append("resource")
        if p.scope not in {sc.lower() for sc in self.domain.scopes}:
            bad.append("scope")
        return bad

    def to_dict(self) -> dict[str, Any]:
        return {"domain": self.domain.name, "resources": dict(self.resources),
                "executed": [e.to_dict() for e in self.executed]}
