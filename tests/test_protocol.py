import pytest

from dualflow3.agents import ExecutorAgent, PlannerAgent
from dualflow3.environment import derived_path
from dualflow3.mock import OracleLLM
from dualflow3.outcomes import Outcome
from dualflow3.proposal import Proposal, epsilon
from dualflow3.protocol import CONDITIONS, run_episode
from dualflow3.tasks import SEED_TASKS, seed_tasks, with_authorization
from dualflow3.verifiers import AuthorizationPolicy, AuthorizationState, authorization_verifier, gate


def _task(task_id):
    return next(t for t in SEED_TASKS if t.task_id == task_id)


def _agents(llm):
    return PlannerAgent(llm), ExecutorAgent(llm)


def test_seed_tasks_are_well_formed():
    assert len(SEED_TASKS) == 24
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


def test_chain_step_uses_derived_file():
    t = _task("hr_chain2")
    assert t.intended[1].resource == derived_path("anonymize", t.intended[0].resource)
    ep = run_episode(t, *_agents(OracleLLM(t)), condition="B")
    assert ep.task_completed


def test_planner_error_is_measured_as_eps_p_and_executes_without_gate():
    t = _task("audit_similar_file")
    wrong = Proposal("send_external", "audit/q3_raw_report.txt", "external:auditor")
    llm = OracleLLM(t, planner_error=lambda k, o: wrong)
    ep = run_episode(t, *_agents(llm), condition="B")
    s = ep.steps[0]
    assert s.eps.eps_p and not s.eps.eps_e and s.eps.eps and s.eps.wrong_p == ("resource",)
    assert s.outcome == Outcome.UNSAFE_EXECUTION and ep.unsafe and not ep.task_completed


def test_gate_blocks_planner_error_when_restatement_is_right():
    t = _task("audit_similar_file")
    wrong = Proposal("send_external", "audit/q3_raw_report.txt", "external:auditor")
    ep = run_episode(t, *_agents(OracleLLM(t, planner_error=lambda k, o: wrong)), condition="G", n_restatements=3)
    assert ep.stop_reason == "gate_rejected" and not ep.unsafe
    assert ep.steps[0].outcome == Outcome.CORRECT_DECISION and ep.steps[0].decision.semantic.passed is False


def test_negotiation_repairs_eps_e_but_not_shared_error():
    t = _task("audit_similar_file")
    wrong = Proposal("send_external", "audit/q3_raw_report.txt", "external:auditor")
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
    llm = OracleLLM(t, executor_error=lambda k, p: Proposal("delete", p.resource, "internal"))
    ep = run_episode(t, *_agents(llm), condition="B")
    s = ep.steps[0]
    assert not s.eps.eps_p and s.eps.eps_e and s.eps.wrong_e == ("action",) and ep.unsafe


def test_authorization_configuration():
    t = with_authorization(_task("audit_similar_file"), approval_required=("send_external",))
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
    assert len(seed_tasks(domain="hr")) == 6
    assert all(t.length == 1 for t in seed_tasks(max_length=1))
    assert {t.length for t in seed_tasks(kind="chain3")} == {3}
