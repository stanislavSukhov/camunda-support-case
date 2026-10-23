"""Area classification for a support thread.

If ANTHROPIC_API_KEY is set and the `anthropic` package is installed, ask Claude.
Otherwise fall back to a keyword heuristic. The process does not care which one ran;
it only sees `area` and `areaSource` ("llm" or "heuristic"), which is the point:
the model decides *that* classification happens, the worker decides *how*.
"""
from __future__ import annotations

import os

AREAS = ("backtesting", "gateway", "core", "infra", "data", "other")

_KEYWORDS = {
    "backtesting": ("backtest", "bt run", "stand reset", "simulation"),
    "gateway": ("gateway", "connectivity", "exchange connection", "fix session", "order rejected"),
    "core": ("core", "backend", "service crashed", "api returns", "500"),
    "infra": ("infra", "kubernetes", "k8s", "pod", "disk", "vm", "deploy", "ci "),
    "data": ("market data", "md ", "dataset", "parquet", "clickhouse", "missing candles"),
}


def heuristic(text: str) -> str:
    t = (text or "").lower()
    for area, words in _KEYWORDS.items():
        if any(w in t for w in words):
            return area
    return "other"


def classify(text: str) -> tuple[str, str]:
    """Return (area, source)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _classify_with_claude(text), "llm"
        except Exception as e:  # network, auth, missing package: degrade loudly, not fatally
            print(f"[classify] Claude call failed ({type(e).__name__}: {e}); using heuristic")
    return heuristic(text), "heuristic"


def _classify_with_claude(text: str) -> str:
    import anthropic  # optional dependency: pip install anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5-5",
        max_tokens=256,
        output_config={"effort": "low"},
        system=(
            "You triage support threads for a trading platform's on-call team. "
            f"Answer with exactly one word from this list: {', '.join(AREAS)}. "
            "backtesting = simulation runs and stands; gateway = exchange connectivity and order flow; "
            "core = backend services and APIs; infra = clusters, VMs, deployments, CI; "
            "data = market data and datasets; other = anything else."
        ),
        messages=[{"role": "user", "content": f"Thread:\n\n{text}"}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused")
    answer = "".join(b.text for b in response.content if b.type == "text").strip().lower()
    return answer if answer in AREAS else "other"
