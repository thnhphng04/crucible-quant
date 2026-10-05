"""Immutable dataset registry for campaign data bundles.

The registry gives new campaigns a content-addressed dataset without changing the legacy
``data/is`` and ``data/perp`` readers. A v1 dataset is published under
``data/datasets/<dataset_id>/`` with a manifest, while holdout bytes live separately under
``holdout/datasets/<dataset_id>/`` so callers do not need to expose holdout paths to a browser.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from quantcrucible.data.holdout_split import make_read_only, read_holdout_lock, verify_holdout
from quantcrucible.data.manifest import sha256_file
from quantcrucible.data.window import holdout_window, unclean_holdout

DATASET_MANIFEST_VERSION = 1
Section = Literal["is", "second"]

if TYPE_CHECKING:
    from quantcrucible.config.schema import UserConfig
    from quantcrucible.core.strategy.base import Bars


class DatasetRegistryError(RuntimeError):
    """A dataset cannot be published, resolved, or trusted."""


@dataclass(frozen=True, slots=True)
class DatasetSpec:
    market: str
    primary_exchange: str
    second_exchange: str | None
    symbols: tuple[str, ...]
    timeframe: str
    start_utc: str
    resolved_end_utc: str
    holdout_months: int
    funding_interval_hours: int | None = None
    # The IS/holdout cut (ADR-0051); None = the last ``holdout_months``. Hashed only when set.
    holdout_start_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(sorted(self.symbols)))
        if not self.symbols:
            raise ValueError("symbols must not be empty")
        if self.holdout_months <= 0:
            raise ValueError("holdout_months must be positive")
        _parse_utc(self.start_utc, "start_utc")
        _parse_utc(self.resolved_end_utc, "resolved_end_utc")
        if self.holdout_start_utc is not None:
            _parse_utc(self.holdout_start_utc, "holdout_start_utc")


@dataclass(frozen=True, slots=True)
class DatasetInput:
    source: Path
    relative_path: str
    section: Section = "is"


@dataclass(frozen=True, slots=True)
class DatasetFile:
    section: Section
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    version: int
    dataset_id: str
    spec: DatasetSpec
    spec_hash: str
    primary_source_id: str
    second_source_id: str | None
    files: tuple[DatasetFile, ...]
    holdout_range: str
    holdout_lock_sha256: str
    created_at: str
    coverage: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DatasetLocator:
    kind: Literal["v1", "legacy"]
    market: str
    is_dir: Path
    holdout_dir: Path
    holdout_lock: Path
    dataset_id: str | None = None
    manifest_path: Path | None = None
    second_dir: Path | None = None
    manifest_sha256: str | None = None
    holdout_lock_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetDescriptor:
    dataset_id: str
    is_dir: Path
    second_dir: Path | None
    holdout_lock_path: Path
    holdout_dir: Path
    manifest_path: Path
    manifest_sha256: str
    holdout_range: str
    holdout_lock_sha256: str
    coverage: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublishedDataset:
    dataset_id: str
    manifest_path: Path
    manifest_sha256: str
    holdout_lock: Path
    holdout_lock_sha256: str
    locator: DatasetLocator
    reused: bool = False


@dataclass(frozen=True, slots=True)
class _StagedFile:
    section: Section
    path: str
    absolute_path: Path
    sha256: str


@dataclass(slots=True)
class _Stage:
    token: str
    data_root: Path
    holdout_root: Path
    files: list[_StagedFile] = field(default_factory=list)


def canonical_json_bytes(value: object) -> bytes:
    """Canonical JSON bytes used for dataset hashes."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def dataset_spec_hash(spec: DatasetSpec) -> str:
    return hashlib.sha256(canonical_json_bytes(_spec_payload(spec))).hexdigest()


