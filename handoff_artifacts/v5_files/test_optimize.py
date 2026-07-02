"""V5 — multi-objective optimizer tests: Pareto frontier, constraints, guards."""
from __future__ import annotations

import math

import pytest

from sim_core.optimize import (
    Candidate,
    Constraint,
    Objective,
    apply_constraints,
    evaluate_candidates,
    expected_log_growth,
    optimize,
    pareto_frontier,
)


def _cand(cid, **metrics):
    return Candidate(id=cid, params=dict(metrics), metrics=dict(metrics))


def test_requires_at_least_two_objectives_by_default():
    cands = [_cand("a", median_terminal=1.0)]
    with pytest.raises(ValueError, match="requires >= 2 objectives"):
        optimize(cands, [Objective("median_terminal", "max")])


def test_single_objective_allowed_with_flag_records_warning():
    cands = [_cand("a", median_terminal=1.0), _cand("b", median_terminal=2.0)]
    result = optimize(
        cands, [Objective("median_terminal", "max")], allow_single_objective=True
    )
    assert any("SINGLE-OBJECTIVE" in w for w in result.warnings)
    # only b is non-dominated
    assert [c.id for c in result.pareto_frontier] == ["b"]


def test_pareto_frontier_excludes_dominated():
    # maximize return, minimize ruin.
    cands = [
        _cand("A", ret=10, ruin=0.5),  # dominated by C
        _cand("B", ret=8, ruin=0.1),
        _cand("C", ret=12, ruin=0.4),
        _cand("D", ret=12, ruin=0.4),  # ties C -> both non-dominated (no strict dominance)
    ]
    objs = [Objective("ret", "max"), Objective("ruin", "min")]
    frontier = {c.id for c in pareto_frontier(cands, objs)}
    # A is dominated by C (higher ret, lower ruin). B, C, D are non-dominated.
    assert "A" not in frontier
    assert {"B", "C", "D"} <= frontier


def test_constraints_reject_with_reasons():
    cands = [
        _cand("ok", ret=10, ruin=0.05, max_dd=0.2),
        _cand("too_risky", ret=20, ruin=0.30, max_dd=0.6),
    ]
    constraints = [Constraint("ruin", "<=", 0.10), Constraint("max_dd", "<=", 0.30)]
    feasible, rejected = apply_constraints(cands, constraints)
    assert [c.id for c in feasible] == ["ok"]
    assert rejected[0]["id"] == "too_risky"
    assert "ruin <= 0.1" in rejected[0]["violations"]
    assert "max_dd <= 0.3" in rejected[0]["violations"]


def test_optimize_end_to_end_frontier_and_ranking():
    cands = [
        _cand("safe", net_cash=1_000, ruin=0.02, log_growth=0.01),
        _cand("mid", net_cash=3_000, ruin=0.08, log_growth=0.03),
        _cand("wild", net_cash=9_000, ruin=0.40, log_growth=0.05),  # fails constraint
    ]
    objs = [
        Objective("net_cash", "max", weight=1.0),
        Objective("log_growth", "max", weight=1.0),
        Objective("ruin", "min", weight=2.0),
    ]
    result = optimize(cands, objs, [Constraint("ruin", "<=", 0.10)])
    ids = {c.id for c in result.pareto_frontier}
    assert "wild" not in ids  # rejected by constraint
    assert ids == {"safe", "mid"}  # both feasible, neither dominates the other
    assert result.rejected[0]["id"] == "wild"
    # scalarized ranking is present and covers only feasible candidates
    assert {row["id"] for row in result.scalarized_ranking} == {"safe", "mid"}


def test_missing_metric_raises():
    cands = [_cand("a", ret=1.0)]
    with pytest.raises(KeyError, match="missing"):
        pareto_frontier(cands, [Objective("ret", "max"), Objective("ruin", "min")])


def test_evaluate_candidates_uses_id_key():
    params = [{"name": "x", "k": 1}, {"name": "y", "k": 2}]
    cands = evaluate_candidates(
        params, lambda p: {"score": p["k"] * 10}, id_key="name"
    )
    assert [c.id for c in cands] == ["x", "y"]
    assert cands[1].metrics["score"] == 20


def test_expected_log_growth_ruin_is_negative_infinity():
    assert expected_log_growth([0.1, 0.1]) == pytest.approx(math.log1p(0.1))
    assert expected_log_growth([0.5, -1.0, 0.2]) == float("-inf")  # total loss period


def test_no_feasible_candidate_warns():
    cands = [_cand("a", ret=1, ruin=0.9), _cand("b", ret=2, ruin=0.8)]
    result = optimize(
        cands, [Objective("ret", "max"), Objective("ruin", "min")],
        [Constraint("ruin", "<=", 0.1)],
    )
    assert result.pareto_frontier == []
    assert any("no candidate satisfied" in w for w in result.warnings)
