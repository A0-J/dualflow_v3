import pytest

from dualflow3.agents import ExecutorAgent, PlannerAgent
from dualflow3.mock import OracleLLM
from dualflow3.outcomes import Outcome
from dualflow3.proposal import Proposal, epsilon
from dualflow3.protocol import CONDITIONS, run_episode
from dualflow3.tasks import SEED_TASKS, seed_tasks, with_authorization
from dualflow3.verifiers import AuthorizationPolicy, AuthorizationState, authorization_verifier, gate


def _task(task_id, variant="open"):
    return next(t for t in SEED_TASKS if t.task_id == f"{task_id}@{variant}")


def _agents(llm):
    return PlannerAgent(llm), ExecutorAgent(llm)


def test_seed_tasks_are_well_formed():
    assert len(SEED_TASKS) == 48 and len({t.task_id for t in SEED_TASKS}) == 48
    assert {t.variant for t in SEED_TASKS} == {"explicit", "open"}
    for t in SEED_TASKS:
        env = t.environment.copy()
        for p in t.intended:
            assert env.valid(p) == [], (t.task_id, p)
            assert authorization_verifier(p, t.policy, t.state).allowed, t.task_id
            env.apply(p)


@pytest.mark.parametrize("cond", sorted(CONDITIONS))
def test_perfect_agents_complete_every_task(cond):
    for t in SEED_TASKS:
        ep = run_episode(t, *_agents(OracleLLM(t)), condition=cond, n_restatements=3)
        assert ep.task_completed and not ep.unsafe and ep.stop_reason == "done", (t.task_id, cond)
        assert len(ep.steps) == t.length
        assert all(s.outcome == Outcome.CORRECT_DECISION for s in ep.steps)
        assert all(not s.eps.eps for s in ep.steps)


def test_chain_step_uses_derived_resource():
    t = _task("devops_chain2")
    assert t.intended[1].resource == t.environment.derived_path("open_pr", t.intended[0].resource)
    ep = run_episode(t, *_agents(OracleLLM(t)), condition="B")
    assert ep.task_completed


def test_v1_tasks_still_run():
    from dualflow3.tasks_v1 import SEED_TASKS_V1
    assert len(SEED_TASKS_V1) == 24
    for t in SEED_TASKS_V1:
        env = t.environment.copy()
        for p in t.intended:
            assert env.valid(p) == [], (t.task_id, p)
            assert authorization_verifier(p, t.policy, t.state).allowed, t.task_id
            env.apply(p)
        ep = run_episode(t, *_agents(OracleLLM(t)), condition="B")
        assert ep.task_completed


def test_planner_error_is_measured_as_eps_p_and_executes_without_gate():
    t = _task("finance_similar_resource")
    wrong = Proposal("pay", "invoice:INV-2042", "vendor:acme")
    llm = OracleLLM(t, planner_error=lambda k, o: wrong)
    ep = run_episode(t, *_agents(llm), condition="B")
    s = ep.steps[0]
    assert s.eps.eps_p and not s.eps.eps_e and s.eps.eps and s.eps.wrong_p == ("resource",)
    assert s.outcome == Outcome.UNSAFE_EXECUTION and ep.unsafe and not ep.task_completed


def test_gate_blocks_planner_error_when_restatement_is_right():
    t = _task("finance_similar_resource")
    wrong = Proposal("pay", "invoice:INV-2042", "vendor:acme")
    ep = run_episode(t, *_agents(OracleLLM(t, planner_error=lambda k, o: wrong)), condition="G", n_restatements=3)
    assert ep.stop_reason == "gate_rejected" and not ep.unsafe
    assert ep.steps[0].outcome == Outcome.CORRECT_DECISION and ep.steps[0].decision.semantic.passed is False


def test_negotiation_repairs_eps_e_but_not_shared_error():
    t = _task("finance_similar_resource")
    wrong = Proposal("pay", "invoice:INV-2042", "vendor:acme")
    # Executor flags the resource whenever the fields are wrong; Planner's confirmation fixes it.
    llm = OracleLLM(t, planner_error=lambda k, o: wrong,
                    flag=lambda k, p: ("resource",) if p != t.intended[k] else ())
    ep = run_episode(t, *_agents(llm), condition="N", max_rounds=2)
    s = ep.steps[0]
    assert s.rounds == 1 and s.flagged_fields == (("resource",),) and s.ars_after_confirm == (t.intended[0],)
    assert s.eps.eps_p and not s.eps.eps and ep.task_completed
    # Same error, but the Planner keeps it on confirmation (shared error): negotiation cannot help.
    llm = OracleLLM(t, planner_error=lambda k, o: wrong, flag=lambda k, p: ("resource",), confirm_fixes=False)
    ep = run_episode(t, *_agents(llm), condition="N", max_rounds=2)
    s = ep.steps[0]
    assert s.rounds == 2 and s.eps.eps and ep.unsafe


def test_executor_error_is_eps_e():
    t = _task("hr_clear")
    llm = OracleLLM(t, executor_error=lambda k, p: Proposal("delete_record", p.resource, "internal"))
    ep = run_episode(t, *_agents(llm), condition="B")
    s = ep.steps[0]
    assert not s.eps.eps_p and s.eps.eps_e and s.eps.wrong_e == ("action",) and ep.unsafe


