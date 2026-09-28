from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quantcrucible.data.holdout_split import sha256_file
from quantcrucible.data.registry import DatasetInput, DatasetRegistry, DatasetSpec
from quantcrucible.studio.app import create_app
from quantcrucible.studio.jobs import JobSupervisor
from quantcrucible.studio.store import DraftStore


def test_studio_draft_requires_token_and_revision(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    capabilities = client.get("/api/studio/capabilities").json()
    token = capabilities["session_token"]
    config = capabilities["defaults"]
    headers = {"X-Studio-Token": token}

    assert client.post("/api/studio/drafts", json={"config": config}).status_code == 403
    created = client.post("/api/studio/drafts", json={"config": config}, headers=headers)
    assert created.status_code == 201
    draft = created.json()
    assert draft["revision"] == 1
    assert (
        client.patch(
            f"/api/studio/drafts/{draft['draft_id']}",
            json={"config": config},
            headers={**headers, "If-Match": '"1"'},
        ).json()["revision"]
        == 2
    )
    assert (
        client.patch(
            f"/api/studio/drafts/{draft['draft_id']}",
            json={"config": config},
            headers={**headers, "If-Match": '"1"'},
        ).status_code
        == 409
    )


def test_studio_rejects_cross_origin_mutation(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    capabilities = client.get("/api/studio/capabilities").json()
    result = client.post(
        "/api/studio/drafts",
        json={"config": capabilities["defaults"]},
        headers={
            "X-Studio-Token": capabilities["session_token"],
            "Origin": "https://elsewhere.test",
        },
    )
    assert result.status_code == 403


def test_studio_rejects_legacy_timeframe_in_a_new_draft(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    capability = client.get("/api/studio/capabilities").json()
    config = capability["defaults"]
    config["research"]["data"]["timeframe"] = "1d"
    response = client.post(
        "/api/studio/drafts",
        json={"config": config},
        headers={"X-Studio-Token": capability["session_token"]},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["fields"][0]["path"] == "research.data.timeframe"


def test_run_does_not_create_an_unknown_campaign(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    token = client.get("/api/studio/capabilities").json()["session_token"]
    response = client.post(
        "/api/studio/campaigns/c-missing/runs",
        json={"engines": ["gp"], "workers": 1, "request_key": "run-once"},
        headers={"X-Studio-Token": token},
    )
    assert response.status_code == 409
    assert client.get("/api/studio/jobs").json() == []
    assert not (tmp_path / "ledger" / "crucible.db").exists()


def test_prepare_job_can_be_enqueued_and_stopped_without_a_data_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(JobSupervisor, "start", lambda self, job_id: self.status(job_id))
    client = TestClient(create_app(tmp_path))
    capability = client.get("/api/studio/capabilities").json()
    headers = {"X-Studio-Token": capability["session_token"]}
    draft = client.post(
        "/api/studio/drafts", json={"config": capability["defaults"]}, headers=headers
    ).json()
    prepared = client.post(
        f"/api/studio/drafts/{draft['draft_id']}/prepare-data",
        json={"draft_revision": 1, "request_key": "prepare-once"},
        headers=headers,
    )
    assert prepared.status_code == 200
    assert prepared.json()["state"] == "queued"
    job_id = prepared.json()["job_id"]
    stopped = client.post(
        f"/api/studio/jobs/{job_id}/stop",
        json={"request_key": "stop-once"},
        headers=headers,
    )
    assert stopped.json()["state"] == "interrupted"
    assert client.get(f"/api/studio/jobs/{job_id}").json()["state"] == "interrupted"


def test_studio_preview_and_create_use_one_dataset_and_idempotency_key(tmp_path: Path) -> None:
    bars = tmp_path / "BTC-USDT_1h.parquet"
    bars.write_bytes(b"IS bars")
    holdout_dir = tmp_path / "holdout-input"
    holdout_dir.mkdir()
    held = holdout_dir / bars.name
    held.write_bytes(b"hidden bars")
    holdout_lock = tmp_path / "holdout-input.lock"
    holdout_lock.write_text(
        json.dumps(
            {
                "range": "2022-01-01/2023-01-02",
                "created_at": datetime.now(UTC).isoformat(),
                "files": {bars.name: sha256_file(held)},
            }
        ),
        encoding="utf-8",
    )
    dataset = DatasetRegistry(tmp_path).publish_prepared(
        DatasetSpec(
            market="spot",
            primary_exchange="binance",
            second_exchange=None,
            symbols=("BTC/USDT",),
            timeframe="1h",
            start_utc="2020-01-01T00:00:00+00:00",
            resolved_end_utc="2023-01-01T00:00:00+00:00",
            holdout_months=12,
            funding_interval_hours=8,
        ),
        [DatasetInput(bars, bars.name, "is")],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id=None,
    )
    client = TestClient(create_app(tmp_path))
    capabilities = client.get("/api/studio/capabilities").json()
    config = capabilities["defaults"]
    config["research"]["campaign"] = {"purpose": "harness_test", "trial_budget": 1}
    config["research"]["data"].update(
        symbols=["BTC/USDT"], start="2020-01-01", end="2023-01-01", second_exchange=None
    )
    config["research"]["holdout_pass"] = 1.3
    headers = {"X-Studio-Token": capabilities["session_token"]}
    created_draft = client.post(
        "/api/studio/drafts", json={"config": config}, headers=headers
    ).json()
    DraftStore(tmp_path).set_dataset(created_draft["draft_id"], 1, dataset.dataset_id)
    preview = client.post(
        f"/api/studio/drafts/{created_draft['draft_id']}/preview",
        json={"draft_revision": 1},
        headers=headers,
    )
    assert preview.status_code == 200
    assert preview.json()["ok"], preview.json()
    assert not (tmp_path / "ledger" / "crucible.db").exists()
    assert not (tmp_path / "config" / "evaluation.lock.yaml").exists()
    body = {
        "draft_id": created_draft["draft_id"],
        "draft_revision": 1,
        "preview_token": preview.json()["preview_token"],
        "request_key": "create-one",
    }
    first = client.post("/api/studio/campaigns", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert first.json()["dataset_id"] == dataset.dataset_id
    again = client.post("/api/studio/campaigns", json=body, headers=headers)
    assert again.status_code == 201
    assert again.json() == first.json()
    campaigns = client.get("/api/campaigns")
    assert campaigns.status_code == 200
    assert [row["campaign_id"] for row in campaigns.json()] == [first.json()["campaign_id"]]
