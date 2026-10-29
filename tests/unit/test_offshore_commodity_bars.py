"""Unit tests for Sina offshore commodity bars (COMEX gold)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from cnequity.adapters.sina.global_futures import (
    OFFSHORE_CONTRACTS,
    fetch_offshore_commodity_bars_range,
)
from cnequity.domain.schemas import validate_dataframe, with_provenance


def test_offshore_contracts_unique():
    syms = [c[0] for c in OFFSHORE_CONTRACTS]
    assert len(syms) == len(set(syms))
    assert ("GC0.CMX", "GC", "COMEX黄金", "CMX") in OFFSHORE_CONTRACTS


def test_explicit_empty_offshore_contracts_are_a_noop():
    client = MagicMock()
    client.get.side_effect = AssertionError("empty contract selection must not fetch defaults")
    df = fetch_offshore_commodity_bars_range(
        date(2026, 7, 21), date(2026, 7, 21), contracts=(), client=client
    )
    assert df.is_empty()


def test_fetch_offshore_parses_sina_payload():
    payload = [
        {
            "date": "2026-07-20",
            "open": "4022.700",
            "high": "4046.000",
            "low": "3986.500",
            "close": "4011.800",
            "volume": "0",
            "position": "12",
            "settlement": "0",
        },
        {
            "date": "2026-07-21",
            "open": "4011.800",
            "high": "4088.400",
            "low": "4003.300",
            "close": "4077.700",
            "volume": "0",
            "position": "0",
            "settlement": "4077.700",
        },
        {
            "date": "2019-01-02",
            "open": "1",
            "high": "1",
            "low": "1",
            "close": "1",
            "volume": "0",
            "position": "0",
            "settlement": "0",
        },
    ]
    fake = MagicMock()
    fake.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value=payload),
    )
    with patch("cnequity.adapters.sina.global_futures.httpx.Client", return_value=fake):
        fake.__enter__ = MagicMock(return_value=fake)
        fake.__exit__ = MagicMock(return_value=None)
        # Client is constructed as context? Our code doesn't use context manager —
        # it constructs Client() and calls .get / .close
        client = MagicMock()
        client.get.return_value = MagicMock(
            raise_for_status=MagicMock(),
            json=MagicMock(return_value=payload),
        )
        df = fetch_offshore_commodity_bars_range(
            date(2026, 7, 20),
            date(2026, 7, 21),
            client=client,
        )
    assert df.height == 2
    assert set(df["symbol"].to_list()) == {"GC0.CMX"}
    assert df.filter(pl.col("trade_date") == date(2026, 7, 21))["close"][0] == 4077.7
    assert df.filter(pl.col("trade_date") == date(2026, 7, 21))["open_interest"][0] is None
    assert df["source"].unique().to_list() == ["sina"]
    validated = validate_dataframe(
        with_provenance(df, source="eastmoney", data_version="v1"),
        "commodity_bars",
    )
    assert validated["source"].unique().to_list() == ["sina"]


def test_fetch_offshore_skips_non_object_rows_and_keeps_valid_rows():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(
            return_value=[
                None,
                {
                    "date": "2026-07-21",
                    "open": "4011.8",
                    "high": "4088.4",
                    "low": "4003.3",
                    "close": "4077.7",
                    "volume": "10",
                },
            ]
        ),
    )
    df = fetch_offshore_commodity_bars_range(date(2026, 7, 21), date(2026, 7, 21), client=client)
    assert df.height == 1


def test_fetch_offshore_empty_on_bad_payload():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value={"error": True}),
    )
    df = fetch_offshore_commodity_bars_range(date(2026, 7, 21), date(2026, 7, 21), client=client)
    assert df.is_empty()


def test_fetch_offshore_malformed_payload_fails_strict_fetch():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(return_value={"error": True}),
    )
    with pytest.raises(RuntimeError, match="offshore commodity_bars failed for GC0.CMX"):
        fetch_offshore_commodity_bars_range(
            date(2026, 7, 21), date(2026, 7, 21), client=client, strict=True
        )


def test_missing_ohlcv_is_not_replaced_with_close_or_zero():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(
            return_value=[
                {
                    "date": "2026-07-21",
                    "open": None,
                    "high": "4088.400",
                    "low": "4003.300",
                    "close": "4077.700",
                    "volume": None,
                }
            ]
        ),
    )

    df = fetch_offshore_commodity_bars_range(date(2026, 7, 21), date(2026, 7, 21), client=client)

    assert df.is_empty()


def test_nonfinite_open_interest_is_dropped():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(
            return_value=[
                {
                    "date": "2026-07-21",
                    "open": "4011.8",
                    "high": "4088.4",
                    "low": "4003.3",
                    "close": "4077.7",
                    "volume": "10",
                    "position": "inf",
                }
            ]
        ),
    )
    df = fetch_offshore_commodity_bars_range(date(2026, 7, 21), date(2026, 7, 21), client=client)
    assert df.height == 1
    assert df["open_interest"][0] is None


def test_int64_overflow_volume_is_dropped():
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(),
        json=MagicMock(
            return_value=[
                {
                    "date": "2026-07-21",
                    "open": "4011.8",
                    "high": "4088.4",
                    "low": "4003.3",
                    "close": "4077.7",
                    "volume": "1e300",
                }
            ]
        ),
    )
    assert fetch_offshore_commodity_bars_range(
        date(2026, 7, 21), date(2026, 7, 21), client=client
    ).is_empty()


def test_strict_transport_failure_is_not_treated_as_empty_history():
    client = MagicMock()
    client.get.side_effect = RuntimeError("route down")

    with pytest.raises(RuntimeError, match="offshore commodity_bars failed"):
        fetch_offshore_commodity_bars_range(
            date(2026, 7, 21),
            date(2026, 7, 21),
            client=client,
            strict=True,
        )


def _bar(d: str, close: str) -> dict:
    return {"date": d, "open": close, "high": close, "low": close, "close": close, "volume": "0"}


def _client(payload: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get.return_value = MagicMock(
        raise_for_status=MagicMock(), json=MagicMock(return_value=payload)
    )
    return client


def test_unfinished_session_row_is_not_emitted():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    payload = [_bar("2026-10-22", "4039.8"), _bar("2026-10-23", "4036.8")]
    # 10:49 UTC on 10-23 is 06:49 New York: the 10-23 session is still trading.
    intraday = datetime(2026, 10, 23, 6, 49, tzinfo=ny)
    df = fetch_offshore_commodity_bars_range(
        date(2026, 10, 22), date(2026, 10, 23), client=_client(payload), now=intraday
    )
    assert df["trade_date"].to_list() == [date(2026, 10, 22)]

    closed = datetime(2026, 10, 23, 18, 0, tzinfo=ny)
    df = fetch_offshore_commodity_bars_range(
        date(2026, 10, 23), date(2026, 10, 23), client=_client(payload), now=closed
    )
    assert date(2026, 10, 23) in df["trade_date"].to_list()


def test_daily_step_rereads_recent_offshore_sessions(monkeypatch):
    from cnequity.steps import commodity

    calls = []
    writes = []

    def fake_range(start, end, *, config=None):
        calls.append((start, end))
        return pl.DataFrame({"trade_date": [date(2026, 10, 16)], "symbol": ["GC0.CMX"]})

    def fake_write(config, run_id, dataset, df, *, source, batch_id):
        writes.append((dataset, source, batch_id, df.height))
        return {"rows_read": df.height, "rows_written": df.height}

    monkeypatch.setattr(commodity, "fetch_offshore_commodity_bars_range", fake_range)
    monkeypatch.setattr(commodity, "write_fetched", fake_write)
    monkeypatch.setattr(
        commodity,
        "run_incremental_fetched",
        lambda *a, **k: {"rows_read": 15, "rows_written": 15},
    )
    config = MagicMock(sources={"eastmoney": True, "sina": True}, _backfill=False)

    result = commodity.step_commodity_bars(config, date(2026, 10, 19), "run-1", {})

    # The run-day validator only accepts 10-19, so the trailing window is a
    # separate batch that ends the day before.
    assert calls == [(date(2026, 10, 12), date(2026, 10, 18))]
    assert writes[0][:2] == ("commodity_bars", "sina")
    assert writes[0][2].startswith("offshore-recheck-")
    assert result == {"rows_read": 16, "rows_written": 16}


def test_offshore_recheck_failure_does_not_fail_daily_step(monkeypatch):
    from cnequity.steps import commodity

    def boom(*a, **k):
        raise RuntimeError("sina down")

    monkeypatch.setattr(commodity, "fetch_offshore_commodity_bars_range", boom)
    monkeypatch.setattr(
        commodity, "run_incremental_fetched", lambda *a, **k: {"rows_read": 15, "rows_written": 15}
    )
    config = MagicMock(sources={"eastmoney": True, "sina": True}, _backfill=False)
    result = commodity.step_commodity_bars(config, date(2026, 10, 19), "run-1", {})
    assert result == {"rows_read": 15, "rows_written": 15}
