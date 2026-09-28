from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from quantcrucible.config.schema import Data, Research, UserConfig
from quantcrucible.data.ccxt_source import CcxtSource
from quantcrucible.data.holdout_split import HoldoutLockError, sha256_file
from quantcrucible.data.registry import (
    DatasetInput,
    DatasetRegistry,
    DatasetRegistryError,
    DatasetSpec,
    compute_dataset_id,
    dataset_spec_hash,
    read_dataset_manifest,
    resolve_dataset,
)
from tests.data.conftest import FakeExchange


def _spec() -> DatasetSpec:
    return DatasetSpec(
        market="spot",
        primary_exchange="binance",
        second_exchange="gate",
        symbols=("ETH/USDT", "BTC/USDT"),
        timeframe="1h",
        start_utc="2020-01-01T00:00:00+00:00",
        resolved_end_utc="2023-01-01T00:00:00+00:00",
        holdout_months=12,
        funding_interval_hours=8,
    )


def _write_holdout(root: Path) -> tuple[Path, Path]:
    holdout_dir = root / "holdout-src"
    holdout_dir.mkdir()
    target = holdout_dir / "BTC-USDT_1h.parquet"
    target.write_bytes(b"hidden prices")
    lock = root / "holdout.lock"
    payload = {
        "created_at": "2026-01-01T00:00:00+00:00",
        "files": {target.name: sha256_file(target)},
        "range": "2022-01-01/2023-01-01",
    }
    lock.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return holdout_dir, lock


def _write_inputs(root: Path) -> tuple[Path, Path]:
    primary = root / "BTC-USDT_1h.parquet"
    second = root / "gate" / "BTC-USDT_1h.parquet"
    second.parent.mkdir()
    primary.write_bytes(b"primary bars")
    second.write_bytes(b"second bars")
    return primary, second


