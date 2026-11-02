"""Write ``delisted`` status rows for recorded delisting days the daily step missed.

A stock whose ``instruments.delist_date`` is recorded stopped trading on that
date: the exchange delists it before the session, so the day has no bar and —
when the lake joined after the fact — no status row either. The daily step
writes ``derived_delisted`` for such days; a lake built later never ran it for
history. Without the row, a candidate as_of falling on the delist date fails
the G0 engineering legs with ``UNIVERSE_STATUS_MISSING``.

The recorded ``delist_date`` is the first-hand evidence. A delist-day bar
contradicts it, and this repair must not take sides: rows with a bar are
reported and kept as they are.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import polars as pl

from cnequity.config import Config
from cnequity.domain.contracts import contract_fingerprint, dataset_contract
from cnequity.domain.schemas import with_provenance
from cnequity.domain.trading_status import DELISTED_SOURCE, STATUS_DELISTED
from cnequity.file_lock import lake_mutation_lock
from cnequity.quality.publication import check_repair_publication
from cnequity.query.parquet_scan import scan_parquet_root
from cnequity.storage.parquet import StagingWriter, compact_dataset
from cnequity.storage.read_context import ReadContext
from cnequity.storage.revisions import RevisionStore


def repair_delisted_day_status(
    config: Config, *, start: date, end: date, apply: bool = False
) -> dict:
    """Preview without writes, or publish additive rows under the mutation lock."""
    if end < start:
        raise ValueError("end must not be before start")
    with lake_mutation_lock(config.meta_root) if apply else nullcontext():
        store = RevisionStore(
            config.meta_root, config.curated_root, config.derived_root, create=apply
        )
        if apply:
            for dataset in ("daily_bars", "instruments", "trading_status"):
                store.ensure_current(dataset)
        context = ReadContext.capture(config, ("daily_bars", "instruments", "trading_status"), {})
        inst_root = context.roots["instruments"]
        delisted = (
            scan_parquet_root(inst_root)
            .filter(
                pl.col("delist_date").is_not_null()
                & (pl.col("delist_date") >= pl.lit(start))
                & (pl.col("delist_date") <= pl.lit(end))
            )
            .select("symbol", "delist_date")
            .collect()
        )
        report: dict = {"applied": False, "rows": 0, "sample": []}
        if delisted.is_empty():
            report["note"] = "no recorded delist dates in range"
            return report

        bars_root = context.roots["daily_bars"]
        bar_keys = set()
        if any(Path(bars_root).rglob("*.parquet")):
            bars = (
                scan_parquet_root(bars_root, partition_col="trade_date")
                .select("symbol", "trade_date")
                .collect()
            )
            bar_keys = set(
                zip(bars.get_column("symbol").to_list(), bars.get_column("trade_date").to_list())
            )

        status_base = context.roots["trading_status"]
        status_keys = set()
        if any(Path(status_base).rglob("*.parquet")):
            status = (
                scan_parquet_root(status_base, partition_col="trade_date")
                .select("symbol", "trade_date")
                .collect()
            )
            status_keys = set(
                zip(
                    status.get_column("symbol").to_list(),
                    status.get_column("trade_date").to_list(),
                )
            )

        candidates = delisted.filter(
            ~pl.struct("symbol", "delist_date").map_elements(
                lambda k: (k["symbol"], k["delist_date"]) in bar_keys
                or (k["symbol"], k["delist_date"]) in status_keys,
                return_dtype=pl.Boolean,
            )
        )
        report.update(
            rows=candidates.height,
            sample=[
                {"symbol": s, "trade_date": d}
                for s, d in candidates.head(20).iter_rows()
            ],
        )
        if not apply or candidates.is_empty():
            return report
        rows = with_provenance(
            candidates.select(
                "symbol",
                pl.col("delist_date").alias("trade_date"),
                pl.lit(False).alias("is_trading"),
                pl.lit(STATUS_DELISTED).alias("status"),
                pl.lit(None, dtype=pl.Boolean).alias("risk_warning"),
            ),
            source=DELISTED_SOURCE,
            data_version="v1",
        )
        run_id = f"delisted-day-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        store.materialize_current("trading_status")
        changed: list[Path] = []
        with TemporaryDirectory(prefix="cne-delisted-day-") as directory:
            staging = Path(directory)
            StagingWriter(staging).write_batch("trading_status", run_id, "repair-0", rows)
            compact_dataset(
                staging,
                config.curated_root,
                "trading_status",
                run_id,
                changed_files=changed,
                base_root=context.roots["trading_status"],
            )
        publication = check_repair_publication(config, "trading_status", run_id, changed)
        contract = dataset_contract("trading_status")
        revision = store.commit(
            "trading_status",
            run_id=run_id,
            changed_files=changed,
            schema_version=int(contract["schema_version"]),
            contract_fingerprint=contract_fingerprint(contract),
            metadata={
                "reason": "delisted_day_status",
                "rows_added": candidates.height,
                "input_roots": {name: str(root) for name, root in context.roots.items()},
                "publication_audit": publication["report_path"],
            },
        )
        if revision is None:
            raise RuntimeError("delisted_day_status produced no revision")
        report.update(
            applied=True, revision=revision.revision, publication_audit=publication["report_path"]
        )
        return report
