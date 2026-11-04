"""Figures for the collected runs (0 API calls).

  PYTHONPATH=src python experiments/figures.py                                  # results/phase1 + results/phase2
  PYTHONPATH=src python experiments/figures.py results/phase1 results/phase2 --out results/figures

Writes PNG (200 dpi) and PDF for each figure. Every rate is drawn with its
Wilson 95% interval over episodes; the numbers behind each figure are in the
runs' derived/summary.md and derived/gate_rules.md.

  fig1_conditions_L1        completed / over-execution / unsafe / LLM calls per episode, by condition (L = 1)
  fig2_planner_vs_executed  Planner chain error (wrong or extra step sent) vs extra step actually executed (L = 1)
  fig3_chain_length         chain ε_P by chain length L, one panel per condition
  fig4_gate_rules           task completion and unsafe episodes under five Semantic Verifier rules (G, NG)
  fig5_per_task             chain ε_P per task × condition
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from analyze import load, wilson  # noqa: E402
from common import RESULTS  # noqa: E402
from gate_rules import RULES, n_restatements, rule_stats  # noqa: E402

CONDITIONS = ("B", "G", "N", "NG")
COND_NAME = {"B": "baseline", "G": "gate", "N": "negotiation", "NG": "both"}
COND_KEY = "B baseline · G gate · N negotiation · NG negotiation + gate"

# Reference palette (dataviz skill, references/palette.md), light mode. Slots 1-2 are validated all-pairs.
SURFACE, INK, INK_2, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#898781"
GRID, BASELINE = "#e1e0d9", "#c3c2b7"
ZERO_CELL = "#f0efec"                     # neutral gray (palette.md diverging midpoint): "nothing"
SERIES = ("#2a78d6", "#eb6834")          # categorical slot 1 (blue), slot 2 (orange)
SEQ_BLUE = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")


def style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "Arial", "DejaVu Sans"], "font.size": 9,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.titlecolor": INK, "axes.titlesize": 10,
        "axes.titleweight": "semibold", "axes.titlelocation": "left", "axes.titlepad": 10,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "xtick.major.size": 0, "ytick.major.size": 0, "axes.edgecolor": BASELINE, "axes.linewidth": 1,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.axisbelow": True, "legend.frameon": False, "legend.fontsize": 9,
    })


# ----------------------------------------------------------------------------- per-episode measures

def chain_eps_p(e: dict) -> bool:
    """The Planner's chain differs from o*: a wrong intended step, or an extra step sent (executed or not)."""
    return any((s.get("eps") or {}).get("eps_p") or s.get("kind") == "extra" for s in e["steps"])


MEASURES = {
    "completed": lambda e: bool(e["task_completed"]),
    "over_execution": lambda e: bool(e.get("over_execution")),
    "unsafe": lambda e: bool(e["unsafe"]),
    "chain_eps_p": chain_eps_p,
}


def rate(eps: list[dict], measure: str) -> tuple[float, float, float, int, int]:
    k, n = sum(MEASURES[measure](e) for e in eps), len(eps)
    lo, hi = wilson(k, n)
    return (k / n if n else float("nan")), lo, hi, k, n


def by(eps: list[dict], *keys: str) -> dict[tuple, list[dict]]:
    g = defaultdict(list)
    for e in eps:
        g[tuple(e[k] for k in keys)].append(e)
    return g


# ----------------------------------------------------------------------------- drawing helpers

def dot_ci(ax, x, p, lo, hi, color, label=None) -> None:
    ax.errorbar(x, p, yerr=[[p - lo], [hi - p]], fmt="none", ecolor=color, elinewidth=1.5, capsize=0, zorder=2)
    ax.plot(x, p, "o", ms=7, color=color, mec=SURFACE, mew=1.5, zorder=3, label=label)


def pct_axis(ax, top: float = 1.0) -> None:
    ax.set_ylim(-0.02, top + 0.02)
    ticks = [t / 100 for t in range(0, int(top * 100) + 1, 25)]
    ax.set_yticks(ticks, [f"{t:.0%}" for t in ticks])


def note(fig, text: str) -> None:
    fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=8, color=MUTED)


