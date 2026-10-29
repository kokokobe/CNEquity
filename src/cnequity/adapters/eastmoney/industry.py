"""EastMoney industry classification membership.

The board report lists each stock under its level 1, 2 and 3 industry boards.
One row per stock is kept: ``industry_code``/``industry_name`` is the level-3
leaf and ``l1_*``..``l3_*`` carry the whole chain (see ``domain.industry_levels``).
"""

from __future__ import annotations

from datetime import date

import polars as pl

from cnequity.adapters.eastmoney.common import exchange_from_datacenter, symbol_from_em
from cnequity.adapters.eastmoney.datacenter import fetch_datacenter
from cnequity.adapters.eastmoney.em_auth import EastMoneyClient
from cnequity.domain.industry_levels import em_member_levels

_BOARD_REPORT = "RPT_BOARD_CONSTITUENT"
_BOARD_COLUMNS = "SECURITY_CODE,BOARD_CODE,BOARD_NAME,BOARD_TYPE_NEW"
_INDUSTRY_BOARD_TYPE = "2"


def fetch_industry_members(
    as_of_date: date,
    *,
    client: EastMoneyClient | None = None,
    config=None,
) -> pl.DataFrame:
    owns = client is None
    if client is None:
        client = EastMoneyClient(config=config)

    try:
        raw = fetch_datacenter(
            client,
            _BOARD_REPORT,
            _BOARD_COLUMNS,
            filter_expr=f'(BOARD_TYPE_NEW="{_INDUSTRY_BOARD_TYPE}")',
            # Same report as sector_members, same measured 5000-row page. The
            # industry slice is only ~17k rows today, but at the 500 clamp that is
            # 34 pages and a third of the way to the pageNumber cap that broke
            # sector_members; 5000 keeps it at 4.
            page_size=5000,
            trust_page_size=True,
        )
        rows: list[dict] = []
        for item in raw:
            code = str(item.get("SECURITY_CODE", "")).zfill(6)
            exch = exchange_from_datacenter(item)
            sym = symbol_from_em(code, 1 if exch == "SH" else (2 if exch == "BJ" else 0))
            if not sym:
                continue
            industry_code = str(item.get("BOARD_CODE") or "").strip()
            industry_name = str(item.get("BOARD_NAME") or "").strip()
            if not industry_code or not industry_name:
                continue
            rows.append({"symbol": sym, "board_code": industry_code, "board_name": industry_name})
    finally:
        if owns:
            client.close()
    if not rows:
        return pl.DataFrame()
    # Keeping one board per stock used to keep whichever level the page order
    # put last, so a stock's industry changed level from one day to the next.
    members = em_member_levels(pl.DataFrame(rows))
    leaf_code = pl.coalesce("l3_code", "l2_code", "l1_code")
    leaf_name = pl.coalesce("l3_name", "l2_name", "l1_name")
    return members.select(
        "symbol",
        pl.lit("eastmoney").alias("classification_system"),
        leaf_code.alias("industry_code"),
        leaf_name.alias("industry_name"),
        "l1_code",
        "l1_name",
        "l2_code",
        "l2_name",
        "l3_code",
        "l3_name",
        pl.lit(as_of_date).alias("as_of_date"),
    )