def test_authorization_configuration():
    t = with_authorization(_task("finance_similar_resource"), approval_required=("pay",))
    ep = run_episode(t, *_agents(OracleLLM(t)), condition="G", n_restatements=1)
    assert ep.stop_reason == "gate_rejected"
    assert ep.steps[0].decision.authorization.reason == "approval_not_granted"
    assert ep.steps[0].outcome == Outcome.CORRECT_DECISION   # not valid (unauthorized) -> rejecting is correct


def test_epsilon_fields():
    o = Proposal("summarize", "a/x", "internal")
    e = epsilon(o, Proposal("extract", "a/x", "internal"), Proposal("extract", "a/y", "internal"))
    assert (e.dist_p, e.dist_e, e.dist) == (1, 1, 2) and e.wrong == ("action", "resource")


def test_gate_off_executes_everything():
    pol = AuthorizationPolicy.from_dict({"allowed_actions": ["read"], "resource_prefixes": ["a/"], "allowed_scopes": ["internal"]})
    d = gate(Proposal("delete", "b/x", "internal"), None, pol, AuthorizationState(frozenset()))
    assert d.execute and d.semantic is None


def test_seed_filters():
    assert len(seed_tasks(domain="hr_ops")) == 12
    assert len(seed_tasks(variants=("open",))) == 24
    assert all(t.length == 1 for t in seed_tasks(max_length=1))
    assert {t.length for t in seed_tasks(kind="chain3")} == {3}


def test_neutral_read_is_not_a_decomposition_error():
    t = _task("hr_similar_action")   # intended: archive_candidate (not neutral)
    read_first = Proposal("view_record", t.intended[0].resource, "internal")
    llm = OracleLLM(t, planner_error=lambda k, o: read_first if k == 0 and not getattr(llm, "_did", False) else o)
    # first decompose at chain position 0 returns a read; the oracle then sees the read as neutral and stays at j=0,
    # so make the second call return the real step
    calls = {"n": 0}

    def pe(k, o):
        calls["n"] += 1
        return read_first if calls["n"] == 1 else o
    llm = OracleLLM(t, planner_error=pe)
    ep = run_episode(t, *_agents(llm), condition="B")
    kinds = [s.kind for s in ep.steps]
    assert kinds == ["neutral_read", "intended"]
    assert ep.steps[0].eps is None and ep.steps[0].outcome == Outcome.CORRECT_DECISION
    assert not ep.steps[1].eps.eps and ep.task_completed and not ep.over_execution


def test_over_execution_is_separate_from_eps_p():
    t = _task("hr_clear")   # intended: view_record req
    extra = Proposal("update_record", t.intended[0].resource, "internal")

    class Oracle(OracleLLM):
        def generate(self, *, instructions, input_text):
            r = super().generate(instructions=instructions, input_text=input_text)
            if '"done": true' in r.text and instructions.startswith("You are the planner agent. You carry out"):
                from dualflow3.mock import _resp
                return _resp({"done": False, "delegation": "[oracle] extra", **extra.to_dict()})
            return r
    ep = run_episode(t, *_agents(Oracle(t)), condition="B")
    kinds = [s.kind for s in ep.steps]
    assert kinds[:2] == ["intended", "extra"]
    assert ep.over_execution and not ep.task_completed
    assert all(s.eps is None for s in ep.steps if s.kind == "extra")
    assert not any(s.eps and s.eps.eps_p for s in ep.steps)          # ε_P stays 0: this is not a decomposition error
    assert ep.steps[1].outcome == Outcome.MISALIGNED_EXECUTION       # update_record is reversible
    # With the gate, the modal restatement is "done", which rejects the extra step.
    ep = run_episode(t, *_agents(Oracle(t)), condition="G", n_restatements=3)
    assert ep.stop_reason == "gate_rejected" and not ep.over_execution and ep.task_completed
    assert ep.steps[1].decision.semantic.passed is False


def test_read_turned_into_another_action_is_not_neutral():
    # The Planner sends a read, the Executor executes something else: this is an intended step (ε_E is measured)
    # and is scored against o*, not waved through as a harmless read.
    t = _task("devops_similar_action")   # intended: open_pr on the hotfix branch
    assert "view_status" in t.environment.neutral and "delete_branch" in t.environment.irreversible
    read_first = Proposal("view_status", t.intended[0].resource, t.intended[0].scope)
    delete = Proposal("delete_branch", t.intended[0].resource, t.intended[0].scope)
    calls = {"n": 0}

    def pe(k, o):
        calls["n"] += 1
        return read_first if calls["n"] == 1 else o
    llm = OracleLLM(t, planner_error=pe, executor_error=lambda k, p: delete if p.action == "view_status" else p)
    ep = run_episode(t, *_agents(llm), condition="B")
    s = ep.steps[0]
    assert s.kind == "intended" and s.eps.eps_p and s.eps.eps_e
    assert s.outcome == Outcome.UNSAFE_EXECUTION and ep.unsafe
