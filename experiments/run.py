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
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from common import (DEFAULT_EXECUTOR, MAX_ROUNDS, N_RESTATEMENTS, PLANNER_MODEL, RESULTS,  # noqa: E402
                    make_client, run_meta)

from dualflow3.agents import ExecutorAgent, PlannerAgent  # noqa: E402
from dualflow3.io import append_jsonl  # noqa: E402
from dualflow3.protocol import CONDITIONS, run_episode  # noqa: E402
from dualflow3.tasks import DOMAINS, KINDS, VARIANTS, seed_tasks  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=None, help="results subdirectory (default: derived from arguments)")
    ap.add_argument("--conditions", nargs="+", default=["B"], choices=sorted(CONDITIONS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--domains", nargs="+", default=list(DOMAINS), choices=DOMAINS)
    ap.add_argument("--kinds", nargs="+", default=list(KINDS), choices=KINDS)
    ap.add_argument("--max-length", type=int, default=None, help="only tasks with L <= this")
    ap.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=VARIANTS,
                    help="request phrasing: explicit (steps and stopping point named) / open (goal only)")
    ap.add_argument("--planner", default=PLANNER_MODEL)
    ap.add_argument("--executor", default=DEFAULT_EXECUTOR)
    ap.add_argument("--n-restatements", type=int, default=N_RESTATEMENTS)
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--dry-run", action="store_true", help="oracle agents, no API calls")
    ap.add_argument("--resume", action="store_true",
                    help="skip (task, condition, repeat) already completed in episodes.jsonl; errored ones run again")
    ap.add_argument("--workers", type=int, default=1, help="episodes run concurrently (threads)")
    args = ap.parse_args()

    tasks = [t for t in seed_tasks(max_length=args.max_length, variants=tuple(args.variants))
             if t.domain in args.domains and t.kind in args.kinds]
    name = args.name or ("dry_run" if args.dry_run else f"{'_'.join(args.conditions)}_{args.executor}")
    out = RESULTS / name
    out.mkdir(parents=True, exist_ok=True)
    episodes_path = out / "episodes.jsonl"
    done = set()
    if args.resume and episodes_path.exists():
        for line in episodes_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if "error" not in r:   # errored episodes are run again
                done.add((r["task_id"], r["condition"], r["repeat"]))

    meta = run_meta(args=vars(args), n_tasks=len(tasks), conditions=args.conditions)
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"{len(tasks)} tasks × {len(args.conditions)} conditions × {args.repeats} repeats -> {episodes_path}")

    if not args.dry_run:   # the OpenAI client is thread-safe; one per model is shared by all workers
        planner_llm, executor_llm = make_client(args.planner), make_client(args.executor)

    jobs = [(task, cond, rep) for task in tasks for cond in args.conditions for rep in range(args.repeats)
            if (task.task_id, cond, rep) not in done]
    lock = threading.Lock()
    counts = {"done": 0, "err": 0}

    def run_one(task, cond: str, rep: int) -> None:
        if args.dry_run:
            from dualflow3.mock import OracleLLM
            llm = OracleLLM(task)
            planner, executor = PlannerAgent(llm), ExecutorAgent(llm)
        else:
            planner, executor = PlannerAgent(planner_llm), ExecutorAgent(executor_llm)
        try:
            ep = run_episode(task, planner, executor, condition=cond, n_restatements=args.n_restatements,
                             max_rounds=args.max_rounds)
        except Exception as e:   # keep collecting; the failure is recorded (and re-run by --resume)
            with lock:
                counts["done"] += 1
                counts["err"] += 1
                append_jsonl(episodes_path, {"task_id": task.task_id, "condition": cond, "repeat": rep,
                                             "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
                print(f"  [{counts['done']}/{len(jobs)}] ERROR {task.task_id} {cond} #{rep}: {e}", file=sys.stderr)
            return
        rec = {**ep.to_dict(), "repeat": rep, "domain": task.domain, "kind": task.kind, "length": task.length,
               "variant": task.variant,
               "planner_model": args.planner, "executor_model": args.executor}
        with lock:   # record first: nothing after this line can lose the episode
            counts["done"] += 1
            append_jsonl(episodes_path, rec)
        flag = "UNSAFE" if ep.unsafe else ("ok" if ep.task_completed else "incomplete")
        if getattr(ep, "over_execution", False):
            flag += " over-exec"
        # per step: P = ε_P on an intended step, - = intended and correct, r = neutral read, x = extra step
        marks = "".join("r" if s.eps is None and getattr(s, "kind", "") == "neutral_read" else
                        "x" if s.eps is None else ("P" if s.eps.eps_p else "-") for s in ep.steps) or "-"
        print(f"  [{counts['done']}/{len(jobs)}] {task.task_id:24s} {cond:2s} #{rep} {ep.stop_reason:13s} "
              f"steps={marks} {flag}", flush=True)

    print(f"{len(jobs)} episodes to run with {args.workers} worker(s)")
    if args.workers <= 1:
        for job in jobs:
            run_one(*job)
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(run_one, *job): job for job in jobs}
            for f in futures:
                try:
                    f.result()
                except Exception:   # a bug outside run_episode: report it, keep the other workers going
                    task, cond, rep = futures[f]
                    counts["err"] += 1
                    print(f"  RUNNER BUG {task.task_id} {cond} #{rep}:\n{traceback.format_exc()}", file=sys.stderr)
    print(f"done; {counts['err']} errors")


if __name__ == "__main__":
    main()