def compute_dataset_id(
    spec_hash: str, files: Sequence[DatasetFile], holdout_manifest: Mapping[str, Any]
) -> str:
    payload = {
        "spec_hash": spec_hash,
        "files": [
            {"path": f.path, "section": f.section, "sha256": f.sha256}
            for f in sorted(files, key=lambda item: (item.section, item.path))
        ],
        "holdout": {
            "range": holdout_manifest["range"],
            "files": dict(sorted(holdout_manifest["files"].items())),
        },
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


class DatasetRegistry:
    """Stage, publish, verify and resolve v1 datasets under one project root."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.data_root = project_root / "data" / "datasets"
        self.holdout_root = project_root / "holdout" / "datasets"

    def publish_prepared(
        self,
        spec: DatasetSpec,
        files: Sequence[DatasetInput],
        *,
        holdout_files_dir: Path,
        holdout_lock_path: Path,
        primary_source_id: str,
        second_source_id: str | None = None,
        created_at: datetime | None = None,
        coverage: Mapping[str, str] | None = None,
    ) -> PublishedDataset:
        """Publish prepared in-sample/second files and a write-once holdout lock.

        ``holdout_files_dir`` is copied but never recorded in the dataset manifest; the lock hash
        is the public handle for that hidden side of the dataset.
        """

        if not files:
            raise DatasetRegistryError("dataset must contain at least one in-sample file")
        if not holdout_lock_path.is_file():
            raise DatasetRegistryError(f"{holdout_lock_path} is not a holdout lock")
        if not holdout_files_dir.is_dir():
            raise DatasetRegistryError(f"{holdout_files_dir} is not a holdout directory")
        if second_source_id is None and any(item.section == "second" for item in files):
            raise DatasetRegistryError("second_source_id is required when second files are present")

        stage = self._stage_inputs(files, holdout_files_dir, holdout_lock_path)
        try:
            holdout_lock_sha = sha256_file(stage.holdout_root / "holdout.lock")
            spec_sha = dataset_spec_hash(spec)
            manifest_files = tuple(
                DatasetFile(item.section, item.path, item.sha256)
                for item in sorted(stage.files, key=lambda f: (f.section, f.path))
            )
            holdout_manifest = read_holdout_lock(stage.holdout_root / "holdout.lock")
            dataset_id = compute_dataset_id(spec_sha, manifest_files, holdout_manifest)
            existing = self.data_root / dataset_id
            if existing.exists():
                self._discard_stage(stage)
                return self.verify_dataset(dataset_id, reused=True)

            manifest = DatasetManifest(
                version=DATASET_MANIFEST_VERSION,
                dataset_id=dataset_id,
                spec=spec,
                spec_hash=spec_sha,
                primary_source_id=primary_source_id,
                second_source_id=second_source_id,
                files=manifest_files,
                holdout_range=str(holdout_manifest["range"]),
                holdout_lock_sha256=holdout_lock_sha,
                created_at=(created_at or datetime.now(UTC)).isoformat(),
                coverage=dict(sorted((coverage or {}).items())),
            )
            self._write_manifest(stage.data_root / "manifest.json", manifest)
            data_target = self.data_root / dataset_id
            holdout_target = self.holdout_root / dataset_id
            if data_target.exists() or holdout_target.exists():
                raise DatasetRegistryError(f"dataset {dataset_id} is partially published")
            _publish_directory(stage.data_root, data_target)
            _publish_directory(stage.holdout_root, holdout_target)
            self._make_tree_read_only(self.data_root / dataset_id)
            self._make_tree_read_only(self.holdout_root / dataset_id)
            return self.verify_dataset(dataset_id)
        except Exception:
            self._discard_stage(stage)
            raise

    def prepare(self, cfg: UserConfig) -> DatasetDescriptor:
        """Synchronously publish the prepared files for ``cfg`` and return a descriptor.

        This is intentionally a thin interface for studio jobs: data fetching can happen before
        this call, then ``prepare`` fingerprints and publishes the immutable dataset.
        """

        data = cfg.research.data
        market = str(data.market)
        resolved_end = _resolved_end_utc(data.end)
        spec = DatasetSpec(
            market=market,
            primary_exchange=str(data.exchange),
            second_exchange=str(data.second_exchange) if data.second_exchange is not None else None,
            symbols=tuple(str(symbol) for symbol in data.symbols),
            timeframe=str(data.timeframe),
            start_utc=_date_start_utc(data.start),
            resolved_end_utc=resolved_end,
            holdout_months=int(data.holdout_months),
            holdout_start_utc=(
                _date_start_utc(data.holdout_start) if data.holdout_start is not None else None
            ),
            funding_interval_hours=(
                int(data.funding_interval_hours)
                if getattr(data, "funding_interval_hours", None) is not None
                else None
            ),
        )
        files = self._inputs_from_prepared_layout(market, data.second_exchange is not None)
        holdout_dir, holdout_lock = self._prepared_holdout_paths(market)
        published = self.publish_prepared(
            spec,
            files,
            holdout_files_dir=holdout_dir,
            holdout_lock_path=holdout_lock,
            primary_source_id=str(data.exchange),
            second_source_id=(
                str(data.second_exchange) if data.second_exchange is not None else None
            ),
        )
        return self.descriptor(published.dataset_id)

    def fetch_and_publish(
        self,
        cfg: UserConfig,
        *,
        primary_source: Any | None = None,
        second_source: Any | None = None,
        perp_source: Any | None = None,
        second_perp_source: Any | None = None,
        latest_is_end: date | None = None,
    ) -> DatasetDescriptor:
        """Fetch into isolated staging paths, carve there, then publish immutably.

        ``latest_is_end`` is the last day research has already searched (``Ledger.latest_is_end``):
        a holdout reaching back into it is refused before anything is downloaded (D28).

        This is the studio prepare-data job entry point. It never writes ``data/is``,
        ``data/perp``, root ``holdout.lock``, or the legacy second-source directories.
        """

        data = cfg.research.data
        market = str(data.market)
        end_day = _resolved_end_day(data.end)
        start = datetime.combine(data.start, time(), tzinfo=UTC)
        end = datetime.combine(end_day, time(), tzinfo=UTC)
        try:
            holdout_start, holdout_end = holdout_window(data, end_day)
        except ValueError as e:
            raise DatasetRegistryError(str(e)) from None
        problem = unclean_holdout(latest_is_end, holdout_start)
        if problem is not None:
            raise DatasetRegistryError(problem)
        token = uuid.uuid4().hex
        fetch_data = self.data_root / ".fetching" / token
        fetch_holdout = self.holdout_root / ".fetching" / token
        try:
            if _is_perp_market(market):
                coverage = self._fetch_perp(
                    data,
                    start,
                    end,
                    holdout_start,
                    holdout_end,
                    fetch_data,
                    fetch_holdout,
                    perp_source=perp_source,
                    second_perp_source=second_perp_source,
                )
            else:
                coverage = self._fetch_spot(
                    data,
                    start,
                    end,
                    holdout_start,
                    holdout_end,
                    fetch_data,
                    fetch_holdout,
                    primary_source=primary_source,
                    second_source=second_source,
                )

            spec = DatasetSpec(
                market=market,
                primary_exchange=str(data.exchange),
                second_exchange=(
                    str(data.second_exchange) if data.second_exchange is not None else None
                ),
                symbols=tuple(str(symbol) for symbol in data.symbols),
                timeframe=str(data.timeframe),
                start_utc=_date_start_utc(data.start),
                resolved_end_utc=_date_start_utc(end_day),
                holdout_months=int(data.holdout_months),
                holdout_start_utc=(
                    _date_start_utc(data.holdout_start) if data.holdout_start is not None else None
                ),
                funding_interval_hours=(
                    int(data.funding_interval_hours)
                    if getattr(data, "funding_interval_hours", None) is not None
                    else None
                ),
            )
            files = _inputs_from_directory(fetch_data / "is", "is") + _inputs_from_directory(
                fetch_data / "second", "second"
            )
            published = self.publish_prepared(
                spec,
                files,
                holdout_files_dir=fetch_holdout / "files",
                holdout_lock_path=fetch_holdout / "holdout.lock",
                primary_source_id=str(data.exchange),
                second_source_id=(
                    str(data.second_exchange) if data.second_exchange is not None else None
                ),
                coverage=coverage,
            )
            return self.descriptor(published.dataset_id)
        finally:
            for path in (fetch_data, fetch_holdout):
                if path.exists():
                    _make_tree_writable(path)
                    shutil.rmtree(path)

    def get(self, dataset_id: str) -> DatasetDescriptor:
        """Return the studio-facing descriptor for a published dataset."""

        return self.descriptor(dataset_id)

    def descriptor(self, dataset_id: str) -> DatasetDescriptor:
        published = self.verify_dataset(dataset_id)
        manifest = read_dataset_manifest(published.manifest_path)
        return DatasetDescriptor(
            dataset_id=dataset_id,
            is_dir=published.locator.is_dir,
            second_dir=published.locator.second_dir,
            holdout_lock_path=published.holdout_lock,
            holdout_dir=published.locator.holdout_dir,
            manifest_path=published.manifest_path,
            manifest_sha256=published.manifest_sha256,
            holdout_range=manifest.holdout_range,
            holdout_lock_sha256=published.holdout_lock_sha256,
            coverage=manifest.coverage,
        )

    def verify_dataset(
        self, dataset_id: str, *, reused: bool = False, verify_holdout_files: bool = True
    ) -> PublishedDataset:
        dataset_path = _dataset_id_path(self.data_root, dataset_id)
        holdout_path = _dataset_id_path(self.holdout_root, dataset_id)
        manifest_path = dataset_path / "manifest.json"
        if not manifest_path.is_file():
            raise DatasetRegistryError(f"dataset {dataset_id} has no manifest")
        if not holdout_path.is_dir():
            raise DatasetRegistryError(f"dataset {dataset_id} has no holdout directory")
        manifest = read_dataset_manifest(manifest_path)
        if manifest.version != DATASET_MANIFEST_VERSION:
            raise DatasetRegistryError(f"unsupported dataset manifest v{manifest.version}")
        if manifest.dataset_id != dataset_id:
            raise DatasetRegistryError("manifest dataset_id does not match its directory")
        if manifest.spec_hash != dataset_spec_hash(manifest.spec):
            raise DatasetRegistryError("manifest spec_hash does not match its spec")
        for entry in manifest.files:
            rel = _safe_relative_path(entry.path)
            if entry.section == "is" and rel.parts[0] != "is":
                raise DatasetRegistryError(f"{entry.path} is not under is/")
            if entry.section == "second" and rel.parts[0] != "second":
                raise DatasetRegistryError(f"{entry.path} is not under second/")
            path = _safe_join(dataset_path, entry.path)
            if not path.is_file() or path.is_symlink():
                raise DatasetRegistryError(f"{entry.path} is missing from dataset {dataset_id}")
            if sha256_file(path) != entry.sha256:
                raise DatasetRegistryError(f"{entry.path} checksum does not match manifest")
        holdout_lock = holdout_path / "holdout.lock"
        if not holdout_lock.is_file():
            raise DatasetRegistryError(f"dataset {dataset_id} has no holdout.lock")
        if sha256_file(holdout_lock) != manifest.holdout_lock_sha256:
            raise DatasetRegistryError("holdout.lock checksum does not match manifest")
        if (
            compute_dataset_id(manifest.spec_hash, manifest.files, read_holdout_lock(holdout_lock))
            != dataset_id
        ):
            raise DatasetRegistryError("dataset id does not match its content")
        if verify_holdout_files:
            verify_holdout(holdout_lock, holdout_path / "files")
        manifest_sha = sha256_file(manifest_path)
        locator = DatasetLocator(
            kind="v1",
            market=manifest.spec.market,
            dataset_id=dataset_id,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha,
            is_dir=dataset_path / "is",
            second_dir=(dataset_path / "second" if (dataset_path / "second").exists() else None),
            holdout_dir=holdout_path / "files",
            holdout_lock=holdout_lock,
            holdout_lock_sha256=manifest.holdout_lock_sha256,
        )
        return PublishedDataset(
            dataset_id=dataset_id,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha,
            holdout_lock=holdout_lock,
            holdout_lock_sha256=manifest.holdout_lock_sha256,
            locator=locator,
            reused=reused,
        )

    def resolve(self, lock: Mapping[str, Any]) -> DatasetLocator:
        return resolve_dataset(self.project_root, lock)

    def _inputs_from_prepared_layout(
        self, market: str, include_second: bool
    ) -> tuple[DatasetInput, ...]:
        primary_dir = self.project_root / "data" / ("perp" if _is_perp_market(market) else "is")
        if not primary_dir.is_dir():
            raise DatasetRegistryError(
                f"{primary_dir} has no prepared files; run the data preparation job first"
            )
        inputs = [
            DatasetInput(path, path.relative_to(primary_dir).as_posix(), "is")
            for path in sorted(primary_dir.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        ]
        if include_second:
            second_dir = self.project_root / "data" / "second"
            if not second_dir.is_dir():
                raise DatasetRegistryError(
                    f"{second_dir} has no prepared files for the second source"
                )
            inputs.extend(
                DatasetInput(path, path.relative_to(second_dir).as_posix(), "second")
                for path in sorted(second_dir.rglob("*"))
                if path.is_file() and path.name != "manifest.json"
            )
        return tuple(inputs)

    def _prepared_holdout_paths(self, market: str) -> tuple[Path, Path]:
        if _is_perp_market(market):
            return (
                self.project_root / "holdout" / "perp",
                self.project_root / "holdout" / "perp.lock",
            )
        return self.project_root / "holdout", self.project_root / "holdout.lock"

    def _fetch_spot(
        self,
        data: Any,
        start: datetime,
        end: datetime,
        holdout_start: date,
        holdout_end: date,
        fetch_data: Path,
        fetch_holdout: Path,
        *,
        primary_source: Any | None,
        second_source: Any | None,
    ) -> dict[str, str]:
        from quantcrucible.data.ccxt_source import CcxtSource
        from quantcrucible.data.holdout_split import carve
        from quantcrucible.data.second_source import download_in_sample
        from quantcrucible.data.source import timeframe_delta
        from quantcrucible.data.store import file_name, parse_range, read_bars

        source = primary_source or CcxtSource(str(data.exchange))
        bars = {s: source.bars(s, data.timeframe, start, end) for s in data.symbols}
        step = timeframe_delta(data.timeframe)
        coverage = {
            f"primary:{symbol}": _bars_coverage(series, step, start, end, symbol)
            for symbol, series in bars.items()
        }
        summary = carve(
            bars,
            holdout_start,
            holdout_end,
            in_sample_dir=fetch_data / "is",
            holdout_dir=fetch_holdout / "files",
            lock_path=fetch_holdout / "holdout.lock",
            harden=False,
        )
        if data.second_exchange is not None:
            vendor = second_source or CcxtSource(str(data.second_exchange))
            download_in_sample(
                vendor,
                data.symbols,
                data.timeframe,
                data.start,
                [parse_range(summary.holdout_range)],
                fetch_data / "second",
            )
            second_end = datetime.combine(holdout_start, time(), tzinfo=UTC) - step
            for symbol in data.symbols:
                second_bars = read_bars(
                    fetch_data / "second" / file_name(symbol, data.timeframe),
                    symbol,
                    data.timeframe,
                )
                coverage[f"second:{symbol}"] = _bars_coverage(
                    second_bars, step, start, second_end, symbol
                )
        return coverage

    def _fetch_perp(
        self,
        data: Any,
        start: datetime,
        end: datetime,
        holdout_start: date,
        holdout_end: date,
        fetch_data: Path,
        fetch_holdout: Path,
        *,
        perp_source: Any | None,
        second_perp_source: Any | None,
    ) -> dict[str, str]:
        from quantcrucible.core.perp_inputs import write_bundle
        from quantcrucible.data.perp_carve import carve_perp
        from quantcrucible.data.perp_pipeline import prepare_perpetual
        from quantcrucible.data.perp_source import PerpSource, common_window
        from quantcrucible.data.source import timeframe_delta
        from quantcrucible.data.store import file_name, write_bars

        source = perp_source or PerpSource(exchange_id=str(data.exchange))
        prepared = prepare_perpetual(
            source,
            fetch_data / "minutes-primary",
            data.symbols,
            data.timeframe,
            start,
            end,
            funding_interval_hours=int(data.funding_interval_hours),
        )
        common_start, common_end = common_window(
            {
                symbol: (coverage.start, coverage.end)
                for symbol, coverage in prepared.coverage.items()
            }
        )
        if common_start != start or common_end != end:
            raise DatasetRegistryError("perpetual series do not cover the configured common window")
        coverage = {
            f"primary:{symbol}": f"{item.start.isoformat()}/{item.end.isoformat()}"
            for symbol, item in prepared.coverage.items()
        }
        carve_perp(
            prepared.data,
            holdout_start,
            holdout_end,
            in_sample_dir=fetch_data / "is",
            holdout_dir=fetch_holdout / "files",
            lock_path=fetch_holdout / "holdout.lock",
            harden=False,
        )
        if data.second_exchange is not None:
            second_source = second_perp_source or PerpSource(exchange_id=str(data.second_exchange))
            cut_end = datetime.combine(holdout_start, time(), tzinfo=UTC) - timeframe_delta(
                data.timeframe
            )
            second = prepare_perpetual(
                second_source,
                fetch_data / "minutes-second",
                data.symbols,
                data.timeframe,
                start,
                cut_end,
                funding_interval_hours=int(data.funding_interval_hours),
            )
            for symbol, item in second.coverage.items():
                if item.start != start or item.end != cut_end:
                    raise DatasetRegistryError(
                        f"{symbol}: second-source perpetual coverage does not match IS window"
                    )
            out_dir = fetch_data / "second"
            for symbol, (trade, bundle) in second.data.items():
                write_bars(out_dir / file_name(symbol, data.timeframe), trade)
                write_bundle(out_dir, bundle)
            coverage.update(
                {
                    f"second:{symbol}": f"{item.start.isoformat()}/{item.end.isoformat()}"
                    for symbol, item in second.coverage.items()
                }
            )
        return coverage

    def _stage_inputs(
        self,
        files: Sequence[DatasetInput],
        holdout_files_dir: Path,
        holdout_lock_path: Path,
    ) -> _Stage:
        token = uuid.uuid4().hex
        stage = _Stage(
            token=token,
            data_root=self.data_root / ".staging" / token,
            holdout_root=self.holdout_root / ".staging" / token,
        )
        seen: set[str] = set()
        for item in files:
            rel = _manifest_relative(item.section, item.relative_path)
            if rel in seen:
                raise DatasetRegistryError(f"duplicate dataset file {rel}")
            seen.add(rel)
            source = item.source
            if not source.is_file() or source.is_symlink():
                raise DatasetRegistryError(f"{source} is not a regular file")
            target = _safe_join(stage.data_root, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            stage.files.append(_StagedFile(item.section, rel, target, sha256_file(target)))
        holdout_target = stage.holdout_root / "files"
        shutil.copytree(
            holdout_files_dir,
            holdout_target,
            ignore=shutil.ignore_patterns("holdout.lock"),
            copy_function=shutil.copy2,
            symlinks=False,
        )
        _reject_tree_symlinks(holdout_target)
        lock_target = stage.holdout_root / "holdout.lock"
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(holdout_lock_path, lock_target)
        verify_holdout(lock_target, holdout_target)
        return stage

    def _write_manifest(self, path: Path, manifest: DatasetManifest) -> None:
        path.write_text(
            json.dumps(_manifest_payload(manifest), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _discard_stage(self, stage: _Stage) -> None:
        for path in (stage.data_root, stage.holdout_root):
            if path.exists():
                _make_tree_writable(path)
                shutil.rmtree(path)

    def _make_tree_read_only(self, root: Path) -> None:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                make_read_only(path, harden=False)


def read_dataset_manifest(path: Path) -> DatasetManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = DatasetSpec(**payload["spec"])
    files = tuple(DatasetFile(**item) for item in payload["files"])
    return DatasetManifest(
        version=int(payload["version"]),
        dataset_id=str(payload["dataset_id"]),
        spec=spec,
        spec_hash=str(payload["spec_hash"]),
        primary_source_id=str(payload["primary_source_id"]),
        second_source_id=(
            str(payload["second_source_id"])
            if payload.get("second_source_id") is not None
            else None
        ),
        files=files,
        holdout_range=str(payload["holdout_range"]),
        holdout_lock_sha256=str(payload["holdout_lock_sha256"]),
        created_at=str(payload["created_at"]),
        coverage={str(k): str(v) for k, v in payload.get("coverage", {}).items()},
    )


def resolve_dataset(project_root: Path, lock: Mapping[str, Any]) -> DatasetLocator:
    derived = lock.get("derived", {})
    if not isinstance(derived, Mapping):
        derived = {}
    dataset_id = derived.get("dataset_id") or lock.get("dataset_id")
    if dataset_id:
        registry = DatasetRegistry(project_root)
        published = registry.verify_dataset(str(dataset_id), verify_holdout_files=False)
        manifest_hash = derived.get("manifest_sha256") or lock.get("manifest_sha256")
        if manifest_hash is not None and manifest_hash != published.manifest_sha256:
            raise DatasetRegistryError("dataset manifest hash does not match the campaign lock")
        holdout_hash = (
            derived.get("holdout_lock_sha256")
            or lock.get("holdout_lock_sha256")
            or lock.get("holdout_lock_hash")
        )
        if holdout_hash is not None and holdout_hash != published.holdout_lock_sha256:
            raise DatasetRegistryError("dataset holdout lock hash does not match the campaign lock")
        return published.locator
    return resolve_legacy_dataset(project_root, lock)


def resolve_legacy_dataset(project_root: Path, lock: Mapping[str, Any]) -> DatasetLocator:
    market = _lock_market(lock)
    data = lock.get("research", {}).get("data", {})
    second_exchange = data.get("second_exchange") if isinstance(data, dict) else None
    if market in {"usdt_m_perpetual", "perpetual", "perp"}:
        return DatasetLocator(
            kind="legacy",
            market=market,
            is_dir=project_root / "data" / "perp",
            second_dir=(
                project_root / "data" / f"perp-second-{second_exchange}"
                if second_exchange
                else None
            ),
            holdout_dir=project_root / "holdout" / "perp",
            holdout_lock=project_root / "holdout" / "perp.lock",
        )
    return DatasetLocator(
        kind="legacy",
        market=market,
        is_dir=project_root / "data" / "is",
        second_dir=(project_root / "data" / f"is-{second_exchange}" if second_exchange else None),
        holdout_dir=project_root / "holdout",
        holdout_lock=project_root / "holdout.lock",
    )


def _spec_payload(spec: DatasetSpec) -> dict[str, Any]:
    payload = asdict(spec)
    if payload["holdout_start_utc"] is None:  # a dataset published before the cut existed
        del payload["holdout_start_utc"]
    return payload


def _bars_coverage(
    bars: Bars, step: timedelta, requested_start: datetime, requested_end: datetime, symbol: str
) -> str:
    """Record the actual closed-bar window and refuse a hole that changes the sample length."""
    closes = bars.ts.astype("datetime64[ns]").astype(np.int64)
    if len(closes) == 0:
        raise DatasetRegistryError(f"{symbol}: no bars in the requested window")
    step_ns = int(step.total_seconds() * 1_000_000_000)
    start_ns = int(requested_start.timestamp() * 1_000_000_000)
    end_ns = int(requested_end.timestamp() * 1_000_000_000)
    if int(closes[0]) > start_ns + step_ns or int(closes[-1]) < end_ns:
        raise DatasetRegistryError(f"{symbol}: bars do not cover the configured window")
    if len(closes) > 1 and bool(np.any(np.diff(closes) != step_ns)):
        raise DatasetRegistryError(f"{symbol}: a bar is missing inside the configured window")
    first_open = datetime.fromtimestamp((int(closes[0]) - step_ns) / 1e9, UTC)
    last_close = datetime.fromtimestamp(int(closes[-1]) / 1e9, UTC)
    return f"{first_open.isoformat()}/{last_close.isoformat()}"


def _manifest_payload(manifest: DatasetManifest) -> dict[str, Any]:
    return {
        "version": manifest.version,
        "dataset_id": manifest.dataset_id,
        "spec": _spec_payload(manifest.spec),
        "spec_hash": manifest.spec_hash,
        "primary_source_id": manifest.primary_source_id,
        "second_source_id": manifest.second_source_id,
        "files": [asdict(item) for item in manifest.files],
        "holdout_range": manifest.holdout_range,
        "holdout_lock_sha256": manifest.holdout_lock_sha256,
        "created_at": manifest.created_at,
        "coverage": dict(sorted(manifest.coverage.items())),
    }


def _parse_utc(value: str, field_name: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError(f"{field_name} must be an aware UTC timestamp")
    return parsed


def _date_start_utc(value: Any) -> str:
    return datetime(value.year, value.month, value.day, tzinfo=UTC).isoformat()


def _resolved_end_utc(value: Any) -> str:
    if value is None:
        now = datetime.now(UTC)
        return datetime(now.year, now.month, now.day, tzinfo=UTC).isoformat()
    return _date_start_utc(value)


def _resolved_end_day(value: Any) -> date:
    today = datetime.now(UTC).date()
    end_day = value or today
    if end_day > today:
        raise DatasetRegistryError("research.data.end cannot be in the future")
    return end_day


def _inputs_from_directory(root: Path, section: Section) -> tuple[DatasetInput, ...]:
    if not root.exists():
        return ()
    return tuple(
        DatasetInput(path, path.relative_to(root).as_posix(), section)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    )


def _manifest_relative(section: Section, relative_path: str) -> str:
    rel = _safe_relative_path(relative_path)
    if rel.parts and rel.parts[0] in {"is", "second"}:
        if rel.parts[0] != section:
            raise DatasetRegistryError(f"{relative_path} is not under {section}/")
        return rel.as_posix()
    return Path(section, rel).as_posix()


def _safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise DatasetRegistryError(f"{value} must be a relative path")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise DatasetRegistryError(f"{value} is not a safe relative path")
    return path


def _safe_join(root: Path, relative_path: str) -> Path:
    rel = _safe_relative_path(relative_path)
    target = root.joinpath(*rel.parts)
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if resolved_root != resolved_target and resolved_root not in resolved_target.parents:
        raise DatasetRegistryError(f"{relative_path} escapes {root}")
    return target


def _dataset_id_path(root: Path, dataset_id: str) -> Path:
    if (
        not dataset_id
        or any(c not in "0123456789abcdef" for c in dataset_id)
        or len(dataset_id) != 64
    ):
        raise DatasetRegistryError(f"{dataset_id!r} is not a dataset id")
    return root / dataset_id


def _publish_directory(source: Path, target: Path) -> None:
    if target.exists():
        raise DatasetRegistryError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, target)


def _reject_tree_symlinks(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DatasetRegistryError(f"{path} is a symlink")


def _make_tree_writable(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        with suppress(FileNotFoundError):
            os.chmod(path, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)
    with suppress(FileNotFoundError):
        os.chmod(root, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)


def _lock_market(lock: Mapping[str, Any]) -> str:
    value = lock.get("market")
    if value is None and isinstance(lock.get("research"), Mapping):
        research = lock["research"]
        value = research.get("market")
        if value is None and isinstance(research.get("data"), Mapping):
            value = research["data"].get("market")
    return str(value or "spot")


def _is_perp_market(market: str) -> bool:
    return market in {"usdt_m_perpetual", "perpetual", "perp"}
