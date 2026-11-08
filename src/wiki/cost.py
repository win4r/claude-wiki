"""Cost estimates, cap checks and cap sharing.

The starting figures come from the knowledge-wiki quickstart's estimate_build_cost (MIT, see NOTICE); after
each paid command the ledger's averages per phase and model replace them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import WikiError

# US dollars per million tokens at list price (the quickstart's table).
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "cache_write": 5.00, "cache_read": 0.20, "output": 20.00},
    "claude-sonnet-5-5": {"input": 2.00, "cache_write": 2.50, "cache_read": 0.10, "output": 10.00},
    "claude-haiku-5-5": {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50},
    "claude-fable-5-1": {"input": 10.00, "cache_write": 12.50, "cache_read": 0.25, "output": 50.00},
}
# A Haiku 5.5 request whose prompt passes 100,000 tokens pays these instead.
LONG_PROMPT_PRICES = {
    "claude-haiku-5-5": {"over": 100_000, "input": 0.50, "cache_write": 0.625, "cache_read": 0.05, "output": 2.50},
}
ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
           "fable": "claude-fable-5-1"}

CHARS_PER_TOKEN = 3.5
FIXED_TOKENS = 6_000
EXTRACTOR = {"requests": 41, "reads": 3.2, "output": 1.75}
ANALYZER = {"requests": 56, "reads": 5.2, "output": 1.2}
MIN_OUTPUT_TOKENS = 3_000
RESOLVER_TOKENS = {"cache_write": 180_000, "cache_read": 7_700_000, "output": 52_000}
ORGANIZER_TOKENS = {"cache_write": 400_000, "cache_read": 31_000_000, "output": 120_000}
RECORDED_CORPUS_CHARS = 25 * 110_000  # the build those single-agent figures came from
# The quickstart's organizer did linking, compiling and indexing in one agent; split its figure.
ORGANIZER_SPLIT = {"link": 0.3, "compile": 0.4, "index": 0.3}

QUESTION_USD = 0.02
GRADE_USD = 0.02
DREAM_BASE_USD = 1.00
DREAM_PER_TRANSCRIPT_USD = 0.15


def model_id(alias: str) -> str:
    return ALIASES.get(alias, alias)


def prices_of(model: str, prompt_tokens: float = 0) -> dict:
    mid = model_id(model)
    long = LONG_PROMPT_PRICES.get(mid)
    if long and prompt_tokens > long["over"]:
        return long
    return PRICES.get(mid, PRICES["claude-opus-5-5"])


def dollars(tokens: dict, prices: dict) -> float:
    return sum(tokens.get(k, 0) * prices[k] for k in ("input", "cache_write", "cache_read", "output")) / 1e6


def worker_tokens(chars: float, profile: dict) -> dict:
    batch = chars / CHARS_PER_TOKEN
    output = max(MIN_OUTPUT_TOKENS, batch * profile["output"])
    context = FIXED_TOKENS + batch * profile["reads"] + output
    return {"cache_write": context, "cache_read": profile["requests"] * context / 2, "output": output}


def priced(tokens: dict, model: str) -> float:
    return dollars(tokens, prices_of(model, prompt_tokens=tokens["cache_write"]))


def scaled(tokens: dict, factor: float) -> dict:
    return {k: v * factor for k, v in tokens.items()}


@dataclass
class Estimate:
    total: float = 0.0
    phases: dict[str, float] = field(default_factory=dict)
    tasks: dict[str, float] = field(default_factory=dict)  # task id -> estimate
    lines: list[str] = field(default_factory=list)

    def add(self, phase: str, task: str, usd: float) -> None:
        self.tasks[task] = usd
        self.phases[phase] = self.phases.get(phase, 0.0) + usd
        self.total += usd


def _calibrated(averages: dict, phase: str, model: str, chars: float | None, default: float) -> tuple[float, bool]:
    a = averages.get((phase, model))
    if not a:
        return default, False
    if chars and a.get("per_char"):
        return a["per_char"] * chars, True
    return a["per_run"], True


def estimate_build(batches: dict[str, int], corpus_chars: int, models: dict[str, str], averages: dict,
                   n_compile_groups: int, run_resolve: bool = True, run_link: bool = True) -> Estimate:
    """batches maps a batch id to its characters; corpus_chars is the whole corpus (the single agents read it)."""
    est = Estimate()
    calibrated = set()
    for bid, chars in batches.items():
        for phase, profile in (("extract", EXTRACTOR), ("check", ANALYZER)):
            default = priced(worker_tokens(chars, profile), models[phase])
            amount, cal = _calibrated(averages, phase, models[phase], chars, default)
            if cal:
                calibrated.add(phase)
            est.add(phase, f"{phase}-{bid}", amount)
    factor = max(0.05, corpus_chars / RECORDED_CORPUS_CHARS)
    if run_resolve:
        amount, cal = _calibrated(averages, "resolve", models["resolve"], None,
                               priced(scaled(RESOLVER_TOKENS, factor), models["resolve"]))
        calibrated |= {"resolve"} if cal else set()
        est.add("resolve", "resolve", amount)
    for phase in ("link", "compile", "index"):
        if phase == "link" and not run_link:
            continue
        default = priced(scaled(ORGANIZER_TOKENS, factor * ORGANIZER_SPLIT[phase]), models[phase])
        if phase == "compile":
            groups = max(1, n_compile_groups)
            amount, cal = _calibrated(averages, phase, models[phase], None, default / groups)
            for g in range(groups):
                est.add(phase, f"compile-{g + 1:02d}", amount)
        else:
            amount, cal = _calibrated(averages, phase, models[phase], None, default)
            est.add(phase, phase, amount)
        calibrated |= {phase} if cal else set()
    est.lines.append(
        f"{len(batches)} batch(es), {corpus_chars / 1000:,.0f} KB of text "
        f"(about {corpus_chars / CHARS_PER_TOKEN:,.0f} tokens)."
    )
    est.lines.append("By phase: " + ", ".join(f"{p} {usd(v)}" for p, v in est.phases.items()))
    est.lines.append(
        "Calibrated from the ledger: " + (", ".join(sorted(calibrated)) if calibrated else "none yet (starting figures)")
    )
    return est


def estimate_question(averages: dict, mode: str, model: str) -> float:
    return _calibrated(averages, f"ask-{mode}", model, None, QUESTION_USD)[0]


def estimate_grade(averages: dict, model: str) -> float:
    return _calibrated(averages, "grade", model, None, GRADE_USD)[0]


def estimate_dream(n_transcripts: int) -> float:
    return DREAM_BASE_USD + DREAM_PER_TRANSCRIPT_USD * n_transcripts


def usd(amount: float) -> str:
    return f"${amount:,.2f}"


def usd_fine(amount: float) -> str:
    """Like usd, with four decimals under a dime: per-answer costs are often a fraction of a cent."""
    return f"${amount:,.4f}" if 0 < abs(amount) < 0.10 else usd(amount)


def authorize(spent: float, total: float, estimate: float, cap: float, approve_over_cap: bool,
              what: str, reserve: float = 0.10) -> float:
    """The cap this command may spend, or WikiError. Hard stop at the total; ask when over the command's cap."""
    remaining = total - spent
    if spent + estimate > total:
        raise WikiError(
            f"Refused: {what} is estimated at {usd(estimate)}, but only {usd(remaining)} of the "
            f"{usd(total)} total is left (shortfall {usd(spent + estimate - total)}). Only the owner raises "
            "caps.total_usd, by editing wiki.toml.",
            code=4,
        )
    if estimate > cap:
        if not approve_over_cap:
            raise WikiError(
                f"Stopped: {what} is estimated at {usd(estimate)}, above its cap of {usd(cap)}. "
                "Ask the owner; to approve it once, rerun with --approve-over-cap.",
                code=3,
            )
        cap = estimate / (1 - reserve)
    return min(cap, remaining)


def share(estimates: dict[str, float], cap: float, reserve: float = 0.10,
          floor: float = 0.0) -> dict[str, float]:
    """Split a command's cap across its runs in proportion to their estimates, holding back the reserve.

    Each run gets at least `floor` (when the pool allows it), taken from the larger shares, so a cheap run is
    not stopped by a budget of a cent or two. The shares still sum to at most cap * (1 - reserve).
    """
    pool = cap * (1 - reserve)
    total = sum(estimates.values())
    if total <= 0:
        n = max(1, len(estimates))
        return {k: pool / n for k in estimates}
    out = {k: pool * v / total for k, v in estimates.items()}
    floor = min(floor, pool / max(1, len(out)))
    low = {k for k, v in out.items() if v < floor}
    if not low:
        return out
    high_total = sum(v for k, v in out.items() if k not in low)
    scale = (pool - floor * len(low)) / high_total if high_total else 0.0
    return {k: floor if k in low else v * scale for k, v in out.items()}
