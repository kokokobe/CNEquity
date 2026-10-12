"""Missing Beijing daily bars may be staged from the BigQMT bridge."""

from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from cnequity.config import Config
from cnequity.steps import bars

DAY = date(2026, 1, 5)


def _lake(tmp_path):
    return Config(data_root=tmp_path / "lake")


def _instrument(symbol: str, list_date: date, delist_date: date | None = None):
    return {
        "symbol": symbol,
        "name": symbol,
        "exchange": symbol.rsplit(".", 1)[-1],
        "asset_type": "stock",
        "list_date": list_date,
        "delist_date": delist_date,
        "prev_symbol": None,
        "source": "test",
        "data_version": "v1",
        "fetched_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def _bar(symbol: str, day: date):
    return {
        "symbol": symbol,
        "trade_date": day,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 100_000,
        "amount": 1_050_000.0,
        "source": "test",
        "data_version": "v1",
        "fetched_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def _seed(curated_root):
    instruments = curated_root / "instruments" / "part-merged.parquet"
    instruments.parent.mkdir(parents=True)
    pl.DataFrame(
        [
            _instrument("920001.BJ", date(2025, 1, 1)),
            _instrument("920002.BJ", date(2025, 1, 1)),
        ]
    ).write_parquet(instruments)

    daily = curated_root / "daily_bars" / f"trade_date={DAY.isoformat()}" / "part.parquet"
    daily.parent.mkdir(parents=True)
    pl.DataFrame([_bar("920001.BJ", DAY)]).write_parquet(daily)


def test_qmt_bj_bars_repair_stages_only_missing_keys(tmp_path, monkeypatch):
    lake = _lake(tmp_path)
    _seed(lake.curated_root)
    tomorrow = date(2026, 1, 6)
    monkeypatch.setattr(
        "cnequity.steps.bars.fetch_daily_bars_qmt",
        lambda symbols, start, end, **kwargs: pl.DataFrame(
            [
                _bar("920001.BJ", DAY),
                _bar("920001.BJ", tomorrow),
                _bar("920002.BJ", DAY),
            ]
        ),
    )

    result = bars.repair_bj_bars_from_qmt(
        lake,
        DAY,
        tomorrow,
        "repair-qmt-bars",
        symbols=["920001.BJ", "920002.BJ"],
    )

    assert result["rows_written"] == 2
    staged = list((lake.staging_root / "daily_bars").rglob("*.parquet"))
    assert len(staged) == 1
    rows = pl.read_parquet(staged[0]).sort("symbol", "trade_date")
    assert rows["symbol"].to_list() == ["920001.BJ", "920002.BJ"]
    assert rows["trade_date"].to_list() == [tomorrow, DAY]


def test_qmt_bj_bars_repair_respects_listing_span(tmp_path, monkeypatch):
    lake = _lake(tmp_path)
    _seed(lake.curated_root)
    monkeypatch.setattr(
        "cnequity.steps.bars.fetch_daily_bars_qmt",
        lambda symbols, start, end, **kwargs: pl.DataFrame(
            [
                _bar("920001.BJ", date(2024, 12, 31)),
                _bar("920002.BJ", date(2025, 1, 1)),
            ]
        ),
    )

    result = bars.repair_bj_bars_from_qmt(
        lake,
        date(2024, 12, 31),
        date(2025, 1, 1),
        "repair-qmt-bars",
        symbols=["920001.BJ", "920002.BJ"],
    )

    assert result["rows_written"] == 1
    assert result["usable_result"] is True
