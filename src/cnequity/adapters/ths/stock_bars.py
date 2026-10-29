"""同花顺 security daily bars — history older than the primary vendor carries.

TDX serves the lake from 2016; 同花顺 keeps per-year files back to each stock's
or ETF/LOF's listing (贵州茅台 to 2001, 平安银行 to 1991). This module exists
to deepen the research window, not to replace the daily source.

Two rules make the deeper history safe to mix with what is already stored:

**Unadjusted only.** The URL takes ``00`` (raw), never ``01``/``02``. 同花顺 also
publishes qfq and hfq, but its adjustment algorithm differs from Sina's — the two
disagree by up to ~340bps on ex-dividend days, silently, because they treat the
dividend differently. Splicing its hfq onto the existing series would plant a
break at every dividend. Raw prices, by contrast, agree field-for-field (verified
across the 2020 overlap: 243 sessions, zero OHLC mismatches), so the lake keeps
deriving hfq from the Sina factors it already uses. Those factors reach back to
listing too, which is what makes one continuous convention possible.

**Fetched per year.** History is served as one file per calendar year, so a
symbol costs one request per year of history rather than one per day.

``volume`` is already in 股, the lake's unit, so no conversion happens here —
verified twice: ``amount / close / volume`` has a median of 0.999 over the
5,303,037 curated 同花顺 rows, and a live 2015 file for 600519 gives
1,875,063,100 / 202.52 / 9,451,517 = 0.98. Do not "align" this with the TDX
path, which reports 手 and converts. See :mod:`cnequity.domain.units`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from cnequity.adapters.numeric import finite_int64
from cnequity.adapters.ths.boards import ThsNotFoundError, _finite_float, _get, _unwrap_jsonp
from cnequity.adapters.ths.year_cache import cached_year_rows

if TYPE_CHECKING:
    from cnequity.config import Config

logger = logging.getLogger(__name__)

# 00 = unadjusted; see the module docstring for why never 01/02.
_STOCK_KLINE_URL = "https://d.10jqka.com.cn/v6/line/hs_{code}/00/{part}.js"


def _parse_stock_kline(payload: dict[str, Any], symbol: str) -> list[dict]:
    rows: list[dict] = []
    raw = str(payload.get("data") or "")
    if not raw:
        return rows
    for line in raw.split(";"):
        parts = line.split(",")
        if len(parts) < 7 or len(parts[0]) != 8:
            continue
        stamp = parts[0]
        try:
            volume = finite_int64(_finite_float(parts[5]), minimum=0)
            amount = _finite_float(parts[6])
            # Pre-2004 year files carry 0 for turnover they do not have: a
            # traded bar with zero turnover is unknown, not free.
            if volume and amount is not None and amount <= 0:
                amount = None
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8])),
                    "open": _finite_float(parts[1]),
                    "high": _finite_float(parts[2]),
                    "low": _finite_float(parts[3]),
                    "close": _finite_float(parts[4]),
                    "volume": volume,
                    "amount": amount,
                }
            )
        except ValueError:
            continue
    return rows


def fetch_stock_bars(
    symbol: str,
    start: date,
    end: date,
    *,
    config: Config | None = None,
) -> list[dict]:
    """Unadjusted daily bars for one symbol over ``[start, end]``.

    A year file missing is normal — the symbol had not listed yet — and is
    skipped; a transport error is not, and propagates.
    """
    code = symbol[:6]
    rows: list[dict] = []
    seen: set[date] = set()
    for year in range(start.year, end.year + 1):
        url = _STOCK_KLINE_URL.format(code=code, part=year)
        try:
            year_rows = cached_year_rows(
                url,
                year,
                config=config,
                cache_name="stock_year",
                cache_filename=f"stock-{code}-{year}.json",
                fetch=lambda url=url: _get(url, config=config),
                parse=lambda text: _parse_stock_kline(_unwrap_jsonp(text), symbol),
            )
        except ThsNotFoundError as exc:
            logger.debug("THS %s %s unavailable: %s", symbol, year, exc)
            continue
        for row in year_rows:
            if row["trade_date"] in seen or not (start <= row["trade_date"] <= end):
                continue
            seen.add(row["trade_date"])
            rows.append(row)
    rows.sort(key=lambda r: r["trade_date"])
    return rows
