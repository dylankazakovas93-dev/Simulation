"""V5 — multi-objective optimization over declared configurations.

This is an explicit, engine-agnostic selection layer. It does NOT invent metrics
or re-run anything on its own: the caller supplies an ``evaluate`` function that
maps a candidate's parameters to a metrics dict (produced by the V1-V4 engines),
plus the objectives and constraints to optimize under.

GOVERNING PRINCIPLE (ADR-021, KNOWN_LIMITATIONS):
  * The optimizer must never collapse to a single objective (e.g. median terminal
    equity alone). By default at least two objectives are required; a single
    objective must be opted into explicitly and is recorded as a warning.
  * The decision output is the **Pareto frontier** (the non-dominated feasible
    set), not one "winner". A scalarized ranking is provided only as a labeled,
    secondary display aid — never as the answer.
  * Constraints are explicit and declared. Rejected candidates are reported with
    the specific constraint(s) they violated, so nothing is silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

_DIRECTIONS = {"max", "min"}
_OPS: dict[str, Callable[[float, float], bool]] = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}


@dataclass(frozen=True)
class Objective:
    """A declared objective: a metric key and whether to maximize or minimize it."""

    name: str
    direction: str  # "max" | "min"
    weight: float = 1.0  # used ONLY for the secondary scalarized display ranking

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("objective name is required")
        if self.direction not in _DIRECTIONS:
            raise ValueError("direction must be 'max' or 'min'")
        if self.weight < 0:
            raise ValueError("weight cannot be negative")

    def better(self, a: float, b: float) -> bool:
        """True if value ``a`` is strictly better than ``b`` for this objective."""

        return a > b if self.direction == "max" else a < b

    def at_least_as_good(self, a: float, b: float) -> bool:
        return a >= b if self.direction == "max" else a <= b


@dataclass(frozen=True)
class Constraint:
    """A declared feasibility constraint on a metric."""

    name: str
    op: str
    threshold: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("constraint name is required")
        if self.op not in _OPS:
            raise ValueError(f"op must be one of {sorted(_OPS)}")

    def satisfied_by(self, metrics: dict[str, float]) -> bool:
        if self.name not in metrics:
            raise KeyError(f"constraint metric {self.name!r} missing from candidate metrics")
        return _OPS[self.op](float(metrics[self.name]), self.threshold)

    def describe(self) -> str:
        return f"{self.name} {self.op} {self.threshold}"


@dataclass(frozen=True)
class Candidate:
    """A configuration and the metrics it produced."""

    id: str
    params: dict[str, Any]
    metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "params": self.params, "metrics": self.metrics}


@dataclass(frozen=True)
class OptimizationResult:
    objectives: list[Objective]
    constraints: list[Constraint]
    pareto_frontier: list[Candidate]
    feasible: list[Candidate]
    rejected: list[dict[str, Any]]  # {"id", "violations": [constraint descriptions]}
    scalarized_ranking: list[dict[str, Any]]  # secondary display aid only
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "objectives": [(o.name, o.direction, o.weight) for o in self.objectives],
            "constraints": [c.describe() for c in self.constraints],
            "pareto_frontier": [c.to_dict() for c in self.pareto_frontier],
            "num_feasible": len(self.feasible),
            "num_rejected": len(self.rejected),
            "rejected": self.rejected,
            "scalarized_ranking": self.scalarized_ranking,
            "warnings": self.warnings,
            "decision_note": (
                "The decision output is the Pareto frontier. scalarized_ranking is a "
                "labeled secondary display aid (normalized weighted sum) and must not "
                "be treated as the single answer."
            ),
        }


def evaluate_candidates(
    param_sets: list[dict[str, Any]],
    evaluate: Callable[[dict[str, Any]], dict[str, float]],
    *,
    id_key: str | None = None,
) -> list[Candidate]:
    """Run the caller's ``evaluate`` over each parameter set into Candidates."""

    candidates: list[Candidate] = []
    for i, params in enumerate(param_sets):
        cid = str(params[id_key]) if id_key and id_key in params else f"cand-{i}"
        metrics = evaluate(params)
        candidates.append(Candidate(id=cid, params=params, metrics=dict(metrics)))
    return candidates


