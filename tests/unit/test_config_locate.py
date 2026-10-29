"""Config discovery and config-relative paths: one lake per config, wherever a command runs."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from cnequity.cli.main import cli
from cnequity.config import load_config
from cnequity.config import loader as loader_mod
from cnequity.config.bootstrap import example_toml_text
from cnequity.config.locate import (
    PROJECT_CONFIG,
    default_config_path,
    find_config,
    user_config_path,
)
from cnequity.config.upgrade import plan_upgrade
from cnequity.query.reader import ReaderError, resolve_config


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    """An empty per-user config directory and an unrelated working directory."""
    home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.delenv("CNE_CONFIG", raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return home


def _write(path: Path, root: str) -> Path:
    """A config whose root is written as given: relative stays relative."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'[data]\nroot = "{Path(root).as_posix()}"\n', encoding="utf-8")
    return path


def test_user_config_follows_xdg_and_ignores_a_relative_value(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_config_path() == tmp_path / "cnequity" / "config.toml"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
    assert user_config_path() == Path.home() / ".config" / "cnequity" / "config.toml"


def test_user_config_on_windows_uses_appdata(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert user_config_path() == tmp_path / "cnequity" / "config.toml"


def test_project_config_wins_over_the_user_config(user_home):
    assert find_config() is None
    assert default_config_path() == user_config_path()
    _write(user_config_path(), "/lake/user")
    assert find_config() == user_config_path()
    _write(PROJECT_CONFIG, "/lake/project")
    assert find_config() == PROJECT_CONFIG


def test_a_command_uses_the_user_config_from_any_directory(user_home, tmp_path):
    _write(user_config_path(), (tmp_path / "lake").as_posix())

    result = CliRunner().invoke(cli, ["config", "path"])

    assert result.exit_code == 0, result.output
    assert "per-user config" in result.output
    assert str(tmp_path / "lake") in result.output


def test_missing_implicit_config_names_both_locations(user_home):
    result = CliRunner().invoke(cli, ["config", "validate"])

    assert result.exit_code != 0
    assert "configs/cnequity.toml" in result.output
    assert str(user_config_path()) in result.output


def test_config_path_reports_where_init_would_write(user_home):
    result = CliRunner().invoke(cli, ["config", "path"])

    assert result.exit_code == 0, result.output
    assert "Not created yet" in result.output


def test_config_path_rejects_a_missing_explicit_config(user_home, tmp_path):
    missing = tmp_path / "nope.toml"
    result = CliRunner().invoke(cli, ["config", "path", "--config", str(missing)])

    assert result.exit_code != 0
    assert "Config not found" in result.output


def test_config_create_without_config_writes_the_user_config(user_home, tmp_path):
    result = CliRunner().invoke(cli, ["config", "create", "--data-root", str(tmp_path / "lake")])

    assert result.exit_code == 0, result.output
    assert user_config_path().is_file()
    assert load_config(user_config_path()).data_root == (tmp_path / "lake").resolve()


def test_relative_root_resolves_against_the_config_directory(user_home, tmp_path):
    config = _write(tmp_path / "cfgdir" / "lake.toml", "lake")

    cfg = load_config(config)

    assert cfg.data_root == (tmp_path / "cfgdir" / "lake").absolute()


def test_relative_duckdb_path_resolves_against_the_config_directory(user_home, tmp_path):
    config = tmp_path / "cfgdir" / "lake.toml"
    config.parent.mkdir()
    config.write_text(
        f'[data]\nroot = "{tmp_path.as_posix()}/lake"\n[duckdb]\npath = "db/x.duckdb"\n',
        encoding="utf-8",
    )

    assert load_config(config).duckdb_path == (config.parent / "db" / "x.duckdb").resolve()


def test_legacy_relative_root_keeps_its_lake_and_warns(user_home, tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(loader_mod, "_WARNED_LEGACY_ROOTS", set())
    (Path.cwd() / "data" / "cnequity").mkdir(parents=True)
    config = _write(Path.cwd() / "configs" / "cnequity.toml", "./data/cnequity")

    with caplog.at_level(logging.WARNING, logger="cnequity.config.loader"):
        cfg = load_config(config)

    assert cfg.data_root == Path.cwd() / "data" / "cnequity"
    assert "cne config upgrade" in caplog.text


def test_upgrade_pins_a_legacy_relative_root(user_home):
    (Path.cwd() / "data" / "cnequity").mkdir(parents=True)
    config = Path.cwd() / "configs" / "cnequity.toml"
    config.parent.mkdir()
    # `config create` writes workers = 1 on macOS; validation after the upgrade insists on it.
    text = re.sub(r"(?m)^workers = \d+", "workers = 1", example_toml_text())
    config.write_text(
        text.replace('root = "../data/cnequity"', 'root = "./data/cnequity"'), encoding="utf-8"
    )

    plan = plan_upgrade(config)

    assert plan.pinned_root == ("./data/cnequity", str(Path.cwd() / "data" / "cnequity"))
    result = CliRunner().invoke(cli, ["config", "upgrade"])
    assert result.exit_code == 0, result.output
    assert load_config(config).data_root == Path.cwd() / "data" / "cnequity"
    assert 'root = "./data/cnequity"' not in config.read_text(encoding="utf-8")


def test_upgrade_leaves_a_root_that_resolves_next_to_the_config(user_home):
    config = Path.cwd() / "configs" / "cnequity.toml"
    config.parent.mkdir()
    config.write_text(example_toml_text(), encoding="utf-8")

    assert plan_upgrade(config).pinned_root is None


def test_load_finds_the_user_config_and_honours_cne_config(user_home, tmp_path, monkeypatch):
    with pytest.raises(ReaderError, match="No config found"):
        resolve_config()
    _write(user_config_path(), (tmp_path / "user-lake").as_posix())
    assert resolve_config().data_root == tmp_path / "user-lake"
    other = _write(tmp_path / "other.toml", (tmp_path / "env-lake").as_posix())
    monkeypatch.setenv("CNE_CONFIG", str(other))
    assert resolve_config().data_root == tmp_path / "env-lake"
