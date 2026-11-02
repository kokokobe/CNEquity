"""Drop Shanghai/Shenzhen phantom bar rows no first-hand source corroborates.

A phantom row repeats the previous close with ``volume=0`` and a null
``amount`` on a day the market did not trade. Sina emitted a handful of them
around suspension starts (2026-11-02: 600095.SH 2025-03-17/18, 603003.SH
2025-04-30, 688287.SH 2026-04-17 and 2026-06-09), and a null amount fails the
G0 engineering legs even though the day itself is real market fact.

A row is dropped only when every gate agrees it should never have existed:

- its ``(symbol, trade_date)`` has a first-hand ``suspended`` trading_status
  row (``derived_*`` sources are bar-gap inferences and never excuse a drop);
- Baostock serves no bar for that key;
- TDX serves no bar for that key.

Anything else keeps the row and is reported as unresolved.
"""

from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path

import polars as pl

from cnequity.config import Config
from cnequity.file_lock import lake_mutation_lock
from cnequity.query.parquet_scan import scan_parquet_root
from cnequity.storage.repairs.orphan_symbols import _publish
from cnequity.storage.revisions import RevisionStore

DATASET = "daily_bars"


def _default_baostock(names: list[str], lo: date, hi: date, config: Config) -> list[dict]:
    from cnequity.adapters.baostock.delisted_bars import fetch_delisted_bars

    rows, _failed = fetch_delisted_bars(names, lo, hi, config=config)
    return list(rows or [])


def _default_tdx(names: list[str], lo: date, hi: date, config: Config) -> pl.DataFrame:
    from cnequity.adapters.tdx_protocol.client import fetch_daily_bars

    return fetch_daily_bars(names, lo, hi, config=config)


def _first_hand_suspended_keys(status_base, symbols: list[str]) -> set[tuple[str, date]]:
    if status_base is None or not any(Path(status_base).rglob("*.parquet")):
        return set()
    frame = (
        scan_parquet_root(status_base, partition_col="trade_date", symbols=symbols)
        .filter(
            (pl.col("status") == "suspended")
            & ~pl.col("source").str.starts_with("derived_")
        )
        .select("symbol", "trade_date")
        .unique()
        .collect()
    )
    return set(
        zip(frame.get_column("symbol").to_list(), frame.get_column("trade_date").to_list())
    )


def repair_phantom_bars(
    config: Config,
    *,
    start: date,
    end: date,
    symbols: list[str] | None = None,
    apply: bool = False,
    fetch_baostock=None,
    fetch_tdx=None,
) -> dict:
    """Plan (default) or drop phantom zero-volume rows inside ``[start, end]``."""
    if fetch_baostock is None:
        fetch_baostock = _default_baostock
    if fetch_tdx is None:
        fetch_tdx = _default_tdx

    with lake_mutation_lock(config.meta_root) if apply else contextlib.nullcontext():
        store = RevisionStore(config.meta_root, config.curated_root, config.derived_root)
        base = store.repair_base(DATASET, register=apply)
        status_base = store.repair_base("trading_status", register=False)
        report: dict = {"applied": False, "rows": 0, "dropped": 0, "kept": 0}
        if base is None or not any(Path(base).rglob("*.parquet")):
            report["note"] = "no daily_bars data; nothing to repair"
            return report
        candidates = (
            scan_parquet_root(base, partition_col="trade_date")
            .filter(
                (pl.col("trade_date") >= pl.lit(start))
                & (pl.col("trade_date") <= pl.lit(end))
                & (pl.col("volume").fill_null(0) == 0)
                & pl.col("amount").is_null()
                & (
                    pl.col("symbol").str.ends_with(".SH")
                    | pl.col("symbol").str.ends_with(".SZ")
                )
                & (pl.col("symbol").is_in(symbols) if symbols else pl.lit(True))
            )
            .select("symbol", "trade_date", "close")
            .sort("symbol", "trade_date")
            .collect()
        )
        if candidates.is_empty():
            report["note"] = "no zero-volume null-amount rows in range"
            return report
        report["rows"] = candidates.height
        names = sorted(candidates.get_column("symbol").unique().to_list())
        lo = candidates.get_column("trade_date").min()
        hi = candidates.get_column("trade_date").max()

        suspended_keys = _first_hand_suspended_keys(status_base, names)
        baostock_keys = {
            (r["symbol"], r["trade_date"])
            for r in (fetch_baostock(names, lo, hi, config=config) or [])
        }
        tdx_frame = fetch_tdx(names, lo, hi, config=config)
        tdx_keys = set()
        if tdx_frame is not None and tdx_frame.height:
            tdx_keys = set(
                zip(
                    tdx_frame.get_column("symbol").to_list(),
                    tdx_frame.get_column("trade_date").to_list(),
                )
            )

        def _drops(symbol: str, trade_date: date) -> bool:
            key = (symbol, trade_date)
            return key in suspended_keys and key not in baostock_keys and key not in tdx_keys

        pairs = candidates.select("symbol", "trade_date").iter_rows()
        drop_keys = [(s, d) for s, d in pairs if _drops(s, d)]
        dropped = candidates.join(
            pl.DataFrame(
                list(drop_keys),
                schema={"symbol": pl.Utf8, "trade_date": pl.Date},
                orient="row",
            ),
            on=["symbol", "trade_date"],
            how="semi",
        )
        kept = candidates.height - dropped.height
        report.update(
            dropped=dropped.height,
            kept=kept,
            sample=[
                f"{s}|{d}"
                for s, d in dropped.select("symbol", "trade_date").head(20).iter_rows()
            ],
        )
        if not apply or dropped.is_empty():
            return report
        marked = dropped.select("symbol", "trade_date").with_columns(
            pl.lit(True).alias("_phantom")
        )

        def keep(frame: pl.DataFrame) -> pl.DataFrame:
            flagged = frame.join(marked, on=["symbol", "trade_date"], how="left")
            return flagged.filter(pl.col("_phantom").fill_null(False).not_()).drop("_phantom")

        report.update(
            _publish(
                config,
                store,
                DATASET,
                "trade_date",
                base,
                keep=keep,
                reason="phantom_bars",
                metadata={"rows": dropped.height},
            )
        )
        report["applied"] = True
        return report
