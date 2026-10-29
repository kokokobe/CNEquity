# Industry levels in industry_members

## Change

Schema version 2 adds six nullable columns, `l1_code`, `l1_name`, `l2_code`,
`l2_name`, `l3_code` and `l3_name`, and fixes `industry_code`/`industry_name`
to the level-3 (leaf) industry. Rows written this way carry `data_version=v2`.
The primary key is unchanged: one row per security, classification system and
snapshot date.

EastMoney lists each stock under its level 1, 2 and 3 industry boards. The v1
adapter kept one of the three, chosen by the source's page order, so a stock's
`industry_code` changed level from one day to the next and per-industry counts
moved as if whole industries were missing.

## Migration

Run `cne migrate` to preview and `cne migrate --apply` to fill the level
columns on rows already in the lake. Shenwan rows need only the lake. EastMoney
rows are matched to the board hierarchy EastMoney publishes at migration time;
each v1 row gets the level of the board it stored and the levels above it. The
levels below were never stored and stay null, and `industry_code` keeps the
board it held. Boards no longer listed by EastMoney stay without levels.

Readers that group EastMoney rows by industry should use the `lN_code` columns
for a fixed level. `industry_code` on a v1 EastMoney row is not a level-3 code.

## Rollback

The migration publishes a new revision and keeps the previous one readable.
Software before 0.20 ignores the new columns; it reads v2 EastMoney rows'
`industry_code` as the level-3 leaf, which is a stable but narrower grouping
than the mixed levels it used to see.
