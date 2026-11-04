#encoding:utf-8
"""TDX minute fallback must fill per-date holes, not only whole symbols.

2026-11-04 全窗 5m 回补暴露：QMT 对某码只要返回过任何数据（哪怕只有近期
窗），``_fetch_minute_bars_with_priority`` 就认为该码"已覆盖"，其老日期缺口
永远轮不到 TDX——139 码 scope 有 14 个 2025 调仓日因此留空，而 TDX 实测
（2025-02-05 探针返回完整 48 根 5m bar）完全可以补。修法=覆盖判定从
按码降到 (symbol, trade_date) 对，缺失日期按簇分组后 TDX 回填。
"""

from datetime import date

import polars as pl

import cnequity.steps.intraday as intraday
from cnequity.config import Config

D_OLD = date(2025, 2, 5)
D_NEW = date(2026, 10, 19)
EXPECTED = [D_OLD, D_NEW]


def _bars(symbol: str, *days: date, source_frame: bool = True) -> pl.DataFrame:
    rows = [
        {
            "symbol": symbol,
            "trade_date": day,
            "frequency": "5m",
            "bar_time": f"{day} 09:35:00",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 100,
            "amount": 100.0,
        }
        for day in days
    ]
    return pl.DataFrame(rows)


def _run(tmp_path, monkeypatch, *, qmt_frame, tdx_frames, expected_dates=EXPECTED):
    """Patch both adapters; return (frame, failed, source, tdx_calls)."""
    calls = []

    def fake_qmt(symbols, start, end, *, frequency, config):
        return qmt_frame

    def fake_tdx(symbols, start, end, **kwargs):
        calls.append((tuple(symbols), start, end))
        return tdx_frames.get((start, end), pl.DataFrame()), []

    monkeypatch.setattr(intraday, "fetch_minute_bars_qmt", fake_qmt)
    monkeypatch.setattr(intraday, "fetch_minute_bars", fake_tdx)
    cfg = Config(data_root=tmp_path)
    cfg.qmt_bridge_enabled = True
    kwargs = {} if expected_dates is None else {"expected_dates": expected_dates}
    frame, failed, source = intraday._fetch_minute_bars_with_priority(
        ["000001.SZ"],
        EXPECTED[0],
        EXPECTED[-1],
        dataset="minute_bars_5m",
        frequency="5m",
        rate_limit=None,
        backfill=False,
        config=cfg,
        max_pages=5,
        workers=1,
        **kwargs,
    )
    return frame, failed, source, calls


def test_partial_symbol_gets_tdx_date_fill(tmp_path, monkeypatch):
    """QMT 只给了新日期 → 老日期缺口必须由 TDX 补上。"""
    qmt = _bars("000001.SZ", D_NEW)
    tdx = {(D_OLD, D_OLD): _bars("000001.SZ", D_OLD)}
    frame, failed, source, calls = _run(
        tmp_path, monkeypatch, qmt_frame=qmt, tdx_frames=tdx
    )
    assert calls, "部分覆盖的码必须触发 TDX 日期级回填"
    assert (D_OLD, D_OLD) in calls[0][:1] or calls[0][1] == D_OLD
    have = set(
        zip(frame.get_column("symbol").to_list(), frame.get_column("trade_date").to_list())
    )
    assert ("000001.SZ", D_OLD) in have, "TDX 补的老日期行必须进结果"
    assert ("000001.SZ", D_NEW) in have, "QMT 已有行不动"
    assert source == "mixed"


def test_fully_covered_symbol_skips_tdx(tmp_path, monkeypatch):
    """QMT 覆盖全部期望日期 → 不打 TDX。"""
    qmt = _bars("000001.SZ", D_OLD, D_NEW)
    frame, failed, source, calls = _run(
        tmp_path, monkeypatch, qmt_frame=qmt, tdx_frames={}
    )
    assert calls == [], "全覆盖不得浪费 TDX 请求"
    assert source == "qmt_bridge"


def test_absent_symbol_keeps_whole_window_tdx(tmp_path, monkeypatch):
    """QMT 完全没有的码 → 维持原整窗 TDX 兜底。"""
    tdx = {(EXPECTED[0], EXPECTED[-1]): _bars("000001.SZ", D_OLD, D_NEW)}
    frame, failed, source, calls = _run(
        tmp_path, monkeypatch, qmt_frame=pl.DataFrame(), tdx_frames=tdx
    )
    assert len(calls) == 1
    assert calls[0][1] == EXPECTED[0] and calls[0][2] == EXPECTED[-1]
    assert source == "tdx_protocol"
    assert frame.height == 2


def test_without_expected_dates_old_behavior(tmp_path, monkeypatch):
    """未传 expected_dates（日历不可用）→ 保持旧按码行为，不猜日期。"""
    qmt = _bars("000001.SZ", D_NEW)
    frame, failed, source, calls = _run(
        tmp_path, monkeypatch, qmt_frame=qmt, tdx_frames={}, expected_dates=None
    )
    assert calls == [], "没有期望日期表时不得臆测缺口"
    assert source == "qmt_bridge"
