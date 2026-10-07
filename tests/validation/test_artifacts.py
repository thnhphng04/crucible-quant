"""Measurement artifact paths (ADR-0013)."""

from __future__ import annotations

from pathlib import Path

from quantcrucible.validation.artifacts import measurement_path, write_parquet_once


def test_a_perpetual_candidate_id_makes_a_valid_directory_on_every_os(tmp_path: Path) -> None:
    """A perpetual scope puts ``SOL/USDT:USDT`` into the candidate id. ``:`` is legal in a Linux
    file name and not in a Windows one, so the first 1h perpetual campaign failed every gate-③
    run on Windows with ``NotADirectoryError`` while CI stayed green."""
    path = measurement_path(tmp_path, "run-1-SOL/USDT:USDT-short-gp-s2-000024")
    assert path.parent.name == "run-1-SOL_USDT_USDT-short-gp-s2-000024"
    assert not set('<>:"/\\|?*') & set(path.parent.name)
    odd = measurement_path(tmp_path, 'a\\b|c?d*e"f<g>h')
    assert odd.parent.name == "a_b_c_d_e_f_g_h"
    import pandas as pd

    write_parquet_once(path, pd.DataFrame({"ts": [1], "ret": [0.0]}))
    assert path.is_file()
