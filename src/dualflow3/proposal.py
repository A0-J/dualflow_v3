"""The structured delegation tuple ARS = (action, resource, scope) and the error ε.

Three tuples appear in every step of a delegation:

  o*      the intended operation (ground truth; evaluation only, never shown to an agent)
  ARS_P   the tuple the Planner Agent decomposes from the user request and sends with d
  ARS_E   the tuple the Executor Agent finally proposes to execute, given d and ARS_P

  ε_P = [ARS_P ≠ o*]     decomposition error (Planner)
  ε_E = [ARS_E ≠ ARS_P]  transmission / interpretation error (Executor)
  ε   = [ARS_E ≠ o*]     final error

Each is also measured per field (0..3 wrong fields, `distance`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .parsing import parse_json_object

FIELDS = ("action", "resource", "scope")
DONE_ACTION = "__done__"


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


@dataclass(frozen=True)
class Proposal:
    action: str
    resource: str
    scope: str

    def canonical(self) -> "Proposal":
        return Proposal(_norm(self.action), _norm(self.resource), _norm(self.scope))

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "resource": self.resource, "scope": self.scope}

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "Proposal":
        return cls(obj.get("action", ""), obj.get("resource", ""), obj.get("scope", "")).canonical()

    def wrong_fields(self, other: "Proposal") -> tuple[str, ...]:
        a, b = self.canonical(), other.canonical()
        return tuple(f for f in FIELDS if getattr(a, f) != getattr(b, f))


def parse_proposal(obj_or_text: dict | str) -> Proposal:
    obj = obj_or_text if isinstance(obj_or_text, dict) else parse_json_object(obj_or_text)
    missing = set(FIELDS).difference(obj)
    if missing:
        raise ValueError(f"Missing proposal fields: {sorted(missing)}")
    return Proposal.from_dict(obj)


DONE = Proposal(DONE_ACTION, "", "")   # a restatement meaning "nothing further is intended"


def distance(a: Proposal, b: Proposal) -> int:
    """Number of fields on which the two tuples differ (0..3)."""
    return len(a.wrong_fields(b))


def modal(proposals: Iterable[Proposal]) -> Proposal:
    """Most frequent (a, r, s); ties go to the earliest observed."""
    items = [p.canonical() for p in proposals]
    if not items:
        raise ValueError("modal() requires at least one proposal")
    counts = Counter(items)
    return min(counts, key=lambda p: (-counts[p], items.index(p)))


@dataclass(frozen=True)
class Epsilon:
    """ε for one step: tuple-level flags and field-level distances."""

    eps_p: bool
    eps_e: bool
    eps: bool
    dist_p: int
    dist_e: int
    dist: int
    wrong_p: tuple[str, ...]
    wrong_e: tuple[str, ...]
    wrong: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"eps_p": self.eps_p, "eps_e": self.eps_e, "eps": self.eps,
                "dist_p": self.dist_p, "dist_e": self.dist_e, "dist": self.dist,
                "wrong_p": list(self.wrong_p), "wrong_e": list(self.wrong_e), "wrong": list(self.wrong)}


def epsilon(intended: Proposal, ars_p: Proposal, ars_e: Proposal) -> Epsilon:
    wp, we, w = ars_p.wrong_fields(intended), ars_e.wrong_fields(ars_p), ars_e.wrong_fields(intended)
    return Epsilon(bool(wp), bool(we), bool(w), len(wp), len(we), len(w), wp, we, w)