def save(fig, out: Path, name: str) -> None:
    for ext in ("png", "pdf"):
        fig.savefig(out / f"{name}.{ext}", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  {out / name}.png / .pdf")


# ----------------------------------------------------------------------------- figures

def fig1_conditions(eps: list[dict], out: Path) -> None:
    l1 = by([e for e in eps if e["length"] == 1], "condition")
    panels = [("completed", "Task completed"), ("over_execution", "Extra step executed (over-execution)"),
              ("unsafe", "Unsafe episode"), (None, "LLM calls per episode")]
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.2))
    for ax, (m, title) in zip(axes, panels):
        for i, c in enumerate(CONDITIONS):
            if m is None:
                calls = [e["n_calls"] for e in l1[(c,)]]
                ax.bar(i, sum(calls) / len(calls), width=0.16, color=SERIES[0], zorder=2)
            else:
                p, lo, hi, _, _ = rate(l1[(c,)], m)
                dot_ci(ax, i, p, lo, hi, SERIES[0])
        ax.set_xticks(range(len(CONDITIONS)), CONDITIONS)
        ax.set_xlim(-0.6, len(CONDITIONS) - 0.4)
        ax.set_title(title)
        if m is not None:
            pct_axis(ax)
    n = len(l1[("B",)])
    fig.suptitle(f"Phase 1, single-step tasks (L = 1): {n} episodes per condition", x=0.01, ha="left",
                 fontsize=11, fontweight="semibold", y=1.04)
    note(fig, f"{COND_KEY}. Dots: share of episodes; vertical lines: Wilson 95% interval.\n"
              "16 seed tasks × 3 repeats. Planner gpt-4o-mini, Executor gpt-4.1, N = 5 restatements, R = 2 rounds.")
    fig.subplots_adjust(bottom=0.25, wspace=0.3)
    save(fig, out, "fig1_conditions_L1")


def fig2_planner_vs_executed(eps: list[dict], out: Path) -> None:
    l1 = by([e for e in eps if e["length"] == 1], "condition")
    series = [("chain_eps_p", "Planner sent a wrong or extra step (chain ε_P)"),
              ("over_execution", "Extra step actually executed (over-execution)")]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    for j, (m, label) in enumerate(series):
        for i, c in enumerate(CONDITIONS):
            p, lo, hi, _, _ = rate(l1[(c,)], m)
            dot_ci(ax, i + (j - 0.5) * 0.22, p, lo, hi, SERIES[j], label=label if i == 0 else None)
    ax.set_xticks(range(len(CONDITIONS)), [f"{c}\n{COND_NAME[c]}" for c in CONDITIONS])
    ax.set_xlim(-0.6, len(CONDITIONS) - 0.4)
    pct_axis(ax, 0.5)
    ax.set_title("The gate does not reduce the Planner's chain error; it stops its execution")
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.22), ncol=1)
    note(fig, "Phase 1, L = 1, 48 episodes per condition. Wilson 95% intervals.")
    fig.subplots_adjust(bottom=0.36)
    save(fig, out, "fig2_planner_vs_executed")


def fig3_chain_length(eps: list[dict], out: Path) -> None:
    g = by(eps, "condition", "length")
    lengths = sorted({e["length"] for e in eps})
    fig, axes = plt.subplots(1, 4, figsize=(12, 3.2), sharey=True)
    for ax, c in zip(axes, CONDITIONS):
        pts = [rate(g[(c, L)], "chain_eps_p") for L in lengths]
        ax.plot(lengths, [p[0] for p in pts], color=SERIES[0], lw=2, solid_capstyle="round", zorder=2)
        for L, (p, lo, hi, _, _) in zip(lengths, pts):
            dot_ci(ax, L, p, lo, hi, SERIES[0])
        ax.set_xticks(lengths, [f"L = {L}\nn = {pts[i][4]}" for i, L in enumerate(lengths)])
        ax.set_xlim(lengths[0] - 0.4, lengths[-1] + 0.4)
        ax.set_title(f"{c} · {COND_NAME[c]}")
        pct_axis(ax, 0.75)
    fig.suptitle("Chain-level ε_P by chain length (file-task seed set): amplification not measurable here", x=0.01,
                 ha="left", fontsize=11, fontweight="semibold", y=1.04)
    note(fig, "L is confounded with request explicitness: the L = 1 requests leave the end point open (\"prepare\", "
              "\"check\"), the chain requests name every step,\nso the Planner had nothing to decompose at L = 2, 3. "
              "Phase 1 (L = 1) + Phase 2 (L = 2, 3). Wilson 95% intervals.")
    fig.subplots_adjust(bottom=0.32, wspace=0.15)
    save(fig, out, "fig3_chain_length")


