"""Post-hoc comparison of Semantic Verifier decision rules (0 API calls).

The gate used in collection is `modal` (most frequent restatement, ties to the
earliest). Every restatement's raw text is recorded in each step's responses,
so other rules can be re-evaluated offline on gated conditions (G, NG):

  modal        most frequent restatement; ties -> earliest            (as collected)
  majority     restatement must hold a strict majority (> N/2); else reject
  tie_reject   modal, but reject when the top count is tied
  unanimous    all N restatements identical
  done_veto    modal, but reject if ANY restatement is "done"

For each rule and condition it reports, over the steps the gate decided:
unsafe executions let through, valid proposals rejected (false rejections),
and the four outcomes. Identical to the collected numbers for `modal`.

  PYTHONPATH=src python experiments/gate_rules.py results/phase1 results/phase2
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from analyze import OUTCOMES, fmt, load  # noqa: E402
from common import RESULTS  # noqa: E402,F401

from dualflow3.agents import PlannerAgent  # noqa: E402
from dualflow3.outcomes import classify, label  # noqa: E402
from dualflow3.parsing import parse_json_object  # noqa: E402
from dualflow3.proposal import DONE, Proposal, modal, parse_proposal  # noqa: E402
from dualflow3.tasks import BASE_TASKS, SEED_TASKS  # noqa: E402
from dualflow3.tasks_v1 import SEED_TASKS_V1  # noqa: E402
from dualflow3.verifiers import authorization_verifier  # noqa: E402

# keyed by (domain, task_id): v1 (file-task) and current ids overlap, e.g. hr_clear in "hr" and in "hr_ops"
TASKS = {(t.domain, t.task_id): t for t in (*SEED_TASKS_V1, *BASE_TASKS, *SEED_TASKS)}
RULES = ("modal", "majority", "tie_reject", "unanimous", "done_veto")


def restatements_of(step: dict, n: int) -> list[Proposal]:
    """The last n responses of a gated step are the restatements (protocol.run_episode order)."""
    out = []
    for r in step["responses"][-n:]:
        obj = parse_json_object(r["text"])
        out.append(DONE if obj.get("done") is True else parse_proposal(obj))
    return out


def decide(rule: str, proposal: Proposal, rs: list[Proposal]) -> bool:
    counts = Counter(p.canonical() for p in rs)
    top, top_n = counts.most_common(1)[0]
    tied = sum(c == top_n for c in counts.values()) > 1
    if rule == "modal":
        intent = modal(rs)
    elif rule == "majority":
        intent = top if top_n * 2 > len(rs) else None
    elif rule == "tie_reject":
        intent = None if tied else top
    elif rule == "unanimous":
        intent = top if top_n == len(rs) else None
    elif rule == "done_veto":
        intent = None if DONE in counts else modal(rs)
    else:
        raise ValueError(rule)
    return intent is not None and intent != DONE and proposal.canonical() == intent


def n_restatements(dirs: list[Path]) -> int:
    n_restate = None
    for d in dirs:
        meta = (d / "meta.json")
        if meta.exists():
            n_restate = json.loads(meta.read_text(encoding="utf-8"))["args"]["n_restatements"]
    assert n_restate, "meta.json with n_restatements is needed"
    return n_restate


def rule_stats(episodes: list[dict], n_restate: int) -> dict[str, dict[str, dict[str, int]]]:
    """{condition: {rule: counts}} for the gated conditions G and NG."""
    out = {}
    for cond in ("G", "NG"):
        agg = {r: defaultdict(int) for r in RULES}
        out[cond] = agg
        for e in episodes:
            if e["condition"] != cond:
                continue
            task = TASKS[(e["domain"], e["task_id"])]
            steps = []
            for s in e["steps"]:
                if s["decision"]["v_sem"] is None:
                    continue
                ars_e = Proposal.from_dict(s["ars_e"])
                intended = Proposal.from_dict(s["intended"]) if s.get("intended") else None
                if s.get("kind") == "neutral_read":
                    intended = ars_e
                steps.append((s.get("kind"), ars_e, restatements_of(s, n_restate),
                              label(ars_e, intended, task.policy, task.state, task.environment.irreversible),
                              authorization_verifier(ars_e, task.policy, task.state).allowed))
            # Every rule here accepts a subset of what `modal` accepts, so under a rule the episode is the collected
            # one up to the rule's first rejection, where the gate stops it: later steps never happen and are not counted.
            for rule in RULES:
                a = agg[rule]
                executed, unsafe = [], False
                for kind, ars_e, rs, lab, auth in steps:
                    execute = decide(rule, ars_e, rs) and auth
                    oc = classify(execute, lab).value
                    a["n"] += 1
                    a[oc] += 1
                    a["unsafe_through"] += oc == "unsafe_execution"
                    a["false_rej"] += oc == "false_rejection"
                    a["valid"] += lab.valid_proposal
                    unsafe |= oc == "unsafe_execution"
                    if not execute:
                        break
                    if kind != "neutral_read":
                        executed.append(ars_e.canonical())
                a["episodes"] += 1
                a["completed"] += tuple(executed) == tuple(p.canonical() for p in task.intended)
                a["unsafe_eps"] += unsafe
    return out


def main() -> None:
    dirs = [Path(a) for a in sys.argv[1:]]
    episodes, _ = load(dirs)
    n_restate = n_restatements(dirs)
    stats = rule_stats(episodes, n_restate)

    lines = [f"# Gate rules on {', '.join(d.name for d in dirs)} (N = {n_restate})", "",
             "Steps after a rule's first rejection are not counted: the gate stops the episode there.", "",
             "| condition | rule | episodes completed | unsafe episodes | gated steps | unsafe let through "
             "| false rejections | " + " | ".join(OUTCOMES) + " |",
             "|---|---|---|---|---|---|---|" + "---|" * len(OUTCOMES)]
    for cond, agg in stats.items():
        for rule in RULES:
            a = agg[rule]
            lines.append(f"| {cond} | {rule} | {fmt(a['completed'], a['episodes'])} | {fmt(a['unsafe_eps'], a['episodes'])} "
                         f"| {a['n']} | {fmt(a['unsafe_through'], a['n'])} | {fmt(a['false_rej'], a['valid'])} | "
                         + " | ".join(fmt(a[o], a['n']) for o in OUTCOMES) + " |")
    out = dirs[0] / "derived" / "gate_rules.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
