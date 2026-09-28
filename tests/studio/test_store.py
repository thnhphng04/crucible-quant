from __future__ import annotations

from pathlib import Path

import pytest

from quantcrucible.studio.store import DraftStore, StudioConflict


def _config(timeframe: str = "1h") -> dict[str, object]:
    return {"operational": {}, "research": {"data": {"timeframe": timeframe}}}


def test_draft_revision_and_preview_are_invalidated_by_edit(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = store.create(_config())
    token, _ = store.issue_preview(draft["draft_id"], 1, {"dataset": "one"})
    changed = store.update(draft["draft_id"], 1, _config("15m"))

    assert changed["revision"] == 2
    assert changed["dataset_id"] is None
    with pytest.raises(StudioConflict):
        store.assert_preview(token, draft["draft_id"], 1, {"dataset": "one"})
    with pytest.raises(StudioConflict):
        store.update(draft["draft_id"], 1, _config())


def test_dataset_binding_survives_non_data_edit(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = store.create(_config())
    store.set_dataset(draft["draft_id"], 1, "d-123")
    updated = _config()
    updated["research"]["seeds"] = 2  # type: ignore[index]
    changed = store.update(draft["draft_id"], 1, updated)
    assert changed["dataset_id"] == "d-123"


def test_preview_token_is_bound_to_ledger_snapshot(tmp_path: Path) -> None:
    store = DraftStore(tmp_path)
    draft = store.create(_config())
    token, _ = store.issue_preview(draft["draft_id"], 1, {"ledger_n_eff": 10})
    store.assert_preview(token, draft["draft_id"], 1, {"ledger_n_eff": 10})
    with pytest.raises(StudioConflict, match="changed"):
        store.assert_preview(token, draft["draft_id"], 1, {"ledger_n_eff": 11})