def fig4_gate_rules(eps: list[dict], dirs: list[Path], out: Path) -> None:
    stats = rule_stats(eps, n_restatements(dirs))
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4))
    for ax, (key, title) in zip(axes, [("completed", "Task completed"), ("unsafe_eps", "Unsafe episode")]):
        for j, cond in enumerate(("G", "NG")):
            for i, rule in enumerate(RULES):
                a = stats[cond][rule]
                k, n = a[key], a["episodes"]
                lo, hi = wilson(k, n)
                dot_ci(ax, i + (j - 0.5) * 0.22, k / n, lo, hi, SERIES[j],
                       label=f"{cond} ({n} episodes)" if i == 0 else None)
        ax.set_xticks(range(len(RULES)), [r.replace("_", " ") for r in RULES])
        ax.set_xlim(-0.6, len(RULES) - 0.4)
        ax.set_title(title)
        pct_axis(ax, 1.0 if key == "completed" else 0.25)
    axes[0].legend(loc="lower left")
    fig.suptitle("Semantic Verifier decision rules, re-evaluated offline on the recorded restatements", x=0.01,
                 ha="left", fontsize=11, fontweight="semibold", y=1.04)
    note(fig, "modal = as collected (ties to the earliest); majority = > N/2; tie reject = modal, reject ties; "
              "unanimous = all N agree; done veto = reject if any restatement is \"done\".\n"
              "Phase 1 + Phase 2, N = 5. An episode stops at a rule's first rejection. Wilson 95% intervals.")
    fig.subplots_adjust(bottom=0.3, wspace=0.2)
    save(fig, out, "fig4_gate_rules")


def fig5_per_task(eps: list[dict], out: Path) -> None:
    g = by(eps, "task_id", "condition")
    tasks = sorted({(e["length"], e["kind"], e["task_id"]) for e in eps})
    grid = [[rate(g[(t, c)], "chain_eps_p") for c in CONDITIONS] for _, _, t in tasks]
    fig, ax = plt.subplots(figsize=(5.6, 0.26 * len(tasks) + 1.4))
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
    cmap.set_bad(ZERO_CELL)
    values = [[cell[0] if cell[3] else float("nan") for cell in row] for row in grid]
    im = ax.imshow(values, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    for r, row in enumerate(grid):
        for c, (p, _, _, k, n) in enumerate(row):
            if k:
                ax.text(c, r, f"{k}/{n}", ha="center", va="center", fontsize=8,
                        color="#ffffff" if p >= 0.5 else INK)
    ax.set_xticks(range(len(CONDITIONS)), CONDITIONS)
    ax.set_yticks(range(len(tasks)), [f"{t}  (L={L})" for L, _, t in tasks])
    ax.tick_params(axis="x", top=True, labeltop=True, bottom=False, labelbottom=False)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks([x - 0.5 for x in range(1, len(CONDITIONS))], minor=True)
    ax.set_yticks([y - 0.5 for y in range(1, len(tasks))], minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)
    cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03, ticks=[0, 0.5, 1])
    cb.ax.set_yticklabels(["0%", "50%", "100%"])
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, labelcolor=INK_2, length=0)
    ax.set_title("Chain-level ε_P per task (share of 3 repeats)", pad=24)
    note(fig, "Gray cells: 0 of 3. Errors concentrate in a few L = 1 tasks. Phase 1 + Phase 2.")
    save(fig, out, "fig5_per_task")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", default=[str(RESULTS / "phase1"), str(RESULTS / "phase2")])
    ap.add_argument("--out", default=str(RESULTS / "figures"))
    args = ap.parse_args()
    dirs = [Path(d) for d in args.dirs]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    eps, errors = load(dirs)
    print(f"{len(eps)} episodes from {', '.join(d.name for d in dirs)} ({len(errors)} errored, excluded) -> {out}")
    style()
    fig1_conditions(eps, out)
    fig2_planner_vs_executed(eps, out)
    fig3_chain_length(eps, out)
    fig4_gate_rules(eps, dirs, out)
    fig5_per_task(eps, out)


if __name__ == "__main__":
    main()
