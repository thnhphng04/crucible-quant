"""Studio's adapter from drafts to the existing campaign and dataset rules."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from quantcrucible.agent.compare import protocol_hash
from quantcrucible.config.loader import load_user_config, parse_user_config, research_to_dict
from quantcrucible.config.lock import (
    CampaignNotOpened,
    assert_lock_matches,
    read_lock,
    resolve_campaign,
    sha256_file,
)
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.strategy.tunable import GRAMMAR_VERSION, STOP_PERIOD_TUNABLE
from quantcrucible.data.registry import DatasetRegistry, read_dataset_manifest, resolve_dataset
from quantcrucible.ledger.db import Ledger
from quantcrucible.studio.store import DraftStore, StudioConflict
from quantcrucible.validation.campaign_service import (
    CampaignService,
    draft_digest,
    preview_campaign,
)
from quantcrucible.validation.campaign_service import DatasetDescriptor as CampaignDataset
from quantcrucible.validation.clock import DAILY_V1
from quantcrucible.validation.numerics import numerics_tag
from quantcrucible.validation.portfolio import SHARED_ACCOUNT
from quantcrucible.validation.run import _check_trial_budget, new_campaign_derived


@contextmanager
def _read_ledger(root: Path) -> Iterator[Ledger]:
    path = root / "ledger" / "crucible.db"
    if path.exists():
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        ledger = Ledger(conn, str(path))
    else:
        ledger = Ledger.open(":memory:")
    try:
        yield ledger
    finally:
        ledger.close()


def _plain_config(cfg: UserConfig) -> dict[str, Any]:
    return {"operational": asdict(cfg.operational), "research": research_to_dict(cfg.research)}


def _dataset_for(root: Path, cfg: UserConfig, dataset_id: str) -> CampaignDataset:
    registry = DatasetRegistry(root)
    descriptor = registry.get(dataset_id)
    manifest = read_dataset_manifest(descriptor.manifest_path)
    data = cfg.research.data
    spec = manifest.spec
    expected = {
        "market": data.market,
        "primary_exchange": data.exchange,
        "second_exchange": data.second_exchange,
        "symbols": tuple(sorted(data.symbols)),
        "timeframe": data.timeframe,
        "start_utc": f"{data.start.isoformat()}T00:00:00+00:00",
        "holdout_months": data.holdout_months,
        "funding_interval_hours": data.funding_interval_hours,
    }
    for key, value in expected.items():
        if getattr(spec, key) != value:
            raise StudioConflict(f"dataset differs from research.data.{key}; prepare data again")
    resolved_end = date.fromisoformat(spec.resolved_end_utc[:10])
    if data.end is not None and data.end != resolved_end:
        raise StudioConflict("dataset end differs from research.data.end; prepare data again")
    return CampaignDataset(
        dataset_id=dataset_id,
        manifest_sha256=descriptor.manifest_sha256,
        holdout_range=descriptor.holdout_range,
        holdout_lock_sha256=descriptor.holdout_lock_sha256,
        resolved_data_end=resolved_end.isoformat(),
    )


class StudioService:
    def __init__(self, root: Path, drafts: DraftStore) -> None:
        self.root = root
        self.drafts = drafts

    def _draft_config(self, draft_id: str, revision: int) -> tuple[dict[str, Any], UserConfig]:
        draft = self.drafts.get(draft_id)
        if draft["revision"] != revision:
            raise StudioConflict("draft changed; reload it")
        return draft, parse_user_config(draft["config"])

    def _derived(self, cfg: UserConfig) -> dict[str, Any]:
        return new_campaign_derived(cfg)

    def assert_runnable(self, campaign_id: str, *, purpose: str | None = None) -> dict[str, Any]:
        active_path = self.root / "config" / "evaluation.lock.yaml"
        config_path = self.root / "config" / "user.yaml"
        with _read_ledger(self.root) as ledger:
            resolved = resolve_campaign(campaign_id, ledger, active_path)
            if resolved.campaign.status != "OPEN":
                raise StudioConflict(f"campaign {campaign_id} is not OPEN")
            if not active_path.exists() or read_lock(active_path)["campaign_id"] != campaign_id:
                raise StudioConflict(f"campaign {campaign_id} is not the active campaign")
            cfg = load_user_config(config_path)
            assert_lock_matches(cfg, resolved.path, ledger, campaign_id)
            lock = resolved.data
            if purpose is not None and cfg.research.campaign.purpose != purpose:
                raise StudioConflict(f"campaign {campaign_id} is not a {purpose} campaign")
            locator = resolve_dataset(self.root, lock)
            if locator.kind == "v1":
                DatasetRegistry(self.root).verify_dataset(
                    str(locator.dataset_id), verify_holdout_files=False
                )
            return lock

    def search_complete(
        self, campaign_id: str, lock: dict[str, Any], engines: set[str] | None = None
    ) -> bool:
        from quantcrucible.agent.evolution.archive import scope_args
        from quantcrucible.agent.scheduler import campaign_scopes, quotas

        with _read_ledger(self.root) as ledger:
            _purpose, budget = ledger.campaign_purpose(campaign_id)
            if budget is None:
                return False
            research = lock["research"]
            limits = quotas(
                budget,
                {name: float(share) for name, share in research["engines"].items()},
                int(research["seeds"]),
                campaign_scopes(lock),
            )
            selected = [
                (k, quota) for k, quota in limits.items() if engines is None or k.engine in engines
            ]
            return bool(selected) and all(
                len(ledger.trials(campaign_id, *scope_args(k))) >= quota for k, quota in selected
            )

    def _binding(
        self, draft: dict[str, Any], cfg: UserConfig, dataset: CampaignDataset, ledger: Ledger
    ) -> dict[str, Any]:
        active = self.root / "config" / "evaluation.lock.yaml"
        return {
            "draft_id": draft["draft_id"],
            "revision": draft["revision"],
            "config_sha256": hashlib.sha256(
                json.dumps(_plain_config(cfg), sort_keys=True).encode()
            ).hexdigest(),
            "dataset_id": dataset.dataset_id,
            "manifest_sha256": dataset.manifest_sha256,
            "active_lock_sha256": sha256_file(active) if active.exists() else None,
            "ledger_n_eff": ledger.trial_stats().n_eff,
            "campaign_count": len(ledger.campaigns()),
            "protocol_sha256": (
                protocol_hash() if cfg.research.campaign.purpose == "harness_test" else None
            ),
            "exit_protocol": "bracket_timeout_v1",
            "backtest_numerics": numerics_tag(cfg.research.backtest.precision),
            "portfolio_protocol": SHARED_ACCOUNT,
            "stop_period": STOP_PERIOD_TUNABLE,
            "grammar_version": GRAMMAR_VERSION,
            "evaluation_clock": DAILY_V1,
        }

    def preview(self, draft_id: str, revision: int) -> dict[str, Any]:
        draft, cfg = self._draft_config(draft_id, revision)
        problems: list[dict[str, str]] = []
        if draft["dataset_id"] is None:
            problems.append(
                {
                    "path": "research.data",
                    "code": "DATA_MISSING",
                    "message": "Chuẩn bị dữ liệu trước",
                }
            )
            return {"ok": False, "problems": problems}
        dataset = _dataset_for(self.root, cfg, draft["dataset_id"])
        with _read_ledger(self.root) as ledger:
            result = preview_campaign(self.root, cfg, ledger, dataset, derived=self._derived(cfg))
            for issue in result.problems:
                problems.append({"path": "campaign", "code": "ADMISSION", "message": issue})
            if cfg.research.holdout_pass is None:
                problems.append(
                    {"path": "research.holdout_pass", "code": "REQUIRED", "message": "Cần D4"}
                )
            if cfg.research.campaign.trial_budget is None:
                problems.append(
                    {
                        "path": "research.campaign.trial_budget",
                        "code": "REQUIRED",
                        "message": "Studio campaign cần ngân sách trial trước khi tạo",
                    }
                )
            try:
                _check_trial_budget(cfg, ledger, dataset.holdout_range)
            except CampaignNotOpened as exc:
                problems.append(
                    {
                        "path": "research.campaign.trial_budget",
                        "code": "MINBTL",
                        "message": str(exc),
                    }
                )
            binding = self._binding(draft, cfg, dataset, ledger)
        response: dict[str, Any] = {
            "ok": not problems,
            "problems": problems,
            "dataset": {
                "id": dataset.dataset_id,
                "hash": dataset.manifest_sha256,
                "holdout_range": dataset.holdout_range,
                "coverage": read_dataset_manifest(
                    self.root / "data" / "datasets" / dataset.dataset_id / "manifest.json"
                ).coverage,
            },
            "lock_summary": {
                "exit": research_to_dict(cfg.research)["exit"],
                "precision": cfg.research.backtest.precision,
                "timeframe": cfg.research.data.timeframe,
                "purpose": cfg.research.campaign.purpose,
                "trial_budget": cfg.research.campaign.trial_budget,
            },
        }
        if not problems:
            token, expires = self.drafts.issue_preview(draft_id, revision, binding)
            response.update(preview_token=token, expires_at=expires)
        return response

    def create(
        self, draft_id: str, revision: int, preview_token: str, request_key: str
    ) -> dict[str, str]:
        draft, cfg = self._draft_config(draft_id, revision)
        if cfg.research.campaign.trial_budget is None:
            raise StudioConflict("research.campaign.trial_budget is required")
        if draft["dataset_id"] is None:
            raise StudioConflict("prepare data before creating a campaign")
        dataset = _dataset_for(self.root, cfg, draft["dataset_id"])
        ledger_path = self.root / "ledger" / "crucible.db"
        if ledger_path.exists():
            with _read_ledger(self.root) as ledger:
                previous = ledger.creation_request(request_key)
            if previous is not None:
                if previous[0] != draft_digest(draft_id, cfg, dataset):
                    raise StudioConflict("request_key already belongs to another draft")
                with Ledger.open(ledger_path) as ledger:
                    recovered = CampaignService(self.root, ledger).create_campaign(
                        cfg,
                        dataset,
                        draft_id=draft_id,
                        request_key=request_key,
                        derived=self._derived(cfg),
                        user_yaml=yaml.safe_dump(
                            _plain_config(cfg), sort_keys=False, allow_unicode=True
                        ),
                    )
                self.drafts.mark_created(draft_id, recovered.campaign.campaign_id)
                return {
                    "campaign_id": recovered.campaign.campaign_id,
                    "lock_sha256": recovered.lock_sha256,
                    "dataset_id": recovered.dataset_id,
                }
        with _read_ledger(self.root) as ledger:
            binding = self._binding(draft, cfg, dataset, ledger)
        self.drafts.assert_preview(preview_token, draft_id, revision, binding)
        (self.root / "ledger").mkdir(parents=True, exist_ok=True)
        with Ledger.open(self.root / "ledger" / "crucible.db") as ledger:
            service = CampaignService(self.root, ledger)
            created = service.create_campaign(
                cfg,
                dataset,
                draft_id=draft_id,
                request_key=request_key,
                derived=self._derived(cfg),
                user_yaml=yaml.safe_dump(_plain_config(cfg), sort_keys=False, allow_unicode=True),
            )
        self.drafts.mark_created(draft_id, created.campaign.campaign_id)
        return {
            "campaign_id": created.campaign.campaign_id,
            "lock_sha256": created.lock_sha256,
            "dataset_id": created.dataset_id,
        }
