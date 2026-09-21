"""EvaluationReport (Architecture §3.3.2) — INV-46: feedback never depends on private metrics."""

import math

import pytest

from quantcrucible.validation.report import EvaluationReport, make_feedback


def test_feedback_ignores_private() -> None:
    public = {"sharpe_is": 0.8, "trades": 42.0}
    a = EvaluationReport.build(public, {"cpcv_oos_sharpe": -1.0, "pbo": 0.9})
    b = EvaluationReport.build(public, {"cpcv_oos_sharpe": 2.5, "pbo": 0.1})
    assert a.feedback == b.feedback
    assert "cpcv" not in a.feedback and "pbo" not in a.feedback


def test_feedback_cannot_be_forged() -> None:
    with pytest.raises(ValueError, match="make_feedback"):
        EvaluationReport({"sharpe_is": 0.8}, {"pbo": 0.9}, "pbo: 0.9")


def test_metric_cannot_be_both_public_and_private() -> None:
    with pytest.raises(ValueError, match="both public and private"):
        EvaluationReport.build({"x": 1.0}, {"x": 2.0})


def test_feedback_is_deterministic_and_sorted() -> None:
    fb = make_feedback({"b": 2.0, "a": 1.23456, "c": math.nan}, "boom\ntrace")
    assert fb == "error: boom\na: 1.235\nb: 2\nc: nan"


def test_json_round_trip() -> None:
    r = EvaluationReport.build({"sharpe_is": 1.5}, {"pbo": 0.2}, error=None)
    assert EvaluationReport.from_json(r.to_json()) == r
