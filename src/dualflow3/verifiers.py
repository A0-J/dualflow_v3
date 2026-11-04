"""Semantic Verifier, Authorization Verifier and the Execution Gate.

Both verifiers are components here, not the contribution. They decide on the
Executor Agent's final tuple ARS_E before anything runs:

- Semantic Verifier (V_sem): the Planner Agent restates the intended tuple N
  times from the user request and its private context -- NOT from d or
  ARS_P, so a shared error carried in d is not simply confirmed. A
  restatement may also be "done" (nothing further intended). V_sem = 1 iff
  ARS_E equals the modal restatement; a modal "done" rejects every proposal.
- Authorization Verifier (V_auth): deterministic check of ARS_E against the
  policy π and the approval state σ, both managed outside the agents.
- Gate: D = V_sem ∧ V_auth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .proposal import DONE, Proposal, modal


def safe_resource_path(resource: str) -> bool:
    return (bool(resource) and not resource.startswith("/") and "\\" not in resource
            and "\x00" not in resource
            and all(seg not in ("", ".", "..") for seg in resource.split("/")))


def resource_under_prefix(resource: str, prefix: str) -> bool:
    """Path prefixes match whole segments (a/b under a/); id prefixes ending in ':' match by start
    (pr:feature/login under pr:); anything else must match exactly."""
    if prefix.endswith(":"):
        return bool(resource) and "\x00" not in resource and resource.startswith(prefix)
    if "/" in prefix or prefix.endswith("/"):
        root = prefix.rstrip("/")
        return (safe_resource_path(resource) and safe_resource_path(root)
                and (resource == root or resource.startswith(root + "/")))
    return resource == prefix


@dataclass(frozen=True)
class SemanticVerdict:
    passed: bool
    planner_intent: Proposal


def semantic_verifier(proposal: Proposal, restatements: list[Proposal]) -> SemanticVerdict:
    intent = modal(restatements)
    if intent == DONE:                      # the Planner Agent's modal intent is "nothing further": no proposal passes
        return SemanticVerdict(False, intent)
    return SemanticVerdict(proposal.canonical() == intent, intent)


@dataclass(frozen=True)
class AuthorizationPolicy:
    """π: what this deployment may do at all."""

    allowed_actions: frozenset[str]
    resource_prefixes: tuple[str, ...]
    allowed_scopes: frozenset[str]
    approval_required: frozenset[str]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "AuthorizationPolicy":
        n = lambda s: " ".join(str(s).strip().lower().split())  # noqa: E731
        return cls(frozenset(n(a) for a in obj["allowed_actions"]),
                   tuple(n(p) for p in obj["resource_prefixes"]),
                   frozenset(n(s) for s in obj["allowed_scopes"]),
                   frozenset(n(a) for a in obj.get("approval_required", [])))

    def to_dict(self) -> dict[str, Any]:
        return {"allowed_actions": sorted(self.allowed_actions), "resource_prefixes": list(self.resource_prefixes),
                "allowed_scopes": sorted(self.allowed_scopes), "approval_required": sorted(self.approval_required)}


@dataclass(frozen=True)
class AuthorizationState:
    """σ: approvals actually granted, per exact (a, r, s)."""

    granted: frozenset[Proposal]

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> "AuthorizationState":
        return cls(frozenset(Proposal.from_dict(g) for g in obj.get("granted", [])))

    def to_dict(self) -> dict[str, Any]:
        return {"granted": [g.to_dict() for g in sorted(self.granted, key=lambda p: p.to_dict().items().__repr__())]}


@dataclass(frozen=True)
class AuthorizationVerdict:
    allowed: bool
    reason: str


def authorization_verifier(proposal: Proposal, policy: AuthorizationPolicy,
                           state: AuthorizationState) -> AuthorizationVerdict:
    p = proposal.canonical()
    if p.action not in policy.allowed_actions:
        return AuthorizationVerdict(False, "action_not_granted")
    if not any(resource_under_prefix(p.resource, pre) for pre in policy.resource_prefixes):
        return AuthorizationVerdict(False, "resource_not_granted")
    if p.scope not in policy.allowed_scopes:
        return AuthorizationVerdict(False, "scope_not_granted")
    if p.action in policy.approval_required and p not in state.granted:
        return AuthorizationVerdict(False, "approval_not_granted")
    return AuthorizationVerdict(True, "authorized")


@dataclass(frozen=True)
class Decision:
    execute: bool
    semantic: SemanticVerdict | None      # None when the gate is off (no restatements made)
    authorization: AuthorizationVerdict | None

    def to_dict(self) -> dict[str, Any]:
        return {"execute": self.execute,
                "v_sem": None if self.semantic is None else self.semantic.passed,
                "p_intent": None if self.semantic is None else self.semantic.planner_intent.to_dict(),
                "v_auth": None if self.authorization is None else self.authorization.allowed,
                "auth_reason": None if self.authorization is None else self.authorization.reason}


def gate(proposal: Proposal, restatements: list[Proposal] | None,
         policy: AuthorizationPolicy, state: AuthorizationState) -> Decision:
    """With restatements: D = V_sem ∧ V_auth. Without: no gate, always execute."""
    if restatements is None:
        return Decision(True, None, None)
    sem = semantic_verifier(proposal, restatements)
    auth = authorization_verifier(proposal, policy, state)
    return Decision(sem.passed and auth.allowed, sem, auth)
