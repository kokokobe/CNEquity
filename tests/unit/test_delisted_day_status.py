"""Delist-day status rows: a recorded delisting date is first-hand evidence."""

from datetime import date, datetime, timezone

import polars as pl

from cnequity.config import Config
from cnequity.domain.schemas import DATASET_SCHEMAS
from cnequity.query import load
from cnequity.storage.repairs.delisted_day_status import repair_delisted_day_status
from cnequity.storage.revisions import RevisionStore

D_DELIST = date(2026, 3, 27)
D_OTHER = date(2026, 3, 26)


def _inst(symbol, delist):
    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": "SS" if symbol.endswith(".SH") else "XSHE",
        "asset_type": "stock",
        "list_date": date(2010, 1, 1),
        "delist_date": delist,
        "prev_symbol": None,
        "source": "sina",
        "data_version": "v1",
        "fetched_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }


def _bar(symbol, day):
    return {
        "symbol": symbol,
        "trade_date": day,
        "open": 1.0,
        "high": 1.0,
        "low": 1.0,
        "close": 1.0,
        "pre_close": 1.0,
        "volume": 100,
        "amount": 100.0,
        "source": "sina",
        "data_version": "v1",
        "fetched_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }


def _status(symbol, day, status, source="baostock"):
    return {
        "symbol": symbol,
        "trade_date": day,
        "is_trading": status == "normal",
        "status": status,
        "risk_warning": None,
        "source": source,
        "data_version": "v1",
        "fetched_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
    }


def _lake(tmp_path):
    cfg = Config(data_root=tmp_path / "lake")
    inst_root = cfg.curated_root / "instruments"
    inst_root.mkdir(parents=True)
    pl.DataFrame(
        [_inst("002231.SZ", D_DELIST), _inst("600355.SH", D_DELIST), _inst("600000.SH", None)],
        schema=DATASET_SCHEMAS["instruments"],
    ).write_parquet(inst_root / "part.parquet")
    bars_root = cfg.curated_root / "daily_bars" / "trade_date=2026"
    bars_root.mkdir(parents=True)
    pl.DataFrame(
        [_bar("600000.SH", D_DELIST), _bar("002231.SZ", D_OTHER)],
        schema=DATASET_SCHEMAS["daily_bars"],
    ).write_parquet(bars_root / "part.parquet")
    status_root = cfg.curated_root / "trading_status" / "trade_date=2026"
    status_root.mkdir(parents=True)
    pl.DataFrame(
        [
            _status("002231.SZ", D_OTHER, "normal"),
            _status("600000.SH", D_DELIST, "normal"),
        ],
        schema=DATASET_SCHEMAS["trading_status"],
    ).write_parquet(status_root / "part.parquet")
    store = RevisionStore(cfg.meta_root, cfg.curated_root, cfg.derived_root)
    for ds, files in (
        ("instruments", [inst_root / "part.parquet"]),
        ("daily_bars", [bars_root / "part.parquet"]),
        ("trading_status", [status_root / "part.parquet"]),
    ):
        store.commit(
            ds,
            run_id="seed",
            changed_files=files,
            schema_version=1,
            contract_fingerprint="contract",
        )
    return cfg


def test_delist_day_rows_written_only_where_missing(tmp_path):
    cfg = _lake(tmp_path)
    plan = repair_delisted_day_status(
        cfg, start=date(2026, 1, 1), end=date(2026, 12, 31)
    )
    assert plan["applied"] is False
    # 002231 (delist day, no bar, no status) and 600355 (same) qualify;
    # 600000 has no delist date; 002231's delist day has no bar -> write.
    assert plan["rows"] == 2
    assert {d["symbol"] for d in plan["sample"]} == {"002231.SZ", "600355.SH"}

    repair_delisted_day_status(cfg, start=date(2026, 1, 1), end=date(2026, 12, 31), apply=True)
    rows = load("trading_status", config=cfg).sort("symbol", "trade_date")
    day = rows.filter(pl.col("trade_date") == D_DELIST)
    got = {r["symbol"]: r["status"] for r in day.to_dicts()}
    assert got == {"002231.SZ": "delisted", "600355.SH": "delisted", "600000.SH": "normal"}
    srcs = day.get_column("source").to_list()
    assert set(srcs) == {"baostock", "derived_delisted"}
    # idempotent
    assert (
        repair_delisted_day_status(cfg, start=date(2026, 1, 1), end=date(2026, 12, 31))["rows"]
        == 0
    )


def test_delist_day_with_existing_bar_is_skipped(tmp_path):
    cfg = _lake(tmp_path)
    # Give 002231 a traded bar ON its delist day: the recorded delist date then
    # contradicts the bar, and this repair must not take sides.
    bars_root = cfg.curated_root / "daily_bars" / "trade_date=2026"
    pl.DataFrame(
        [_bar("002231.SZ", D_DELIST)],
        schema=DATASET_SCHEMAS["daily_bars"],
    ).write_parquet(bars_root / "part2.parquet")
    RevisionStore(cfg.meta_root, cfg.curated_root, cfg.derived_root).commit(
        "daily_bars",
        run_id="seed2",
        changed_files=[bars_root / "part2.parquet"],
        schema_version=1,
        contract_fingerprint="contract",
    )
    plan = repair_delisted_day_status(cfg, start=date(2026, 1, 1), end=date(2026, 12, 31))
    assert {d["symbol"] for d in plan["sample"]} == {"600355.SH"}
