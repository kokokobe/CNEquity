"""Halt and ST status read from each exchange's own whole-board publication.

`trading_status` had no independent daily source. Its feed is EastMoney's
current-state board, reached through a TDX-named facade that only forwards to
EastMoney, and the in-step fallback was this lake's own previous snapshot —
which is not a second opinion, it is the same opinion one day older. The backup
gate said so once the registry stopped mislabelling the primary.

The exchanges publish what is needed already, and the daily-bar route reads it:
a board file carries every listed security with its OHLC and its 证券简称.

* A halted security is still listed beside a non-zero reference close. Shanghai
  reports its open/high/low as ``0``; Shenzhen repeats the reference close in
  all four prices and reports zero volume. Either is the exchange stating it
  did not trade.
* ST and *ST are carried in the name, which is where `exchange/st_lists.py`
  already reads them from.

Measured against the EastMoney rows for 2026-09-15 over 5,219 comparable
symbols: ST agreed on **100.000%**, halts on 99.923%. The rule then read only
the zeroed open/high/low, so the four disagreements (``301139.SZ``,
``301390.SZ``, ``002731.SZ``, ``000016.SZ``) were Shenzhen halts it could not
see, not EastMoney errors. On 2026-10-13 and 10-15 every zero-volume Shenzhen
row (8 and 6) matched an independent Baostock halt, none had zeroed prices,
and no Baostock halt traded.

Two requests for the market, 2.6s measured. Shanghai and Shenzhen only: the
Beijing board is not here, and `covered` says so rather than letting a caller
read absence as "not listed".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import polars as pl

from cnequity.adapters.exchange.board_snapshot import (
    sse_fund_snapshot,
    sse_snapshot,
    szse_fund_report,
    szse_report,
)
from cnequity.adapters.exchange.daily_quotes import (
    SSE_SELECT,
    SSE_URL,
    _client,
    _keep_symbol,
)
from cnequity.adapters.exchange.st_lists import is_st_name
from cnequity.domain.symbols import format_symbol, is_etf_symbol
from cnequity.domain.trading_status import STATUS_NORMAL, STATUS_SUSPENDED

logger = logging.getLogger(__name__)

#: `name` is the addition over the daily-bar route; the rest is the same board.
SSE_STATUS_SELECT = SSE_SELECT
SSE_STATUS_URL = SSE_URL
_SZSE_STATUS_COLUMNS = ("证券代码", "证券简称", "开盘", "最高", "最低", "今收", "成交量(万股)")


@dataclass(frozen=True)
class ExchangeStatusResult:
    """Board status plus which exchanges actually answered.

    ``covered`` exists for the same reason it does on the quote route: one
    exchange answering is not the market answering, and a caller that ignores
    it will read a Shanghai-only reply as a verdict on Shenzhen too.
    """

    rows: pl.DataFrame
    covered: frozenset[str]
    failures: dict[str, str]

    @property
    def is_empty(self) -> bool:
        return self.rows.is_empty()


def _row(
    symbol: str,
    name: str,
    trade_date: date,
    ohl: tuple[float, float, float],
    close: float,
    volume: float | None = None,
):
    # A limit-locked session is open == high == low == close, which is why a
    # flat range alone is not a halt: it takes zeroed prices (Shanghai) or no
    # volume at all (Shenzhen, which repeats the reference close).
    untraded = max(ohl) <= 0.0 or (volume is not None and volume <= 0.0)
    halted = close > 0.0 and untraded
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "is_trading": not halted,
        "status": STATUS_SUSPENDED if halted else STATUS_NORMAL,
        "risk_warning": is_st_name(name),
    }


def _float(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _checked_sse_payload(payload: dict, trade_date: date) -> dict:
    raw_date = payload.get("date")
    try:
        snapshot_day = date(
            int(str(raw_date)[:4]), int(str(raw_date)[4:6]), int(str(raw_date)[6:8])
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"SSE status snapshot carried an unreadable date {raw_date!r}") from exc
    if snapshot_day != trade_date:
        # A current snapshot relabelled as another session would manufacture a
        # point-in-time fact the exchange never published.
        raise ValueError(f"SSE snapshot serves {snapshot_day}, not {trade_date}")
    snapshot_time = payload.get("time")
    if not isinstance(snapshot_time, int) or snapshot_time < 150000:
        raise ValueError(f"SSE snapshot is mid-session (time={snapshot_time})")
    return payload


def _fetch_sse(trade_date: date, *, config=None) -> list[dict]:
    payload = _checked_sse_payload(
        sse_snapshot(trade_date, config=config, client_factory=_client), trade_date
    )

    rows = []
    for item in payload.get("list") or []:
        # The trailing ``prev_close`` is not needed here; a snapshot cached
        # before it was selected still carries every status field.
        if not isinstance(item, (list, tuple)) or len(item) < len(SSE_STATUS_SELECT) - 1:
            continue
        code = str(item[0]).strip().zfill(6)
        if len(code) != 6 or not code.isdigit() or not _keep_symbol(code, "SH"):
            continue
        name = str(item[1] or "")
        o, h, low, close, volume = (_float(v) for v in item[2:7])
        rows.append(_row(format_symbol(code, "SH"), name, trade_date, (o, h, low), close, volume))
    return rows


def _fetch_szse(trade_date: date, *, config=None) -> list[dict]:
    frame = szse_report(trade_date, config=config, client_factory=_client)
    missing = [column for column in _SZSE_STATUS_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"SZSE report is missing {missing}")

    rows = []
    for record in frame[list(_SZSE_STATUS_COLUMNS)].to_dict("records"):
        code = str(record["证券代码"]).strip().zfill(6)
        if len(code) != 6 or not code.isdigit() or not _keep_symbol(code, "SZ"):
            continue
        # The export pads short names with spaces (``万  科Ａ``).
        name = str(record["证券简称"]).replace(" ", "").replace("　", "")
        ohl = (_float(record["开盘"]), _float(record["最高"]), _float(record["最低"]))
        rows.append(
            _row(
                format_symbol(code, "SZ"),
                name,
                trade_date,
                ohl,
                _float(record["今收"]),
                _float(record["成交量(万股)"]),
            )
        )
    return rows


def fetch_trading_status_exchange(
    symbols: list[str] | set[str] | None,
    trade_date: date,
    *,
    config=None,
) -> ExchangeStatusResult:
    """Halt and ST status for the SH/SZ boards, from the exchanges themselves.

    Either exchange failing leaves its own symbols uncovered rather than
    failing the call: a Shenzhen outage must not cost Shanghai its reading.
    """
    rows: list[dict] = []
    covered: set[str] = set()
    failures: dict[str, str] = {}
    for label, fetch in (("sse", _fetch_sse), ("szse", _fetch_szse)):
        try:
            rows.extend(fetch(trade_date, config=config))
            covered.add(label)
        except Exception as exc:  # noqa: BLE001 — a partial board is still evidence
            failures[label] = f"{type(exc).__name__}: {exc}"
            logger.warning("exchange trading status unavailable from %s: %s", label, exc)

    frame = pl.DataFrame(rows, schema_overrides={"risk_warning": pl.Boolean})
    if symbols is not None and not frame.is_empty():
        frame = frame.filter(pl.col("symbol").is_in(sorted(set(symbols))))
    if not frame.is_empty():
        frame = frame.unique(subset=["symbol", "trade_date"], keep="last")
    return ExchangeStatusResult(rows=frame, covered=frozenset(covered), failures=failures)


def _fund_row(symbol: str, trade_date: date) -> dict:
    """A fund the board shows trading is ``normal``; ETFs and LOFs carry no ST."""
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "is_trading": True,
        "status": STATUS_NORMAL,
        "risk_warning": False,
    }


def _fetch_sse_funds(trade_date: date, *, config=None) -> list[dict]:
    payload = _checked_sse_payload(
        sse_fund_snapshot(trade_date, config=config, client_factory=_client), trade_date
    )
    rows = []
    for item in payload.get("list") or []:
        if not isinstance(item, (list, tuple)) or len(item) < len(SSE_STATUS_SELECT) - 1:
            continue
        code = str(item[0]).strip().zfill(6)
        if not is_etf_symbol(code, "SH") or _float(item[6]) <= 0.0:
            continue
        rows.append(_fund_row(format_symbol(code, "SH"), trade_date))
    return rows


def _fetch_szse_funds(trade_date: date, *, config=None) -> list[dict]:
    frame = szse_fund_report(trade_date, config=config, client_factory=_client)
    rows = []
    for record in frame[["证券代码", "成交金额(元)"]].to_dict("records"):
        code = str(record["证券代码"]).strip().zfill(6)
        if not is_etf_symbol(code, "SZ") or _float(record["成交金额(元)"]) <= 0.0:
            continue
        rows.append(_fund_row(format_symbol(code, "SZ"), trade_date))
    return rows


def fetch_fund_trading_status_exchange(
    symbols: list[str] | set[str] | None,
    trade_date: date,
    *,
    config=None,
) -> ExchangeStatusResult:
    """ETF/LOF status for the SH/SZ fund boards, from the exchanges themselves.

    Only funds the board shows trading get a row. Unlike a stock, a fund with
    no turnover is not evidence of a halt: an illiquid fund simply does not
    print on some days, and the Shenzhen report carries no open/high/low to
    tell the two apart. That fund gets no row rather than a ``suspended`` one,
    the same rule that kept 4,863 trading names from being published as halted.

    Same failure contract as :func:`fetch_trading_status_exchange`: one exchange
    failing leaves the other its reading.
    """
    rows: list[dict] = []
    covered: set[str] = set()
    failures: dict[str, str] = {}
    for label, fetch in (("sse", _fetch_sse_funds), ("szse", _fetch_szse_funds)):
        try:
            rows.extend(fetch(trade_date, config=config))
            covered.add(label)
        except Exception as exc:  # noqa: BLE001 — a partial board is still evidence
            failures[label] = f"{type(exc).__name__}: {exc}"
            logger.warning("exchange fund status unavailable from %s: %s", label, exc)

    frame = pl.DataFrame(
        rows,
        schema={
            "symbol": pl.Utf8,
            "trade_date": pl.Date,
            "is_trading": pl.Boolean,
            "status": pl.Utf8,
            "risk_warning": pl.Boolean,
        },
    )
    if symbols is not None and not frame.is_empty():
        frame = frame.filter(pl.col("symbol").is_in(sorted(set(symbols))))
    if not frame.is_empty():
        frame = frame.unique(subset=["symbol", "trade_date"], keep="last")
    return ExchangeStatusResult(rows=frame, covered=frozenset(covered), failures=failures)
