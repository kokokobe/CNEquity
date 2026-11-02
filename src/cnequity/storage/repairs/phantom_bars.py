"""Drop Shanghai/Shenzhen phantom bar rows no first-hand source corroborates.

Two phantom shapes, both emitted by Sina around suspension starts:

- **zero-volume**: repeats the previous close with ``volume=0`` and a null
  ``amount`` on a day the market did not trade (2026-11-02: 600095.SH
  2025-03-17/18, 603003.SH 2025-04-30, 688287.SH 2026-04-17 and 2026-06-09).
  A null amount fails the G0 engineering legs even though the day itself is
  real market fact.
- **copy artifact**: repeats the previous session's whole bar — open, high,
  low, close and volume all identical — with ``volume > 0`` and a null
  ``amount`` on a day the symbol did not trade (2026-11-02: 163907.SZ and
  164902.SZ 2025-03-12). The volume>0 row also fools derived trading status
  into ``derived_traded``, so the first-hand-status gate below cannot see
  this class; the QMT bridge, which serves neither symbol that day, is the
  authority that the session never happened.

A row is dropped only when every gate of its class agrees it should never
have existed:

- zero-volume: its ``(symbol, trade_date)`` has a first-hand ``suspended``
  trading_status row (``derived_*`` sources are bar-gap inferences and never
  excuse a drop), and neither Baostock nor TDX serves a bar for that key;
- copy artifact: the stored row is byte-identical to the nearest prior
  stored session's row for the symbol (within the previous five stored
  sessions), and neither Baostock, TDX nor QMT serves a bar for that key.

Anything else keeps the row and is reported as unresolved.
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from cnequity.config import Config
from cnequity.file_lock import lake_mutation_lock
from cnequity.query.parquet_scan import scan_parquet_root
from cnequity.storage.repairs.orphan_symbols import _publish
from cnequity.storage.revisions import RevisionStore

DATASET = "daily_bars"
# A copy artifact repeats the immediately previous session; a source further
# back than five stored sessions is a coincidence, not a copy, and stays.
_COPY_SOURCE_SESSIONS = 5


def _default_baostock(names: list[str], lo: date, hi: date, config: Config) -> list[dict]:
    from cnequity.adapters.baostock.delisted_bars import fetch_delisted_bars

    rows, _failed = fetch_delisted_bars(names, lo, hi, config=config)
    return list(rows or [])


def _default_tdx(names: list[str], lo: date, hi: date, config: Config) -> pl.DataFrame:
    from cnequity.adapters.tdx_protocol.client import TdxSourceError, fetch_daily_bars

    try:
        return fetch_daily_bars(names, lo, hi, config=config)
    except TdxSourceError as exc:
        # "returned no bars" is the source answering, not an outage — the
        # gate needs exactly that absence as evidence. A connect/timeout
        # failure re-raises: a dead source must never certify absence.
        if "no bars" in str(exc):
            return pl.DataFrame()
        raise


def _default_qmt(names: list[str], lo: date, hi: date, config: Config) -> pl.DataFrame:
    from cnequity.adapters.qmt_bridge import fetch_daily_bars_qmt

    return fetch_daily_bars_qmt(names, lo, hi, config=config)


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
    fetch_qmt=None,
) -> dict:
    """Plan (default) or drop phantom rows inside ``[start, end]``."""
    if fetch_baostock is None:
        fetch_baostock = _default_baostock
    if fetch_tdx is None:
        fetch_tdx = _default_tdx
    if fetch_qmt is None:
        fetch_qmt = _default_qmt

    with lake_mutation_lock(config.meta_root) if apply else contextlib.nullcontext():
        store = RevisionStore(config.meta_root, config.curated_root, config.derived_root)
        base = store.repair_base(DATASET, register=apply)
        status_base = store.repair_base("trading_status", register=False)
        report: dict = {
            "applied": False,
            "rows": 0,
            "dropped": 0,
            "kept": 0,
            "dropped_copy": 0,
        }
        if base is None or not any(Path(base).rglob("*.parquet")):
            report["note"] = "no daily_bars data; nothing to repair"
            return report
        candidates = (
            scan_parquet_root(base, partition_col="trade_date")
            .filter(
                (pl.col("trade_date") >= pl.lit(start))
                & (pl.col("trade_date") <= pl.lit(end))
                & pl.col("amount").is_null()
                & (
                    pl.col("symbol").str.ends_with(".SH")
                    | pl.col("symbol").str.ends_with(".SZ")
                )
                & (pl.col("symbol").is_in(symbols) if symbols else pl.lit(True))
                & (
                    # zero-volume 伪影(原类)
                    (pl.col("volume").fill_null(0) == 0)
                    # 复制伪影(新类):sina 整行复制前会话,volume>0
                    | (
                        (pl.col("volume") > 0)
                        & (pl.col("source") == "sina")
                    )
                )
            )
            .select(
                "symbol",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source",
            )
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
        qmt_frame = fetch_qmt(names, lo, hi, config=config)
        qmt_keys = set()
        if qmt_frame is not None and qmt_frame.height:
            qmt_keys = set(
                zip(
                    qmt_frame.get_column("symbol").to_list(),
                    qmt_frame.get_column("trade_date").to_list(),
                )
            )

        # 复制伪影的证据:同 symbol 在候选日之前最近一个存储会话的行,
        # 五个存储会话之内,open/high/low/close/volume 逐位相同。
        copy_candidates = candidates.filter(pl.col("volume") > 0)
        prior_base = store.repair_base(DATASET, register=False)
        copy_evidence: dict[tuple[str, date], bool] = {}
        if not copy_candidates.is_empty():
            window_lo = min(lo - timedelta(days=30), lo)
            prior = (
                scan_parquet_root(prior_base, partition_col="trade_date")
                .filter(
                    (pl.col("trade_date") < pl.lit(hi))
                    & (pl.col("trade_date") >= pl.lit(window_lo))
                    & (pl.col("symbol").is_in(names))
                )
                .select("symbol", "trade_date", "open", "high", "low", "close", "volume")
                .sort("symbol", "trade_date")
                .collect()
            )
            prior_dates = (
                prior.group_by("symbol")
                .agg(pl.col("trade_date").unique().sort().alias("dates"))
            )
            prior_map = {
                (r["symbol"], r["trade_date"]): r
                for r in prior.iter_rows(named=True)
            }
            date_lists = {
                r["symbol"]: r["dates"] for r in prior_dates.iter_rows(named=True)
            }
            for row in copy_candidates.iter_rows(named=True):
                key = (row["symbol"], row["trade_date"])
                dates = [
                    d
                    for d in date_lists.get(row["symbol"], [])
                    if d < row["trade_date"]
                ]
                recent = dates[-_COPY_SOURCE_SESSIONS:]
                match = False
                for d in reversed(recent):
                    src = prior_map.get((row["symbol"], d))
                    if src is None:
                        continue
                    if (
                        src["open"] == row["open"]
                        and src["high"] == row["high"]
                        and src["low"] == row["low"]
                        and src["close"] == row["close"]
                        and src["volume"] == row["volume"]
                    ):
                        match = True
                        break
                    break  # 只看最近一个存储会话:伪影复制的是"前一日"
                copy_evidence[key] = match

        def _drops(symbol: str, trade_date: date) -> bool:
            key = (symbol, trade_date)
            if key in qmt_keys:
                return False  # QMT 有行 = 当日真实交易,任何类都不删
            if copy_evidence.get(key) is True:
                return True  # 复制伪影类:三源全无 + 逐位复制
            return (
                key in suspended_keys
                and key not in baostock_keys
                and key not in tdx_keys
            )

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
        dropped_copy = dropped.filter(pl.col("volume") > 0).height
        kept = candidates.height - dropped.height
        report.update(
            dropped=dropped.height,
            dropped_copy=dropped_copy,
            kept=kept,
            sample=[
                f"{s}|{d}"
                for s, d in dropped.select("symbol", "trade_date").head(20).iter_rows()
            ],
        )
        if not apply or dropped.is_empty():
            return report
        # 删行会把这个键变成缺口;不登记负证据,下一次 backfill 会从 sina
        # 把同一伪影行原样抓回来复活。claim 带上删除证据的窗口。
        from cnequity.steps.common import record_negative_evidence

        record_negative_evidence(
            config,
            DATASET,
            sorted(dropped.get_column("symbol").unique().to_list()),
            dropped.get_column("trade_date").min(),
            dropped.get_column("trade_date").max(),
            reason="phantom_bar_removed",
            source="qmt_bridge",
            details={"rows": dropped.height},
        )
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
