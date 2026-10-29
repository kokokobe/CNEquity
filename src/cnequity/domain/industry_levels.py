"""Industry hierarchy levels for ``industry_members``.

EastMoney's ``RPT_BOARD_CONSTITUENT`` lists every stock under three industry
boards (level 1, 2 and 3) and says nothing about which is which. A stock's
three boards always nest, so a bigger board is a higher level. Where a parent
has a single child the two boards have the same members; EastMoney then names
the lower one with the Shenwan suffix (``Ⅱ`` / ``Ⅲ``), which breaks the tie.
Measured on 2026-10-25: every one of 496 boards got one level across all its
stocks (31 / 128 / 337), and no stock's chain failed to nest.

Shenwan rows carry the six-digit level-3 code; its level-1 and level-2 codes
are the same code with the last four or two digits zeroed. The Shenwan file
publishes no names, so those columns stay null.
"""

from __future__ import annotations

import polars as pl

LEVEL_COLUMNS: tuple[str, ...] = (
    "l1_code",
    "l1_name",
    "l2_code",
    "l2_name",
    "l3_code",
    "l3_name",
)


def _suffix_rank() -> pl.Expr:
    name = pl.col("board_name")
    return (
        pl.when(name.str.ends_with("Ⅲ")).then(3).when(name.str.ends_with("Ⅱ")).then(2).otherwise(1)
    )


def board_levels(boards: pl.DataFrame) -> pl.DataFrame:
    """``[board_code, board_name, level, consistent]`` from ``[symbol, board_code, board_name]``.

    ``consistent`` is false for a board whose stocks disagree about its level;
    the level then comes from the majority.
    """
    size = boards.group_by("board_code").agg(pl.col("symbol").n_unique().alias("_size"))
    ranked = (
        boards.unique(["symbol", "board_code"])
        .join(size, on="board_code")
        .with_columns(_suffix_rank().alias("_suffix"))
        .sort(["symbol", "_size", "_suffix"], descending=[False, True, False])
        .with_columns(pl.int_range(1, pl.len() + 1).over("symbol").alias("_pos"))
    )
    votes = ranked.group_by("board_code", "_pos").agg(
        pl.len().alias("_n"), pl.col("board_name").first()
    )
    return (
        votes.sort(["board_code", "_n", "_pos"], descending=[False, True, False])
        .group_by("board_code", maintain_order=True)
        .agg(
            pl.col("board_name").first(),
            pl.col("_pos").first().cast(pl.Int8).alias("level"),
            (pl.col("_pos").n_unique() == 1).alias("consistent"),
        )
    )


def em_member_levels(boards: pl.DataFrame) -> pl.DataFrame:
    """One row per stock with its level 1/2/3 board, from ``[symbol, board_code, board_name]``.

    A level the source did not list for a stock stays null.
    """
    levels = board_levels(boards).select("board_code", "level")
    tagged = boards.unique(["symbol", "board_code"]).join(levels, on="board_code")
    out = tagged.select("symbol").unique()
    for level in (1, 2, 3):
        part = (
            tagged.filter(pl.col("level") == level)
            .unique("symbol", keep="first")
            .select(
                "symbol",
                pl.col("board_code").alias(f"l{level}_code"),
                pl.col("board_name").alias(f"l{level}_name"),
            )
        )
        out = out.join(part, on="symbol", how="left")
    return out.sort("symbol")


def board_ancestry(boards: pl.DataFrame) -> pl.DataFrame:
    """Each board's own level and the codes and names of the boards above it.

    ``[board_code, level, l1_code, l1_name, l2_code, l2_name, l3_code, l3_name]``;
    columns below a board's own level are null. A board's parents are taken
    from the stocks that list it, so they are only as current as ``boards``.
    """
    members = em_member_levels(boards)
    levels = board_levels(boards).select("board_code", "level")
    parts = []
    for level in (1, 2, 3):
        keep = [c for c in LEVEL_COLUMNS if int(c[1]) <= level]
        parts.append(
            members.filter(pl.col(f"l{level}_code").is_not_null())
            .select(pl.col(f"l{level}_code").alias("board_code"), *keep)
            .unique("board_code", keep="first")
        )
    ancestry = pl.concat(parts, how="diagonal_relaxed").select("board_code", *LEVEL_COLUMNS)
    return levels.join(ancestry, on="board_code", how="left")


def sw_level_columns() -> list[pl.Expr]:
    """Level columns for Shenwan rows, from the six-digit level-3 ``industry_code``."""
    code = pl.col("industry_code").cast(pl.Utf8)
    six = code.str.len_chars() == 6
    return [
        pl.when(six).then(code.str.slice(0, 2) + "0000").alias("l1_code"),
        pl.lit(None, dtype=pl.Utf8).alias("l1_name"),
        pl.when(six).then(code.str.slice(0, 4) + "00").alias("l2_code"),
        pl.lit(None, dtype=pl.Utf8).alias("l2_name"),
        pl.when(six).then(code).alias("l3_code"),
        pl.lit(None, dtype=pl.Utf8).alias("l3_name"),
    ]
