# dualflow_3

Structured agent-to-agent delegation: the Planner Agent sends the Executor
Agent a natural-language message `d` **together with** the structured tuple
ARS = (action, resource, scope). The study measures the error that remains
even then — the Planner's decomposition error ε — how it grows over a chain
of dependent steps, and what reduces it.

```
user request ─▶ Planner ──(d_k, ARS_P)──▶ Executor ──ARS_E──▶ [Semantic V] ∧ [Authorization V] ──▶ execute
                   ▲        negotiation (≤R rounds)   │              ▲
                   └────────── question / confirm ────┘              └── N restatements from request + private context
```

Three tuples per step: `o*` (intended, ground truth), `ARS_P` (sent by the
Planner), `ARS_E` (proposed by the Executor).
`ε_P = [ARS_P ≠ o*]`, `ε_E = [ARS_E ≠ ARS_P]`, `ε = [ARS_E ≠ o*]`; each also per field (0–3).

Conditions (2×2):

|                   | no gate | gate |
|---|---|---|
| no negotiation    | **B**   | **G** |
| negotiation (≤R)  | **N**   | **NG** |

## Layout

| Path | Contents |
|---|---|
| `src/dualflow3/proposal.py` | `Proposal`, `modal`, `epsilon` (tuple- and field-level) |
| `src/dualflow3/environment.py` | expanded candidate space (8 actions, 6 files/domain, 3 recipients), derived files for chains |
| `src/dualflow3/agents.py` | Planner: `decompose`, `confirm`, `restate`; Executor: `propose`, `propose_or_flag`, `finalize` |
| `src/dualflow3/verifiers.py` | Semantic Verifier, Authorization Verifier, gate |
| `src/dualflow3/protocol.py` | one episode under a condition: steps, negotiation rounds, gate, ε, outcomes |
| `src/dualflow3/outcomes.py` | the four outcomes (Unsafe / Misaligned / False Rejection / Correct), per step |
| `src/dualflow3/tasks.py` | `Task`, 24 seed tasks (4 domains × 6 kinds, L = 1..3), `with_authorization` |
| `src/dualflow3/mock.py` | `OracleLLM` (offline, with error injection), `ScriptedLLM` |
| `experiments/run.py` | collect episodes: tasks × conditions × repeats |
| `experiments/analyze.py` | tables by condition, kind, chain length × step index, model; per-task view |
| `PLAN.md` | experiment plan |

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## Run

```bash
# whole pipeline offline (oracle agents, 0 API calls)
PYTHONPATH=src python experiments/run.py --dry-run --conditions B G N NG
PYTHONPATH=src python experiments/analyze.py results/dry_run

# Phase 0 pilot: does ε_P > 0 appear at all?  (condition B, single-step tasks, 3 repeats)
export OPENAI_API_KEY=...
PYTHONPATH=src python experiments/run.py --name pilot --conditions B --max-length 1 --repeats 3
PYTHONPATH=src python experiments/analyze.py results/pilot

# Phase 1: 2×2 on single-step tasks
PYTHONPATH=src python experiments/run.py --name phase1 --conditions B G N NG --max-length 1 --repeats 3
# Phase 2: chains
PYTHONPATH=src python experiments/run.py --name phase2 --conditions B G N NG --kinds chain2 chain3 --repeats 3
# Phase 3: Executor models (B and NG only)
for m in gpt-4o-mini gpt-4.1 gpt-5.5 gpt-6.1-sol; do
  PYTHONPATH=src python experiments/run.py --name phase3_$m --conditions B NG --executor $m --repeats 3
done
PYTHONPATH=src python experiments/analyze.py results/phase3_*
```

`--resume` skips (task, condition, repeat) triples already in `episodes.jsonl`.
Every episode record keeps every LLM response with token usage, so cost is
analysed from the raw data. Default models: Planner `gpt-4o-mini`, Executor
`gpt-4.1`; `N = 5` restatements; `R = 2` negotiation rounds (`experiments/common.py`).

## Authorization configurations

Seed tasks grant every action in the domain (like v2 case C2), so that
authorization alone cannot stop a misinterpretation. `tasks.with_authorization`
derives the C3/C4-style configurations (`deny_actions`, `approval_required`,
`granted`) from the same task; the evaluation set decides which to run.

## Relation to dualflow_v2

v2's `structured_intent_control` showed that in v2's setting (4 actions, 2
files, Planner sees the full task context) sending ARS removes every error.
v3 changes the setting so that the decomposition itself can fail, and treats
v2's two verifiers as one condition (the gate), not the contribution.
