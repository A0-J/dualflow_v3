"""Shared settings for the experiment scripts."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RESULTS = ROOT / "results"

PLANNER_MODEL = "gpt-4o-mini"
EXECUTOR_MODELS = ("gpt-4o-mini", "gpt-4.1", "gpt-5.5", "gpt-6.1-sol")
DEFAULT_EXECUTOR = "gpt-4.1"
DEFAULT_SAMPLING_MODELS = frozenset({"gpt-5.5", "gpt-6.1-sol"})   # reject temperature/top_p

N_RESTATEMENTS = 5
MAX_ROUNDS = 2


def sampling_for(model: str) -> dict:
    if model in DEFAULT_SAMPLING_MODELS:
        return {"temperature": None, "top_p": None}
    return {"temperature": 1.0, "top_p": 1.0}


API_MAX_RETRIES = 8   # SDK retries 429 / 5xx / connection errors with exponential backoff


def make_client(model: str):
    from openai import OpenAI
    from dualflow3.llm import OpenAILLMClient
    return OpenAILLMClient(OpenAI(max_retries=API_MAX_RETRIES, timeout=120.0), model, **sampling_for(model))


def git_info() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
        return {"commit": commit, "dirty": dirty}
    except Exception:
        return {"commit": None, "dirty": None}


def run_meta(**extra) -> dict:
    return {"time": datetime.now(timezone.utc).isoformat(), "git": git_info(),
            "python": sys.version.split()[0], "cwd": os.getcwd(), **extra}
