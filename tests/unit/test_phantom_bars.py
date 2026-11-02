"""Phantom zero-volume bar rows are dropped only with first-hand corroboration."""

from datetime import date, datetime, timezone

import polars as pl

from cnequity.config import Config
from cnequity.domain.schemas import DATASET_SCHEMAS
from cnequity.query import load
from cnequity.storage.repairs.phantom_bars import repair_phantom_bars
from cnequity.storage.revisions import RevisionStore

D_SUS1 = date(2025, 3, 17)
D_SUS2 = date(2025, 3, 18)
D_NORMAL = date(2025, 3, 18)


def _bar(symbol, day, close, volume=0, amount=None, source="sina"):
    return {
        "symbol": symbol,
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "volume": volume,
        "amount": amount,
        "source": source,
        "data_version": "v1",
        "fetched_at": datetime(2025, 3, 20, tzinfo=timezone.utc),
    }


def _status(symbol, day, status, source="baostock"):
    return {
        "symbol": symbol,
        "trade_date": day,
        "is_trading": status != "suspended",
        "status": status,
        "risk_warning": False,
        "source": source,
        "data_version": "v1",
        "fetched_at": datetime(2025, 3, 20, tzinfo=timezone.utc),
    }


def _lake(tmp_path, sus1_source="baostock", extra_status=()):
    cfg = Config(data_root=tmp_path / "lake")
    bars_root = cfg.curated_root / "daily_bars" / "trade_date=2025"
    bars_root.mkdir(parents=True)
    bars = [
        # phantom: volume=0, amount null, suspended first-hand, no baostock/tdx bar
        _bar("600095.SH", D_SUS1, 6.89),
        _bar("600095.SH", D_SUS2, 6.89),
        # kept: same shape but status says the stock traded that day
        _bar("600811.SH", D_NORMAL, 0.92),
        # untouched: a normal traded row
        _bar("600000.SH", date(2025, 3, 17), 10.0, volume=1000, amount=10000.0),
    ]
    pl.DataFrame(bars, schema=DATASET_SCHEMAS["daily_bars"]).write_parquet(
        bars_root / "part.parquet"
    )
    status_root = cfg.curated_root / "trading_status" / "trade_date=2025"
    status_root.mkdir(parents=True)
    status = [
        _status("600095.SH", D_SUS1, "suspended", source=sus1_source),
        _status("600095.SH", D_SUS2, "suspended"),
        _status("600811.SH", D_NORMAL, "normal"),
    ]
    status += list(extra_status)
    pl.DataFrame(status, schema=DATASET_SCHEMAS["trading_status"]).write_parquet(
        status_root / "part.parquet"
    )
    store = RevisionStore(cfg.meta_root, cfg.curated_root, cfg.derived_root)
    store.commit(
        "daily_bars",
        run_id="seed",
        changed_files=[bars_root / "part.parquet"],
        schema_version=1,
        contract_fingerprint="contract",
    )
    store.commit(
        "trading_status",
        run_id="seed",
        changed_files=[status_root / "part.parquet"],
        schema_version=1,
        contract_fingerprint="contract",
    )
    return cfg


def _no_baostock(symbols, lo, hi, config=None):
    return []


def _no_tdx(symbols, lo, hi, *, config=None, **kwargs):
    return pl.DataFrame()


def test_phantom_rows_dropped_with_first_hand_suspension(tmp_path):
    cfg = _lake(tmp_path)
    plan = repair_phantom_bars(
        cfg,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        fetch_baostock=_no_baostock,
        fetch_tdx=_no_tdx,
    )
    assert plan["applied"] is False
    assert plan["dropped"] == 2
    assert plan["kept"] == 1  # 600811: status says normal that day

    repair_phantom_bars(
        cfg,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        fetch_baostock=_no_baostock,
        fetch_tdx=_no_tdx,
        apply=True,
    )
    rows = load("daily_bars", config=cfg).sort("symbol", "trade_date")
    assert rows.filter(pl.col("symbol") == "600095.SH").height == 0
    assert rows.filter(pl.col("symbol") == "600811.SH").height == 1
    assert rows.filter(pl.col("symbol") == "600000.SH")["amount"].to_list() == [10000.0]
    # idempotent: a rerun finds nothing
    assert (
        repair_phantom_bars(
            cfg,
            start=date(2025, 1, 1),
            end=date(2025, 12, 31),
            fetch_baostock=_no_baostock,
            fetch_tdx=_no_tdx,
        )["dropped"]
        == 0
    )


def test_row_with_baostock_bar_is_kept(tmp_path):
    cfg = _lake(tmp_path)

    def _has_bar(symbols, lo, hi, config=None):
        return [
            {
                "symbol": "600095.SH",
                "trade_date": D_SUS1,
                "open": 6.89,
                "high": 6.89,
                "low": 6.89,
                "close": 6.89,
                "volume": 100,
                "amount": 689.0,
            }
        ]

    plan = repair_phantom_bars(
        cfg,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        fetch_baostock=_has_bar,
        fetch_tdx=_no_tdx,
    )
    assert plan["dropped"] == 1  # only D_SUS2; D_SUS1 is corroborated
    assert plan["kept"] == 2  # corroborated D_SUS1 + 600811 (status normal)


def test_derived_suspension_does_not_excuse_drop(tmp_path):
    # D_SUS1's only suspended row is a derived bar-gap inference -> not
    # first-hand -> the row is kept even though baostock/tdx are silent.
    cfg = _lake(tmp_path, sus1_source="derived_bar_gap")

    plan = repair_phantom_bars(
        cfg,
        start=date(2025, 1, 1),
        end=date(2025, 12, 31),
        fetch_baostock=_no_baostock,
        fetch_tdx=_no_tdx,
    )
    assert plan["dropped"] == 1
    assert plan["kept"] == 2
