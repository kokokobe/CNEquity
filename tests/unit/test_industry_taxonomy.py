"""An industry identity is system + version + code: Shenwan reused code strings in 2021."""

from datetime import date

import polars as pl

from cnequity.derive.industry_taxonomy import SW_2021_FROM, build_industry_taxonomy


def _row(symbol, day, l3, *, system="sw", names=(None, None, None)):
    if system == "sw":
        l1, l2 = l3[:2] + "0000", l3[:4] + "00"
    else:
        l1, l2 = "1" + l3[:2], "2" + l3[:2]
    return {
        "symbol": symbol,
        "classification_system": system,
        "l1_code": l1,
        "l1_name": names[0],
        "l2_code": l2,
        "l2_name": names[1],
        "l3_code": l3,
        "l3_name": names[2],
        "as_of_date": day,
    }


def test_a_reused_shenwan_code_is_two_identities():
    before, after = date(2021, 6, 30), SW_2021_FROM
    table = build_industry_taxonomy(
        pl.DataFrame([_row("600000.SH", before, "480101"), _row("600000.SH", after, "480101")])
    )
    leaf = table.filter(pl.col("level") == 3)
    assert leaf["industry_id"].to_list() == ["sw:2014:480101", "sw:2021:480101"]
    assert leaf["last_seen"].to_list() == [before, after]
    assert dict(zip(table["industry_id"], table["parent_id"], strict=True))["sw:2021:480101"] == (
        "sw:2021:480100"
    )


def test_eastmoney_boards_keep_their_names_parents_and_sizes():
    day = date(2026, 10, 23)
    names = ("食品饮料", "白酒Ⅱ", "白酒Ⅲ")
    table = build_industry_taxonomy(
        pl.DataFrame(
            [
                _row("600519.SH", day, "575", system="eastmoney", names=names),
                _row("000858.SZ", day, "575", system="eastmoney", names=names),
            ]
        )
    )
    by = {r["industry_id"]: r for r in table.iter_rows(named=True)}
    assert by["eastmoney:board:575"]["name"] == "白酒Ⅲ"
    assert by["eastmoney:board:575"]["parent_id"] == "eastmoney:board:257"
    assert by["eastmoney:board:157"]["parent_id"] is None
    assert by["eastmoney:board:575"]["max_members"] == 2
    # Shenwan publishes no names; none are invented.
    sw = build_industry_taxonomy(pl.DataFrame([_row("600000.SH", day, "480101")]))
    assert sw["name"].null_count() == sw.height
