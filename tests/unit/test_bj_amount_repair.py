"""Filling the Beijing turnover Sina never published, from TDX.

Sina exposes no amount for the Beijing board at all: every one of the 505,518
Sina rows in the lake carried a null one. TDX serves the board and does publish
it, but counts volume in lots — so this supplements a stored row rather than
replacing it, and only when TDX is demonstrably describing the same session.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from cnequity.config import Config
from cnequity.steps import bars

DAY = date(2026, 9, 15)


def _stored(**over) -> pl.DataFrame:
    row = {
        "symbol": "920001.BJ",
        "trade_date": DAY,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 123_456.0,
        "amount": None,
        "source": "sina",
    }
    row.update(over)
    return pl.DataFrame([row])


def _tdx(monkeypatch, **over) -> None:
    row = {
        "symbol": "920001.BJ",
        "trade_date": DAY,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 123_400.0,
        "amount": 1_296_000.0,
    }
    row.update(over)
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars",
        lambda *a, **k: pl.DataFrame([row]),
    )


def _run(cfg, frame):
    return bars._supplement_bj_amounts_from_tdx(cfg, frame, start=DAY, end=DAY)


@pytest.fixture
def cfg(tmp_path):
    return Config(data_root=tmp_path / "lake")


def test_the_stored_volume_survives_the_supplement(cfg, monkeypatch):
    """TDX counts in lots and Sina has been exact since 2026. Taking TDX's row
    whole would coarsen 7,270 of them for a field we did not need."""
    _tdx(monkeypatch)
    updated, _ = _run(cfg, _stored())

    assert updated["amount"][0] == 1_296_000.0
    assert updated["volume"][0] == 123_456.0, "the finer figure stays"
    assert updated["source"][0] == "tdx_protocol"


def test_a_price_that_disagrees_leaves_the_row_alone(cfg, monkeypatch):
    """Open/high/low/close matched to the last digit across every row measured.
    One that does not is a different session, not a rounding difference."""
    _tdx(monkeypatch, close=10.6)
    updated, findings = _run(cfg, _stored())

    assert updated["amount"][0] is None
    assert updated["source"][0] == "sina"
    assert [f["check"] for f in findings if f["severity"] == "warning"] == [
        "daily_bars_tdx_amount_mismatch"
    ]


def test_a_volume_further_than_a_lot_is_not_rounding(cfg, monkeypatch):
    _tdx(monkeypatch, volume=123_456.0 - 100)
    updated, findings = _run(cfg, _stored())

    assert updated["amount"][0] is None
    assert any(f["check"] == "daily_bars_tdx_amount_mismatch" for f in findings)


def test_a_volume_inside_a_lot_is_rounding(cfg, monkeypatch):
    _tdx(monkeypatch, volume=123_456.0 - 99)
    updated, _ = _run(cfg, _stored())

    assert updated["amount"][0] == 1_296_000.0


def test_a_row_that_already_has_turnover_is_not_overwritten(cfg, monkeypatch):
    _tdx(monkeypatch)
    updated, _ = _run(cfg, _stored(amount=999.0, source="bse"))

    assert updated["amount"][0] == 999.0
    assert updated["source"][0] == "bse"


def test_a_key_tdx_never_served_is_counted_not_ignored(cfg, monkeypatch):
    """The board's retired 8xxxxx/430xxx codes are ~218,000 such rows. Counting
    them nowhere is how a repair reports success over rows it never touched."""
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars",
        lambda *a, **k: pl.DataFrame(
            [
                {
                    "symbol": "920002.BJ",
                    "trade_date": DAY,
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 1.0,
                    "volume": 1.0,
                    "amount": 1.0,
                }
            ]
        ),
    )
    updated, findings = _run(cfg, _stored())

    assert updated["amount"][0] is None
    unserved = [f for f in findings if f["check"] == "daily_bars_tdx_amount_unserved"]
    assert unserved and unserved[0]["rows_unserved"] == 1


def test_a_source_that_raises_leaves_every_row_as_it_was(cfg, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("TDX returned no bars")

    monkeypatch.setattr("cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _boom)
    updated, findings = _run(cfg, _stored())

    assert updated["amount"][0] is None
    assert [f["check"] for f in findings] == ["daily_bars_tdx_amount_unavailable"]


def test_a_sina_sh_row_uses_the_same_gate(cfg, monkeypatch):
    """沪深新浪历史行和北交所走同一条核对：价格一致才补成交额，成交量留原值。"""
    _tdx(monkeypatch, symbol="600005.SH")
    updated, _ = _run(cfg, _stored(symbol="600005.SH"))

    assert updated["amount"][0] == 1_296_000.0
    assert updated["volume"][0] == 123_456.0
    assert updated["source"][0] == "tdx_protocol"


def test_a_delisted_beijing_row_can_be_supplemented_from_qmt(cfg, monkeypatch):
    row = {
        "symbol": "920305.BJ",
        "trade_date": DAY,
        "open": 10.15,
        "high": 10.22,
        "low": 10.09,
        "close": 10.15,
        "volume": 1_400_706,
        "amount": None,
        "source": "sina",
    }
    monkeypatch.setattr(
        "cnequity.adapters.qmt_bridge.fetch_daily_bars_qmt",
        lambda *a, **k: pl.DataFrame(
            [
                {
                    **row,
                    "amount": 14_209_645.0,
                }
            ]
        ),
    )
    stored = pl.DataFrame([row])
    updated, findings = bars._supplement_bj_amounts_from_qmt(cfg, stored, start=DAY, end=DAY)

    assert updated["amount"][0] == 14_209_645.0
    assert updated["volume"][0] == 1_400_706
    assert updated["source"][0] == "qmt_bridge"
    supplement = findings[0]
    assert supplement["check"] == "daily_bars_qmt_amount_supplement"
    assert supplement["rows_supplemented"] == 1


def test_a_qmt_price_mismatch_leaves_beijing_row_alone(cfg, monkeypatch):
    row = {
        "symbol": "920305.BJ",
        "trade_date": DAY,
        "open": 10.15,
        "high": 10.22,
        "low": 10.09,
        "close": 10.15,
        "volume": 1_400_706,
        "amount": None,
        "source": "sina",
    }
    monkeypatch.setattr(
        "cnequity.adapters.qmt_bridge.fetch_daily_bars_qmt",
        lambda *a, **k: pl.DataFrame([{**row, "close": 10.16, "amount": 14_209_645.0}]),
    )
    stored = pl.DataFrame([row])
    updated, findings = bars._supplement_bj_amounts_from_qmt(cfg, stored, start=DAY, end=DAY)

    assert updated["amount"][0] is None
    assert updated["source"][0] == "sina"
    assert [f["check"] for f in findings if f["severity"] == "warning"] == [
        "daily_bars_qmt_amount_mismatch"
    ]


def test_sina_history_repair_asks_tdx_only_for_null_sina_rows(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from cnequity.query import load
    from cnequity.steps.finalize import step_compact
    from cnequity.storage.revisions import RevisionStore

    day = date(2015, 1, 5)
    fetched = datetime(2026, 7, 23, tzinfo=timezone.utc)
    lake = Config(data_root=tmp_path)
    path = lake.curated_root / f"daily_bars/trade_date={day}/part-merged.parquet"
    path.parent.mkdir(parents=True)

    def row(symbol, source, amount, volume=1000):
        return {
            "symbol": symbol,
            "trade_date": day,
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": 10.5,
            "volume": volume,
            "amount": amount,
            "source": source,
            "data_version": "v2",
            "fetched_at": fetched,
        }

    pl.DataFrame(
        [
            row("600005.SH", "sina", None),
            row("600270.SH", "ths", None),
            row("000001.SZ", "sina", 80_000.0),
            row("920001.BJ", "sina", None),
        ],
        schema_overrides={"amount": pl.Float64},
    ).write_parquet(path)
    RevisionStore(lake.meta_root, lake.curated_root).commit(
        "daily_bars",
        run_id="seed",
        changed_files=[path],
        schema_version=1,
        contract_fingerprint="contract",
    )
    asked: list[list[str]] = []

    def fetch(symbols, start, end, **_kwargs):
        asked.append(list(symbols))
        if "600005.SH" not in symbols:
            return pl.DataFrame()
        return pl.DataFrame(
            [
                {
                    "symbol": "600005.SH",
                    "trade_date": day,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.5,
                    "close": 10.5,
                    "volume": 1000,
                    "amount": 50_000.0,
                }
            ]
        )

    monkeypatch.setattr("cnequity.adapters.tdx_protocol.client.fetch_daily_bars", fetch)
    result = bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    # 沪深与京分批请求：京批探活不该拖累沪深，反之亦然。
    assert asked == [["600005.SH"], ["920001.BJ"]]
    assert result["rows_written"] == 1
    step_compact(lake, day, "repair", {})
    # The fixture's Beijing row predates the exchange; read the lake as stored.
    stored = {
        r["symbol"]: r
        for r in load("daily_bars", config=lake, include_otc=True).iter_rows(named=True)
    }
    assert stored["600005.SH"]["amount"] == 50_000.0
    assert stored["600005.SH"]["source"] == "tdx_protocol"
    assert stored["600005.SH"]["volume"] == 1000
    assert stored["600270.SH"]["source"] == "ths"
    assert stored["600270.SH"]["amount"] is None
    assert stored["000001.SZ"]["amount"] == 80_000.0
    assert stored["920001.BJ"]["amount"] is None
    assert stored["920001.BJ"]["source"] == "sina"


def _seed_lake(tmp_path, rows):
    from datetime import datetime, timezone

    from cnequity.config import Config
    from cnequity.storage.revisions import RevisionStore

    lake = Config(data_root=tmp_path)
    day = rows[0]["trade_date"]
    path = lake.curated_root / f"daily_bars/trade_date={day}/part-merged.parquet"
    path.parent.mkdir(parents=True)
    fetched = datetime(2026, 7, 23, tzinfo=timezone.utc)
    pl.DataFrame(
        [
            {
                **row,
                "data_version": "v2",
                "fetched_at": fetched,
            }
            for row in rows
        ],
        schema_overrides={"amount": pl.Float64},
    ).write_parquet(path)
    RevisionStore(lake.meta_root, lake.curated_root).commit(
        "daily_bars",
        run_id="seed",
        changed_files=[path],
        schema_version=1,
        contract_fingerprint="contract",
    )
    return lake


def _sina_row(symbol, day, amount=None, close=10.5, volume=1000.0):
    return {
        "symbol": symbol,
        "trade_date": day,
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": close,
        "volume": volume,
        "amount": amount,
        "source": "sina",
    }


def _baostock_rows(symbol, day, close=10.5, volume=1000.0, amount=52_500.0):
    return [
        {
            "symbol": symbol,
            "trade_date": day,
            "open": 10.0,
            "high": 11.0,
            "low": 9.5,
            "close": close,
            "volume": volume,
            "amount": amount,
        }
    ]


def _tdx_serves_nothing(*a, **k):
    raise RuntimeError("TDX returned no bars")


def _read_lake(lake, day):
    from cnequity.query import load
    from cnequity.steps.finalize import step_compact

    step_compact(lake, day, "repair", {})
    return {
        r["symbol"]: r
        for r in load("daily_bars", config=lake, include_otc=True).iter_rows(named=True)
    }


def test_a_delisted_sina_row_falls_back_to_baostock_when_tdx_serves_nothing(tmp_path, monkeypatch):
    """2026-10-31 的直接教训：103 只退市码 TDX 一行不补（退市码不在 TDX 目录，
    回答是权威空行），n1 因此 fail-closed。沪深退市码转 baostock 兜底，只补
    amount，存储的价量不动。"""
    from datetime import date

    from cnequity.steps import bars

    day = date(2015, 1, 5)
    lake = _seed_lake(tmp_path, [_sina_row("000004.SZ", day)])
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _tdx_serves_nothing
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars",
        lambda symbols, start, end, **k: (_baostock_rows("000004.SZ", day), []),
    )
    result = bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    stored = _read_lake(lake, day)
    assert stored["000004.SZ"]["amount"] == 52_500.0
    assert stored["000004.SZ"]["source"] == "baostock"
    assert stored["000004.SZ"]["volume"] == 1000.0
    assert stored["000004.SZ"]["close"] == 10.5
    assert result["rows_written"] == 1
    findings = result["context_updates"]["audit_findings"]
    fallback = [f for f in findings if f["check"] == "daily_bars_amounts_baostock_fallback"]
    assert fallback and fallback[0]["rows_supplemented"] == 1


def test_a_baostock_price_disagreement_leaves_the_row_alone(tmp_path, monkeypatch):
    from datetime import date

    from cnequity.steps import bars

    day = date(2015, 1, 5)
    lake = _seed_lake(tmp_path, [_sina_row("000004.SZ", day)])
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _tdx_serves_nothing
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars",
        lambda symbols, start, end, **k: (_baostock_rows("000004.SZ", day, close=10.9), []),
    )
    result = bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    stored = _read_lake(lake, day)
    assert stored["000004.SZ"]["amount"] is None
    assert stored["000004.SZ"]["source"] == "sina"
    findings = result["context_updates"]["audit_findings"]
    assert [f["check"] for f in findings if f["severity"] == "warning"] == [
        "daily_bars_amounts_baostock_mismatch"
    ]


def test_baostock_unserved_keeps_the_null(tmp_path, monkeypatch):
    from datetime import date

    from cnequity.steps import bars

    day = date(2015, 1, 5)
    lake = _seed_lake(tmp_path, [_sina_row("000004.SZ", day)])
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _tdx_serves_nothing
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars",
        lambda symbols, start, end, **k: ([], []),
    )
    result = bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    stored = _read_lake(lake, day)
    assert stored["000004.SZ"]["amount"] is None
    findings = result["context_updates"]["audit_findings"]
    unserved = [f for f in findings if f["check"] == "daily_bars_amounts_baostock_unserved"]
    assert unserved and unserved[0]["rows_unserved"] == 1


def test_the_tdx_unserved_finding_does_not_claim_rows_baostock_will_retry(
    tmp_path, monkeypatch
):
    """TDX 没服务、但已转 baostock 兜底的行，不得出现在 TDX 的 unserved 计数里
    ——否则 finding 报告的是一条审计上不成立的“这些行保持 null”。"""
    from datetime import date

    from cnequity.steps import bars

    day = date(2015, 1, 5)
    lake = _seed_lake(
        tmp_path, [_sina_row("000004.SZ", day), _sina_row("920001.BJ", day)]
    )
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _tdx_serves_nothing
    )
    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars",
        lambda symbols, start, end, **k: (_baostock_rows("000004.SZ", day), []),
    )
    result = bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    findings = result["context_updates"]["audit_findings"]
    # TDX 整批异常走 unavailable finding：已转 baostock 的退市码不得被计入。
    tdx_unavailable = [f for f in findings if f["check"] == "daily_bars_tdx_amount_unavailable"]
    assert tdx_unavailable and set(tdx_unavailable[0]["symbols"]) == {"920001.BJ"}
    assert not [f for f in findings if f["check"] == "daily_bars_tdx_amount_unserved"]
    assert result["rows_written"] == 1


def test_symbols_baostock_cannot_serve_are_not_asked(tmp_path, monkeypatch):
    """京码与基金不在 baostock 服务域，不得发起 baostock 请求，也不装作可修。"""
    from datetime import date

    from cnequity.steps import bars

    day = date(2015, 1, 5)
    lake = _seed_lake(
        tmp_path, [_sina_row("920001.BJ", day), _sina_row("163907.SZ", day)]
    )
    monkeypatch.setattr(
        "cnequity.adapters.tdx_protocol.client.fetch_daily_bars", _tdx_serves_nothing
    )
    asked: list[list[str]] = []

    def fetch_delisted(symbols, start, end, **k):
        asked.append(list(symbols))
        return ([], [])

    monkeypatch.setattr(
        "cnequity.adapters.baostock.delisted_bars.fetch_delisted_bars", fetch_delisted
    )
    bars.repair_sina_history_amounts_from_tdx(lake, day, day, "repair", None)

    assert asked == []


def test_the_window_is_walked_a_year_at_a_time():
    """Half a million rows is not one staging write."""
    assert bars._yearly_slices(date(2024, 3, 1), date(2026, 2, 1)) == [
        (date(2024, 3, 1), date(2024, 12, 31)),
        (date(2025, 1, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 2, 1)),
    ]