def apply_constraints(
    candidates: list[Candidate], constraints: list[Constraint]
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    feasible: list[Candidate] = []
    rejected: list[dict[str, Any]] = []
    for cand in candidates:
        violations = [c.describe() for c in constraints if not c.satisfied_by(cand.metrics)]
        if violations:
            rejected.append({"id": cand.id, "violations": violations})
        else:
            feasible.append(cand)
    return feasible, rejected


def _dominates(a: Candidate, b: Candidate, objectives: list[Objective]) -> bool:
    """True if ``a`` Pareto-dominates ``b`` across all objectives."""

    at_least_as_good_all = True
    strictly_better_any = False
    for obj in objectives:
        av, bv = float(a.metrics[obj.name]), float(b.metrics[obj.name])
        if not obj.at_least_as_good(av, bv):
            at_least_as_good_all = False
            break
        if obj.better(av, bv):
            strictly_better_any = True
    return at_least_as_good_all and strictly_better_any


def pareto_frontier(
    candidates: list[Candidate], objectives: list[Objective]
) -> list[Candidate]:
    """The non-dominated set: no other candidate dominates a frontier member."""

    for obj in objectives:
        for cand in candidates:
            if obj.name not in cand.metrics:
                raise KeyError(
                    f"objective metric {obj.name!r} missing from candidate {cand.id!r}"
                )
    frontier: list[Candidate] = []
    for cand in candidates:
        if not any(
            _dominates(other, cand, objectives) for other in candidates if other is not cand
        ):
            frontier.append(cand)
    return frontier


def _scalarized_ranking(
    feasible: list[Candidate], objectives: list[Objective]
) -> list[dict[str, Any]]:
    """Min-max normalize each objective across feasible set, then weighted sum.

    Explicitly secondary: this is a display aid for humans scanning the frontier,
    never the decision. Direction is respected (min objectives are inverted).
    """

    if not feasible:
        return []
    ranges: dict[str, tuple[float, float]] = {}
    for obj in objectives:
        vals = [float(c.metrics[obj.name]) for c in feasible]
        ranges[obj.name] = (min(vals), max(vals))
    scored: list[dict[str, Any]] = []
    total_weight = sum(o.weight for o in objectives) or 1.0
    for cand in feasible:
        score = 0.0
        for obj in objectives:
            lo, hi = ranges[obj.name]
            v = float(cand.metrics[obj.name])
            norm = 0.5 if hi == lo else (v - lo) / (hi - lo)
            if obj.direction == "min":
                norm = 1.0 - norm
            score += obj.weight * norm
        scored.append({"id": cand.id, "scalarized_score": score / total_weight})
    scored.sort(key=lambda row: row["scalarized_score"], reverse=True)
    return scored


def optimize(
    candidates: list[Candidate],
    objectives: list[Objective],
    constraints: list[Constraint] | None = None,
    *,
    allow_single_objective: bool = False,
) -> OptimizationResult:
    """Filter by constraints, compute the Pareto frontier, and rank (secondary)."""

    constraints = list(constraints or [])
    warnings: list[str] = []
    if not objectives:
        raise ValueError("at least one objective is required")
    if len(objectives) < 2:
        if not allow_single_objective:
            raise ValueError(
                "multi-objective optimization requires >= 2 objectives; single-objective "
                "runs must set allow_single_objective=True (and are recorded as a warning). "
                "Optimizing one metric alone (e.g. median terminal equity) is a documented trap."
            )
        warnings.append(
            "SINGLE-OBJECTIVE run: optimizing one metric alone can exploit model traps "
            "(capped equity, realized-only drawdown, greedy prop payouts). Interpret with care."
        )
    names = [o.name for o in objectives]
    if len(set(names)) != len(names):
        raise ValueError("objectives must have distinct metric names")

    feasible, rejected = apply_constraints(candidates, constraints)
    frontier = pareto_frontier(feasible, objectives)
    ranking = _scalarized_ranking(feasible, objectives)
    if not feasible and candidates:
        warnings.append("no candidate satisfied all constraints; frontier is empty")

    return OptimizationResult(
        objectives=list(objectives),
        constraints=constraints,
        pareto_frontier=frontier,
        feasible=feasible,
        rejected=rejected,
        scalarized_ranking=ranking,
        warnings=warnings,
    )


def expected_log_growth(per_period_returns: list[float]) -> float:
    """Mean log-growth per period (geometric-growth objective).

    Each return is a simple period return r (e.g. 0.02 for +2%). Any r <= -1 (total
    loss of the period's capital) yields -inf growth, which is the correct ruinous
    signal and must not be silently clipped.
    """

    import math

    if not per_period_returns:
        return 0.0
    total = 0.0
    for r in per_period_returns:
        if r <= -1.0:
            return float("-inf")
        total += math.log1p(r)
    return total / len(per_period_returns)
