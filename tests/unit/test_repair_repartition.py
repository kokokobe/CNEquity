"""`cne repair repartition`: rewrite partitions at the registered granularity.

What triggers it is a registry granularity change landing on an existing lake.
Reads already self-describe from the directory shape, so this only reclaims
space and file handles — it never fixes a correctness problem.

`cnequity.quality.dataset_checks` names the command in the findings it emits, so
`test_curated_hygiene` covers that the advice still points somewhere real.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cnequity.cli.main import cli
from cnequity.config.bootstrap import path_for_toml
from cnequity.storage.repairs import repartition as module
from cnequity.storage.repairs.repartition import RepartitionError, RepartitionResult


@pytest.fixture
def cfg_path(tmp_path):
    path = tmp_path / "cnequity.toml"
    path.write_text(f'[data]\nroot = "{path_for_toml(tmp_path / "data")}"\n', encoding="utf-8")
    return str(path)


def _result(name: str) -> RepartitionResult:
    return RepartitionResult(
        dataset=name,
        changed=True,
        rows=10,
        files_before=5,
        files_after=1,
        partitions_before=5,
        partitions_after=1,
        bytes_before=1_000_000,
        bytes_after=200_000,
    )


def _run(*args):
    return CliRunner().invoke(cli, ["repair", "repartition", *args])


def test_bare_invocation_only_lists_and_never_writes(cfg_path, monkeypatch):
    """The listing form has to stay safe to run: the hygiene findings point here."""
    monkeypatch.setattr(module, "repartition_candidates", lambda cfg: ["index_bars"])
    monkeypatch.setattr(
        module, "repartition_dataset", lambda *a, **k: pytest.fail("listing must not rewrite")
    )

    result = _run("--config", cfg_path)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["needs_repartition"] == ["index_bars"]


def test_a_dataset_previews_by_default_and_apply_rewrites(cfg_path, monkeypatch):
    calls = []
    monkeypatch.setattr(module, "repartition_candidates", lambda cfg: ["index_bars"])
    monkeypatch.setattr(
        module,
        "repartition_dataset",
        lambda cfg, name, dry_run=False: calls.append(dry_run) or _result(name),
    )

    preview = _run("index_bars", "--config", cfg_path)
    applied = _run("index_bars", "--apply", "--config", cfg_path)

    assert preview.exit_code == 0 and applied.exit_code == 0
    assert calls == [True, False]
    payload = json.loads(applied.output)
    assert payload["applied"] is True and payload["results"][0]["mb_saved"] == 0.8


def test_all_runs_every_candidate(cfg_path, monkeypatch):
    monkeypatch.setattr(module, "repartition_candidates", lambda cfg: ["a", "b"])
    monkeypatch.setattr(
        module, "repartition_dataset", lambda cfg, name, dry_run=False: _result(name)
    )

    result = _run("--all", "--config", cfg_path)

    assert [r["dataset"] for r in json.loads(result.output)["results"]] == ["a", "b"]


def test_dataset_and_all_together_is_refused(cfg_path):
    assert _run("index_bars", "--all", "--config", cfg_path).exit_code != 0


def test_a_repartition_error_is_reported_cleanly(cfg_path, monkeypatch):
    monkeypatch.setattr(module, "repartition_candidates", lambda cfg: [])

    def boom(cfg, name, dry_run=False):
        raise RepartitionError("cannot repartition index_bars: mixed schema")

    monkeypatch.setattr(module, "repartition_dataset", boom)

    result = _run("index_bars", "--config", cfg_path)

    assert result.exit_code != 0
    assert "mixed schema" in result.output
