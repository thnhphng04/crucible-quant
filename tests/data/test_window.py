"""The data window and the IS/holdout cut (P3-64, ADR-0051) — INV-126."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from quantcrucible.config.loader import ConfigError, parse_user_config
from quantcrucible.config.schema import Data
from quantcrucible.data.window import add_months, holdout_window, is_from, unclean_holdout


def test_without_a_cut_the_holdout_is_the_last_months() -> None:
    data = replace(Data(), holdout_months=12)
    assert holdout_window(data, date(2026, 10, 1)) == (date(2025, 10, 1), date(2026, 10, 2))


def test_a_cut_date_sets_the_holdout_start() -> None:
    data = replace(Data(), start=date(2022, 10, 2), holdout_start=date(2025, 10, 2))
    assert holdout_window(data, date(2026, 10, 1)) == (date(2025, 10, 2), date(2026, 10, 2))


def test_a_cut_after_the_end_or_a_holdout_under_a_month_is_refused() -> None:
    data = replace(Data(), start=date(2022, 10, 2), holdout_start=date(2026, 9, 15))
    with pytest.raises(ValueError, match="at least one month"):
        holdout_window(data, date(2026, 10, 1))


def test_add_months_clamps_to_the_month_end() -> None:
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)


def _config(**data: object) -> dict[str, object]:
    return {"research": {"holdout_pass": 1.3, "data": data}}


def test_the_loader_reads_the_cut_and_checks_it_against_the_window() -> None:
    cfg = parse_user_config(
        _config(start="2022-10-02", end="2026-10-01", holdout_start="2025-10-02")
    )
    assert cfg.research.data.holdout_start == date(2025, 10, 2)
    with pytest.raises(ConfigError, match=r"holdout_start must be after research.data.start"):
        parse_user_config(_config(start="2022-10-02", holdout_start="2022-10-02"))
    with pytest.raises(ConfigError, match="at least one month"):
        parse_user_config(_config(start="2022-10-02", end="2026-10-01", holdout_start="2026-09-15"))


def test_a_holdout_inside_a_range_research_already_saw_is_unclean() -> None:
    """D28: the ledger's trials searched up to 2025-09-20, so that day and earlier are seen."""
    assert unclean_holdout(date(2025, 9, 20), date(2025, 6, 1)) is not None
    assert unclean_holdout(date(2025, 9, 20), date(2025, 9, 20)) is not None
    assert unclean_holdout(date(2025, 9, 20), date(2025, 9, 21)) is None
    assert unclean_holdout(None, date(2020, 1, 1)) is None  # an empty ledger saw nothing
    message = unclean_holdout(date(2025, 9, 20), date(2025, 6, 1))
    assert message is not None and "2025-09-21" in message


def test_the_is_is_cut_at_the_start_with_its_perpetual_inputs() -> None:
    """A legacy store is read whole; ``is_from`` keeps bars closing at or after ``start`` 00:00
    and cuts the bundle at the same bar, funding renumbered (bars close 2021-01-02, -03, ...)."""
    import numpy as np

    from tests.validation.test_perp_portfolio import bars, bundle

    series = bars([100.0, 101.0, 102.0, 103.0, 104.0], "A/USDT:USDT")
    whole = replace(
        bundle(series),
        funding=np.array([[1.0, 0.0, 0.0001, 101.0], [3.0, 0.0, 0.0002, 103.0]]),
    )
    cut_bars, cut_bundles = is_from(date(2021, 1, 4), {"A/USDT:USDT": series},
                                    {"A/USDT:USDT": whole})  # fmt: skip
    kept = cut_bars["A/USDT:USDT"]
    assert kept.close.tolist() == [102.0, 103.0, 104.0]
    assert str(kept.ts[0].astype("datetime64[D]")) == "2021-01-04"
    perp = cut_bundles["A/USDT:USDT"]
    assert perp.marks.close.tolist() == [102.0, 103.0, 104.0] and len(perp.paths) == 3
    assert perp.funding[:, 0].tolist() == [1.0]  # bar 3 of the whole store is bar 1 of the cut
    later, _ = is_from(date(2020, 1, 1), {"A/USDT:USDT": series})
    assert len(later["A/USDT:USDT"]) == 5  # a store starting after the start stays whole
