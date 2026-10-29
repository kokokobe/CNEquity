"""Evidence for which Shenwan and EastMoney industries are the same — lake-only derive.

Nothing is merged. For a date on which both systems have a full snapshot, each
pair of industries at the same level that share members gets a row with the
member counts, the overlap, the Jaccard index and how much of each side lies
in the other, plus a ``relation`` read from those numbers. Each run adds that
date's evidence and keeps earlier dates, so whether a pairing holds can be read
over time.

Only same-day evidence counts: comparing snapshots months apart mixes real
reclassification into the mapping. Only stocks present in both snapshots that
day are compared, so a stock one source has not classified yet does not read
as disagreement. An EastMoney snapshot written before schema version 2 kept one
random level per stock and is never used as evidence.

Measured on a three-month-apart pair (SW 2026-07-16, EastMoney 2026-10-25):
EastMoney's tree mirrors Shenwan 2021, with 26 / 82 / 220 level 1 / 2 / 3
industries at Jaccard >= 0.95 and 97-99% of stocks in the matched industry.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import polars as pl

from cnequity.config import Config
from cnequity.derive.industry_taxonomy import SW_2021_FROM
from cnequity.storage.atomic import write_json_atomic, write_parquet_atomic

CROSSWALK = "industry_crosswalk"
LEFT, RIGHT = "sw", "eastmoney"
#: Members agree this closely: the two are the same industry.
EQUIVALENT = 0.95
#: This share of one side lies in the other: it is contained in it.
CONTAINED = 0.95
#: A partial overlap below this share of both sides is noise, not a relation.
MIN_SHARE = 0.10

SCHEMA = {
    "evidence_date": pl.Date,
    "level": pl.Int8,
    "left_id": pl.Utf8,
    "right_id": pl.Utf8,
    "right_name": pl.Utf8,
    "left_members": pl.UInt32,
    "right_members": pl.UInt32,
    "overlap": pl.UInt32,
    "jaccard": pl.Float64,
    "left_in_right": pl.Float64,
    "right_in_left": pl.Float64,
    "relation": pl.Utf8,
    "best_for_left": pl.Boolean,
    "best_for_right": pl.Boolean,
}


def _path(config: Config) -> Path:
    return config.meta_root / f"{CROSSWALK}.parquet"


def _summary_path(config: Config) -> Path:
    return config.meta_root / f"{CROSSWALK}_summary.json"


def load_industry_crosswalk(config: Config) -> pl.DataFrame:
    path = _path(config)
    return pl.read_parquet(path) if path.exists() else pl.DataFrame(schema=SCHEMA)


def evidence_dates(members: pl.DataFrame) -> list[date]:
    """Dates with a Shenwan snapshot and a full-level (v2) EastMoney snapshot."""
    sw = set(members.filter(pl.col("classification_system") == LEFT)["as_of_date"].to_list())
    em = members.filter(pl.col("classification_system") == RIGHT)
    partial = set(em.filter(pl.col("data_version") == "v1")["as_of_date"].to_list())
    full = set(em["as_of_date"].to_list()) - partial
    return sorted(sw & full)


def _ids(frame: pl.DataFrame, system: str, version: str, level: int) -> pl.DataFrame:
    code = f"l{level}_code"
    return frame.filter(pl.col(code).is_not_null()).select(
        "symbol",
        pl.concat_str([pl.lit(f"{system}:{version}:"), pl.col(code)]).alias("id"),
        pl.col(f"l{level}_name").alias("name"),
    )


def build_crosswalk(members: pl.DataFrame, evidence_date: date) -> pl.DataFrame:
    """Same-level overlap evidence between Shenwan and EastMoney on one date."""
    day = members.filter(pl.col("as_of_date") == evidence_date)
    sw = day.filter(pl.col("classification_system") == LEFT)
    em = day.filter(pl.col("classification_system") == RIGHT)
    common = set(sw["symbol"].to_list()) & set(em["symbol"].to_list())
    sw = sw.filter(pl.col("symbol").is_in(sorted(common)))
    em = em.filter(pl.col("symbol").is_in(sorted(common)))
    version = "2021" if evidence_date >= SW_2021_FROM else "2014"
    parts = []
    for level in (1, 2, 3):
        left = _ids(sw, LEFT, version, level).rename({"id": "left_id"}).drop("name")
        right = _ids(em, RIGHT, "board", level).rename({"id": "right_id", "name": "right_name"})
        if left.is_empty() or right.is_empty():
            continue
        sizes_l = left.group_by("left_id").agg(pl.len().cast(pl.UInt32).alias("left_members"))
        sizes_r = right.group_by("right_id").agg(
            pl.len().cast(pl.UInt32).alias("right_members"), pl.col("right_name").first()
        )
        pairs = (
            left.join(right.drop("right_name"), on="symbol")
            .group_by("left_id", "right_id")
            .agg(pl.len().cast(pl.UInt32).alias("overlap"))
            .join(sizes_l, on="left_id")
            .join(sizes_r, on="right_id")
            .with_columns(
                (
                    pl.col("overlap")
                    / (pl.col("left_members") + pl.col("right_members") - pl.col("overlap"))
                ).alias("jaccard"),
                (pl.col("overlap") / pl.col("left_members")).alias("left_in_right"),
                (pl.col("overlap") / pl.col("right_members")).alias("right_in_left"),
            )
        )
        relation = (
            pl.when(pl.col("jaccard") >= EQUIVALENT)
            .then(pl.lit("equivalent"))
            .when(pl.col("left_in_right") >= CONTAINED)
            .then(pl.lit("left_within_right"))
            .when(pl.col("right_in_left") >= CONTAINED)
            .then(pl.lit("right_within_left"))
            .otherwise(pl.lit("partial"))
        )
        pairs = pairs.with_columns(relation.alias("relation")).filter(
            (pl.col("relation") != "partial")
            | (pl.col("left_in_right") >= MIN_SHARE)
            | (pl.col("right_in_left") >= MIN_SHARE)
        )
        best_l = pl.col("jaccard") == pl.col("jaccard").max().over("left_id")
        best_r = pl.col("jaccard") == pl.col("jaccard").max().over("right_id")
        parts.append(
            pairs.with_columns(
                pl.lit(evidence_date).alias("evidence_date"),
                pl.lit(level, dtype=pl.Int8).alias("level"),
                best_l.alias("best_for_left"),
                best_r.alias("best_for_right"),
            )
        )
    if not parts:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl.concat(parts)
        .select(list(SCHEMA))
        .cast(SCHEMA)
        .sort("level", "left_id", "jaccard", descending=[False, False, True])
    )


def _summary(table: pl.DataFrame, evidence_date: date) -> dict:
    day = table.filter(pl.col("evidence_date") == evidence_date)
    out: dict = {"evidence_date": str(evidence_date), "levels": {}}
    for level in (1, 2, 3):
        rows = day.filter(pl.col("level") == level)
        if rows.is_empty():
            continue
        best = rows.filter(pl.col("best_for_left"))
        out["levels"][f"L{level}"] = {
            "left_industries": rows["left_id"].n_unique(),
            "right_industries": rows["right_id"].n_unique(),
            "pairs": rows.height,
            "by_relation": dict(rows.group_by("relation").len().sort("relation").iter_rows()),
            "left_equivalent": best.filter(pl.col("relation") == "equivalent")[
                "left_id"
            ].n_unique(),
            # Stocks whose Shenwan industry's best EastMoney match holds them.
            "stock_agreement": round(
                float(best["overlap"].sum()) / float(best["left_members"].sum()), 4
            )
            if best.height
            else None,
        }
    return out


def derive_industry_crosswalk(config: Config, *, as_of: date | None = None) -> dict:
    """Add one date's evidence to ``meta/industry_crosswalk.parquet`` from the lake only.

    Uses the latest date with both full snapshots, or the latest on or before
    ``as_of``. Earlier evidence dates are kept; the chosen date is replaced.
    """
    from cnequity.query.reader import load

    members = load("industry_members", config=config)
    dates = [d for d in evidence_dates(members) if as_of is None or d <= as_of]
    if not dates:
        sw = members.filter(pl.col("classification_system") == LEFT)["as_of_date"].max()
        em = members.filter(
            (pl.col("classification_system") == RIGHT) & (pl.col("data_version") != "v1")
        )["as_of_date"].max()
        raise RuntimeError(
            "industry_crosswalk: no date has both a Shenwan snapshot and a full-level "
            f"EastMoney snapshot (latest Shenwan {sw}, latest full EastMoney {em}). "
            "Run the daily update, then `cne backfill industry_members` after a month closes."
        )
    day = dates[-1]
    fresh = build_crosswalk(members, day)
    kept = load_industry_crosswalk(config).filter(pl.col("evidence_date") != day)
    table = pl.concat([kept.cast(SCHEMA), fresh]).sort("evidence_date", "level", "left_id")
    summary = _summary(table, day)
    summary.update(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        evidence_dates=sorted({str(d) for d in table["evidence_date"].to_list()}),
    )
    config.meta_root.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(_path(config), table)
    write_json_atomic(_summary_path(config), summary, indent=2, default=str)
    return summary
