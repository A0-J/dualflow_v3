"""Collect episodes: tasks × conditions × repeats, one Executor model.

  PYTHONPATH=src python experiments/run.py --dry-run                      # oracle, 0 API calls
  PYTHONPATH=src python experiments/run.py --conditions B --repeats 3     # Phase 0 pilot
  PYTHONPATH=src python experiments/run.py --conditions B G N NG --repeats 3 --max-length 1
  PYTHONPATH=src python experiments/run.py --conditions B NG --executor gpt-5.5 --kinds chain2 chain3

Output: results/<name>/episodes.jsonl (one record per episode, every LLM response kept)
        results/<name>/meta.json
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from common import (DEFAULT_EXECUTOR, MAX_ROUNDS, N_RESTATEMENTS, PLANNER_MODEL, RESULTS,  # noqa: E402
                    make_client, run_meta)

from dualflow3.agents import ExecutorAgent, PlannerAgent  # noqa: E402
from dualflow3.io import append_jsonl  # noqa: E402
from dualflow3.protocol import CONDITIONS, run_episode  # noqa: E402
from dualflow3.tasks import DOMAINS, KINDS, seed_tasks  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=None, help="results subdirectory (default: derived from arguments)")
    ap.add_argument("--conditions", nargs="+", default=["B"], choices=sorted(CONDITIONS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--domains", nargs="+", default=list(DOMAINS), choices=DOMAINS)
    ap.add_argument("--kinds", nargs="+", default=list(KINDS), choices=KINDS)
    ap.add_argument("--max-length", type=int, default=None, help="only tasks with L <= this")
    ap.add_argument("--planner", default=PLANNER_MODEL)
    ap.add_argument("--executor", default=DEFAULT_EXECUTOR)
    ap.add_argument("--n-restatements", type=int, default=N_RESTATEMENTS)
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--dry-run", action="store_true", help="oracle agents, no API calls")
    ap.add_argument("--resume", action="store_true", help="skip (task, condition, repeat) already in episodes.jsonl")
    args = ap.parse_args()

    tasks = [t for t in seed_tasks(max_length=args.max_length) if t.domain in args.domains and t.kind in args.kinds]
    name = args.name or ("dry_run" if args.dry_run else f"{'_'.join(args.conditions)}_{args.executor}")
    out = RESULTS / name
    out.mkdir(parents=True, exist_ok=True)
    episodes_path = out / "episodes.jsonl"
    done = set()
    if args.resume and episodes_path.exists():
        for line in episodes_path.read_text().splitlines():
            r = json.loads(line)
            done.add((r["task_id"], r["condition"], r["repeat"]))

    meta = run_meta(args=vars(args), n_tasks=len(tasks), conditions=args.conditions)
    (out / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"{len(tasks)} tasks × {len(args.conditions)} conditions × {args.repeats} repeats -> {episodes_path}")

    if not args.dry_run:
        planner = PlannerAgent(make_client(args.planner))
        executor = ExecutorAgent(make_client(args.executor))

    n_err = 0
    for task in tasks:
        for cond in args.conditions:
            for rep in range(args.repeats):
                if (task.task_id, cond, rep) in done:
                    continue
                if args.dry_run:
                    from dualflow3.mock import OracleLLM
                    llm = OracleLLM(task)
                    planner, executor = PlannerAgent(llm), ExecutorAgent(llm)
                try:
                    ep = run_episode(task, planner, executor, condition=cond, n_restatements=args.n_restatements,
                                     max_rounds=args.max_rounds)
                except Exception as e:   # keep collecting; the failure is recorded
                    n_err += 1
                    append_jsonl(episodes_path, {"task_id": task.task_id, "condition": cond, "repeat": rep,
                                                 "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
                    print(f"  ERROR {task.task_id} {cond} #{rep}: {e}", file=sys.stderr)
                    continue
                rec = {**ep.to_dict(), "repeat": rep, "domain": task.domain, "kind": task.kind, "length": task.length,
                       "planner_model": args.planner, "executor_model": args.executor}
                append_jsonl(episodes_path, rec)
                flag = "UNSAFE" if ep.unsafe else ("ok" if ep.task_completed else "incomplete")
                eps = "".join("P" if s.eps.eps_p else "-" for s in ep.steps) or "-"
                print(f"  {task.task_id:24s} {cond:2s} #{rep} {ep.stop_reason:13s} steps={len(ep.steps)} ε_P={eps} {flag}")
    print(f"done; {n_err} errors")


if __name__ == "__main__":
    main()
