"""`cne backfill` keeps the generic options; dataset-specific modes moved to repair/import."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from cnequity.cli import backfill_cmds
from cnequity.cli.main import cli


@pytest.fixture
def captured(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(backfill_cmds.backfill, "callback", lambda **kw: seen.append(kw))
    return seen


@pytest.mark.parametrize(
    ("argv", "dataset", "flags"),
    [
        (
            [
                "repair",
                "payment-dates",
                "--symbols",
                "600519.SH",
                "--start",
                "2024-01-01",
                "--end",
                "2024-12-31",
            ],
            "corporate_actions",
            {"payment_date_repair": True, "issuer_notice_repair": False},
        ),
        (
            [
                "repair",
                "payment-dates",
                "--symbols",
                "600519.SH",
                "--start",
                "2024-01-01",
                "--end",
                "2024-12-31",
                "--issuer-notices-only",
            ],
            "corporate_actions",
            {"payment_date_repair": False, "issuer_notice_repair": True},
        ),
        (
            ["repair", "delisted-actions", "--source", "ths"],
            "corporate_actions",
            {"ths_repair": True, "baostock_repair": False},
        ),
        (
            ["repair", "ex-dates", "--ex-dates", "2014-06-30"],
            "corporate_actions",
            {"eastmoney_date_repair": True, "ex_dates_str": "2014-06-30"},
        ),
        (
            ["repair", "bar-amounts", "--start", "2024-01-01", "--end", "2024-01-31"],
            "daily_bars",
            {"tdx_amount_repair": True},
        ),
        (
            [
                "repair",
                "bar-turnover",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--peer-review",
            ],
            "daily_bars",
            {"turnover_repair": True, "turnover_peer_review": True},
        ),
        (
            ["repair", "bse-tip-turnover", "--symbols", "920001.BJ", "--date", "2026-10-23"],
            "daily_bars",
            {"bse_tip_repair": True, "start_str": "2026-10-23", "end_str": "2026-10-23"},
        ),
        (
            ["repair", "valuation-outage", "--start", "2026-09-22", "--end", "2026-09-30"],
            "valuation_metrics",
            {"fill_em_outage": True},
        ),
    ],
)
def test_repairs_preview_by_default_and_map_to_the_backfill_mode(captured, argv, dataset, flags):
    preview = CliRunner().invoke(cli, argv)
    assert preview.exit_code == 0, preview.output
    applied = CliRunner().invoke(cli, [*argv, "--apply"])
    assert applied.exit_code == 0, applied.output

    first, second = captured
    assert first["dataset"] == second["dataset"] == dataset
    assert first["plan"] is True and second["plan"] is False
    for key, value in flags.items():
        assert first[key] == value, key
    assert "Preview only" in preview.output


def test_import_shfe_archive_maps_to_the_annual_import(captured, tmp_path):
    archive = tmp_path / "shfe-2018.zip"
    archive.write_bytes(b"PK")

    result = CliRunner().invoke(
        cli,
        [
            "import",
            "shfe-archive",
            str(archive),
            "--dataset",
            "futures_bars",
            "--year",
            "2018",
            "--accept-partial-fields",
            "--apply",
        ],
    )

    assert result.exit_code == 0, result.output
    (call,) = captured
    assert call["shfe_annual_archive"] == archive and call["archive_year"] == 2018
    assert call["plan"] is False


def test_backfill_help_lists_only_generic_options():
    page = CliRunner().invoke(cli, ["backfill", "--help"]).output
    assert "--refetch" in page and "--source" in page
    for gone in ("--payment-date-repair", "--shfe-annual-archive", "--margin-source", "--force"):
        assert gone not in page


def test_refetch_maps_per_dataset_and_rejects_others(config):
    result = CliRunner().invoke(
        cli, ["backfill", "daily_bars", "--refetch", "--plan", "--config", str(config.config_path)]
    )
    assert result.exit_code != 0
    assert "--refetch applies to" in result.output