def test_publish_prepared_writes_v1_manifest_and_hidden_holdout(tmp_path: Path) -> None:
    primary, second = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    registry = DatasetRegistry(tmp_path)

    published = registry.publish_prepared(
        _spec(),
        [
            DatasetInput(primary, "BTC-USDT_1h.parquet", "is"),
            DatasetInput(second, "BTC-USDT_1h.parquet", "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    manifest = read_dataset_manifest(published.manifest_path)
    assert manifest.version == 1
    assert manifest.spec.symbols == ("BTC/USDT", "ETH/USDT")
    assert manifest.spec_hash == dataset_spec_hash(manifest.spec)
    assert manifest.holdout_range == "2022-01-01/2023-01-01"
    assert {item.path for item in manifest.files} == {
        "is/BTC-USDT_1h.parquet",
        "second/BTC-USDT_1h.parquet",
    }
    assert "hidden prices" not in published.manifest_path.read_text(encoding="utf-8")
    assert (tmp_path / "holdout" / "datasets" / published.dataset_id / "files").is_dir()

    expected_id = compute_dataset_id(
        manifest.spec_hash, manifest.files, json.loads(holdout_lock.read_text(encoding="utf-8"))
    )
    assert published.dataset_id == expected_id


def test_same_spec_and_bytes_reuse_existing_dataset(tmp_path: Path) -> None:
    primary, second = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    registry = DatasetRegistry(tmp_path)
    first = registry.publish_prepared(
        _spec(),
        [
            DatasetInput(primary, "BTC-USDT_1h.parquet", "is"),
            DatasetInput(second, "BTC-USDT_1h.parquet", "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
    )
    new_lock = json.loads(holdout_lock.read_text(encoding="utf-8"))
    new_lock["created_at"] = "2030-01-01T00:00:00+00:00"
    holdout_lock.write_text(json.dumps(new_lock), encoding="utf-8")
    second_publish = registry.publish_prepared(
        _spec(),
        [
            DatasetInput(primary, "BTC-USDT_1h.parquet", "is"),
            DatasetInput(second, "BTC-USDT_1h.parquet", "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
    )

    assert second_publish.dataset_id == first.dataset_id
    assert second_publish.reused


def test_verify_refuses_tampered_published_file(tmp_path: Path) -> None:
    primary, second = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    registry = DatasetRegistry(tmp_path)
    published = registry.publish_prepared(
        _spec(),
        [
            DatasetInput(primary, "BTC-USDT_1h.parquet", "is"),
            DatasetInput(second, "BTC-USDT_1h.parquet", "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
    )

    target = published.locator.is_dir / "BTC-USDT_1h.parquet"
    target.chmod(0o600)
    target.write_bytes(b"changed")

    with pytest.raises(DatasetRegistryError, match="checksum"):
        registry.verify_dataset(published.dataset_id)


def test_research_resolver_does_not_open_holdout_price_files(tmp_path: Path) -> None:
    primary, second = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    published = DatasetRegistry(tmp_path).publish_prepared(
        _spec(),
        [
            DatasetInput(primary, primary.name, "is"),
            DatasetInput(second, second.name, "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
    )
    hidden = published.locator.holdout_dir / "BTC-USDT_1h.parquet"
    hidden.chmod(0o600)
    hidden.write_bytes(b"tampered hidden prices")
    lock = {
        "derived": {
            "dataset_id": published.dataset_id,
            "manifest_sha256": published.manifest_sha256,
            "holdout_lock_sha256": published.holdout_lock_sha256,
        }
    }
    assert resolve_dataset(tmp_path, lock).is_dir == published.locator.is_dir
    with pytest.raises(HoldoutLockError):
        DatasetRegistry(tmp_path).verify_dataset(published.dataset_id)


def test_relative_paths_cannot_escape_dataset(tmp_path: Path) -> None:
    primary, _ = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    registry = DatasetRegistry(tmp_path)

    with pytest.raises(DatasetRegistryError, match="safe relative"):
        registry.publish_prepared(
            _spec(),
            [DatasetInput(primary, "../escape.parquet", "is")],
            holdout_files_dir=holdout_dir,
            holdout_lock_path=holdout_lock,
            primary_source_id="binance",
            second_source_id="gate",
        )


def test_resolve_new_dataset_checks_lock_hashes(tmp_path: Path) -> None:
    primary, second = _write_inputs(tmp_path)
    holdout_dir, holdout_lock = _write_holdout(tmp_path)
    published = DatasetRegistry(tmp_path).publish_prepared(
        _spec(),
        [
            DatasetInput(primary, "BTC-USDT_1h.parquet", "is"),
            DatasetInput(second, "BTC-USDT_1h.parquet", "second"),
        ],
        holdout_files_dir=holdout_dir,
        holdout_lock_path=holdout_lock,
        primary_source_id="binance",
        second_source_id="gate",
    )

    locator = resolve_dataset(
        tmp_path,
        {
            "dataset_id": published.dataset_id,
            "manifest_sha256": published.manifest_sha256,
            "holdout_lock_sha256": published.holdout_lock_sha256,
        },
    )
    assert locator.kind == "v1"
    assert locator.is_dir == tmp_path / "data" / "datasets" / published.dataset_id / "is"
    from_derived = resolve_dataset(
        tmp_path,
        {
            "derived": {
                "dataset_id": published.dataset_id,
                "manifest_sha256": published.manifest_sha256,
                "holdout_lock_sha256": published.holdout_lock_sha256,
            }
        },
    )
    assert from_derived == locator

    with pytest.raises(DatasetRegistryError, match="manifest hash"):
        resolve_dataset(tmp_path, {"dataset_id": published.dataset_id, "manifest_sha256": "0" * 64})


def test_resolve_legacy_spot_and_perp_paths(tmp_path: Path) -> None:
    spot = resolve_dataset(
        tmp_path, {"research": {"data": {"market": "spot", "second_exchange": "gate"}}}
    )
    perp = resolve_dataset(
        tmp_path,
        {"research": {"data": {"market": "usdt_m_perpetual", "second_exchange": "gate"}}},
    )

    assert spot.kind == "legacy"
    assert spot.is_dir == tmp_path / "data" / "is"
    assert spot.holdout_lock == tmp_path / "holdout.lock"
    assert spot.second_dir == tmp_path / "data" / "is-gate"
    assert perp.kind == "legacy"
    assert perp.is_dir == tmp_path / "data" / "perp"
    assert perp.holdout_lock == tmp_path / "holdout" / "perp.lock"
    assert perp.second_dir == tmp_path / "data" / "perp-second-gate"


def test_prepare_and_get_descriptor_from_existing_prepared_spot_layout(tmp_path: Path) -> None:
    is_dir = tmp_path / "data" / "is"
    is_dir.mkdir(parents=True)
    (is_dir / "BTC-USDT_1h.parquet").write_bytes(b"bars")
    holdout_dir = tmp_path / "holdout"
    holdout_dir.mkdir()
    holdout_file = holdout_dir / "BTC-USDT_1h.parquet"
    holdout_file.write_bytes(b"holdout")
    (tmp_path / "holdout.lock").write_text(
        json.dumps(
            {
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": {holdout_file.name: sha256_file(holdout_file)},
                "range": "2022-01-01/2023-01-01",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    cfg = UserConfig(
        research=replace(
            Research(),
            data=replace(
                Data(),
                symbols=("BTC/USDT",),
                timeframe="1h",
                start=date(2020, 1, 1),
                end=date(2023, 1, 1),
                second_exchange=None,
            ),
        )
    )

    registry = DatasetRegistry(tmp_path)
    descriptor = registry.prepare(cfg)
    same = registry.get(descriptor.dataset_id)

    assert descriptor.dataset_id == same.dataset_id
    assert descriptor.is_dir == same.is_dir
    assert descriptor.second_dir is None
    assert descriptor.holdout_lock_path.name == "holdout.lock"
    assert descriptor.holdout_range == "2022-01-01/2023-01-01"


def test_fetch_and_publish_uses_isolated_staging_not_legacy_roots(tmp_path: Path) -> None:
    legacy_lock = tmp_path / "holdout.lock"
    legacy_lock.write_text("legacy lock stays untouched", encoding="utf-8")
    cfg = UserConfig(
        research=replace(
            Research(),
            data=replace(
                Data(),
                symbols=("BTC/USDT",),
                timeframe="1d",
                start=date(2020, 1, 2),
                end=date(2023, 1, 1),
                second_exchange="gate",
            ),
        )
    )

    descriptor = DatasetRegistry(tmp_path).fetch_and_publish(
        cfg,
        primary_source=CcxtSource(exchange=FakeExchange()),
        second_source=CcxtSource(exchange=FakeExchange()),
    )
    manifest = read_dataset_manifest(descriptor.manifest_path)

    assert legacy_lock.read_text(encoding="utf-8") == "legacy lock stays untouched"
    assert not (tmp_path / "data" / "is").exists()
    assert not (tmp_path / "data" / "second").exists()
    assert descriptor.is_dir == tmp_path / "data" / "datasets" / descriptor.dataset_id / "is"
    assert descriptor.second_dir == (
        tmp_path / "data" / "datasets" / descriptor.dataset_id / "second"
    )
    assert descriptor.holdout_lock_path == (
        tmp_path / "holdout" / "datasets" / descriptor.dataset_id / "holdout.lock"
    )
    assert descriptor.holdout_range == "2022-01-01/2023-01-02"
    assert {item.section for item in manifest.files} == {"is", "second"}
    assert manifest.coverage["primary:BTC/USDT"].endswith("2023-01-01T00:00:00+00:00")


def test_fetch_refuses_a_missing_bar_in_the_requested_window(tmp_path: Path) -> None:
    cfg = UserConfig(
        research=replace(
            Research(),
            data=replace(
                Data(),
                symbols=("BTC/USDT",),
                timeframe="1d",
                start=date(2020, 1, 2),
                end=date(2023, 1, 1),
                second_exchange=None,
            ),
        )
    )
    exchange = FakeExchange()
    exchange.rows.pop(200)
    with pytest.raises(DatasetRegistryError, match="missing"):
        DatasetRegistry(tmp_path).fetch_and_publish(
            cfg, primary_source=CcxtSource(exchange=exchange)
        )
    assert not list((tmp_path / "data" / "datasets").glob("[0-9a-f]*"))
