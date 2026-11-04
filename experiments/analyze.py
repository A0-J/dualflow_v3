"""Aggregate episodes.jsonl: outcomes, ε, task completion, cost — per condition, kind, step index.

  PYTHONPATH=src python experiments/analyze.py results/<name>
  PYTHONPATH=src python experiments/analyze.py results/a results/b     # several runs together

Writes <dir>/derived/summary.md and <dir>/derived/steps.csv for the first directory.
Units: the primary unit is the task (its repeats and steps are not independent);
rates are reported per task (mean of per-task rates) and per step, both with
simple Wilson intervals for the per-step counts.
"""

from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

from common import RESULTS  # noqa: E402,F401

from dualflow3.io import read_jsonl  # noqa: E402

OUTCOMES = ("unsafe_execution", "misaligned_execution", "false_rejection", "correct_decision")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%) [{lo:.2f}, {hi:.2f}]"


def load(dirs: list[Path]) -> tuple[list[dict], list[dict]]:
    eps, errors = [], []
    for d in dirs:
        for r in read_jsonl(d / "episodes.jsonl"):
            (errors if "error" in r else eps).append({**r, "_run": d.name})
    # an error later re-run successfully by --resume is not an errored episode
    ok = {(e["_run"], e["task_id"], e["condition"], e["repeat"]) for e in eps}
    errors = [e for e in errors if (e["_run"], e["task_id"], e["condition"], e["repeat"]) not in ok]
    return eps, errors


def step_rows(episodes: list[dict]) -> list[dict]:
    rows = []
    for e in episodes:
        for s in e["steps"]:
            eps = s.get("eps") or {}
            rows.append({"run": e["_run"], "task_id": e["task_id"], "domain": e["domain"], "kind": e["kind"],
                         "length": e["length"], "condition": e["condition"], "repeat": e["repeat"], "k": s["k"],
                         "step_kind": s.get("kind", "intended"), "executor_model": e.get("executor_model"),
                         "eps_p": eps.get("eps_p"), "eps_e": eps.get("eps_e"), "eps": eps.get("eps"),
                         "dist_p": eps.get("dist_p"), "dist_e": eps.get("dist_e"), "dist": eps.get("dist"),
                         "wrong_p": "|".join(eps.get("wrong_p", [])), "wrong": "|".join(eps.get("wrong", [])),
                         "rounds": s["rounds"], "flagged": "|".join(",".join(f) for f in s["flagged_fields"]),
                         "execute": s["decision"]["execute"], "v_sem": s["decision"]["v_sem"],
                         "v_auth": s["decision"]["v_auth"], "outcome": s["outcome"],
                         "n_calls": s["n_calls"], "input_tokens": s["input_tokens"], "output_tokens": s["output_tokens"]})
    return rows


def table(episodes: list[dict], rows: list[dict], key, title: str) -> list[str]:
    groups = defaultdict(lambda: {"ep": [], "st": []})
    for e in episodes:
        groups[key(e)]["ep"].append(e)
    for r in rows:
        groups[key(r)]["st"].append(r)
    lines = [f"## {title}", "",
             "ε columns count intended steps only; over-exec = episodes with an executed extra step; "
             "outcome columns count all steps (intended + neutral reads + extra).", "",
             "| group | episodes | completed | over-exec | unsafe | intended steps | ε_P | ε_E | ε | neutral reads | extra steps "
             "| unsafe | misaligned | false rej | correct | calls/ep | rounds/step |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for g in sorted(groups, key=str):
        ep, st = groups[g]["ep"], groups[g]["st"]
        it = [s for s in st if s["step_kind"] == "intended"]
        n, ns, ni = len(ep), len(st), len(it)
        oc = {o: sum(s["outcome"] == o for s in st) for o in OUTCOMES}
        calls = sum(e["n_calls"] for e in ep) / n if n else float("nan")
        rounds = sum(s["rounds"] for s in st) / ns if ns else float("nan")
        lines.append(f"| {g} | {n} | {fmt(sum(e['task_completed'] for e in ep), n)} "
                     f"| {fmt(sum(bool(e.get('over_execution')) for e in ep), n)} | {fmt(sum(e['unsafe'] for e in ep), n)} "
                     f"| {ni} | {fmt(sum(bool(s['eps_p']) for s in it), ni)} | {fmt(sum(bool(s['eps_e']) for s in it), ni)} "
                     f"| {fmt(sum(bool(s['eps']) for s in it), ni)} "
                     f"| {sum(s['step_kind'] == 'neutral_read' for s in st)} | {sum(s['step_kind'] == 'extra' for s in st)} | "
                     + " | ".join(fmt(oc[o], ns) for o in OUTCOMES) + f" | {calls:.1f} | {rounds:.2f} |")
    return lines + [""]


def per_task(episodes: list[dict]) -> list[str]:
    """Task-level view: for each (condition, task) the share of repeats with ε_P at any step, unsafe, completed."""
    g = defaultdict(list)
    for e in episodes:
        g[(e["condition"], e["task_id"])].append(e)
    lines = ["## Per task (share of repeats)", "",
             "| condition | task | L | any ε_P | over-exec | unsafe | completed | stop reasons |",
             "|---|---|---|---|---|---|---|---|"]
    for (c, t), eps in sorted(g.items()):
        n = len(eps)
        any_p = sum(any((s.get("eps") or {}).get("eps_p") for s in e["steps"]) for e in eps)
        stops = defaultdict(int)
        for e in eps:
            stops[e["stop_reason"]] += 1
        lines.append(f"| {c} | {t} | {eps[0]['length']} | {any_p}/{n} | {sum(bool(e.get('over_execution')) for e in eps)}/{n} "
                     f"| {sum(e['unsafe'] for e in eps)}/{n} | {sum(e['task_completed'] for e in eps)}/{n} | {dict(stops)} |")
    return lines + [""]


def main() -> None:
    dirs = [Path(a) for a in sys.argv[1:]] or [RESULTS / "dry_run"]
    episodes, errors = load(dirs)
    rows = step_rows(episodes)
    lines = [f"# Summary of {', '.join(d.name for d in dirs)}", "",
             f"{len(episodes)} episodes, {len(rows)} steps, {len(errors)} errored episodes", ""]
    lines += table(episodes, rows, lambda r: r["condition"], "By condition")
    lines += table(episodes, rows, lambda r: (r["condition"], r["kind"]), "By condition × kind")
    lines += table(episodes, rows, lambda r: (r["condition"], f"L={r['length']}", f"k={r['k']}" if "k" in r else "ep"),
                   "By condition × chain length × step index (ε amplification; episode rows have k=ep)")
    lines += table(episodes, rows, lambda r: (r["condition"], r["executor_model"]), "By condition × Executor model")
    lines += per_task(episodes)
    if errors:
        lines += ["## Errors", ""] + [f"- {e['task_id']} {e['condition']} #{e['repeat']}: {e['error']}" for e in errors]
    out = dirs[0] / "derived"
    out.mkdir(exist_ok=True)
    (out / "summary.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    if rows:
        with (out / "steps.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print("\n".join(lines))
    print(f"\n-> {out / 'summary.md'}, {out / 'steps.csv'}")


if __name__ == "__main__":
    main()
