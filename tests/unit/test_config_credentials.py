"""Credentials: environment, then a file, then the config itself — and doctor says which."""

from __future__ import annotations

import dataclasses
import os
import sys

import pytest

from cnequity.config import load_config
from cnequity.config.bootstrap import path_for_toml
from cnequity.config.errors import ConfigError
from cnequity.diagnostics.report import Severity, build_report
from cnequity.storage.provenance import config_fingerprint


def _config(tmp_path, body: str):
    path = tmp_path / "cnequity.toml"
    path.write_text(
        f'[data]\nroot = "{path_for_toml(tmp_path / "lake")}"\n{body}', encoding="utf-8"
    )
    return path


def test_environment_outranks_file_and_inline(tmp_path, monkeypatch):
    (tmp_path / "key").write_text("from-file\n", encoding="utf-8")
    path = _config(
        tmp_path,
        '[sources.ths_official]\napi_key = "inline"\napi_key_file = "key"\n',
    )

    assert load_config(path).ths_official_api_key == "from-file"
    monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "from-env")
    assert load_config(path).ths_official_api_key == "from-env"


def test_file_is_read_relative_to_the_config(tmp_path, monkeypatch):
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "tushare").write_text("tok\n", encoding="utf-8")
    path = _config(tmp_path, '[sources.tushare]\ntoken_file = "secrets/tushare"\n')
    monkeypatch.chdir(tmp_path / "secrets")

    cfg = load_config(path)

    assert cfg.tushare_token == "tok"
    assert cfg.inline_credentials == ()


def test_unreadable_or_empty_file_names_the_key_not_a_value(tmp_path):
    missing = _config(tmp_path, '[sources.tushare]\ntoken_file = "nope"\n')
    with pytest.raises(ConfigError, match=r"\[sources\.tushare\]\.token_file: cannot read"):
        load_config(missing)
    (tmp_path / "empty").write_text("\n", encoding="utf-8")
    empty = _config(tmp_path, '[sources.tushare]\ntoken_file = "empty"\n')
    with pytest.raises(ConfigError, match="is empty"):
        load_config(empty)


def test_doctor_warns_about_an_inline_credential_and_loose_permissions(tmp_path):
    path = _config(tmp_path, '[sources.tushare]\ntoken = "inline-secret"\n')
    if sys.platform != "win32":
        os.chmod(path, 0o644)
    cfg = load_config(path)

    report = build_report(config=cfg, config_path=path)

    titles = [str(f.title) for f in report.findings if f.severity is Severity.WARN]
    assert any("sources.tushare.token" in t for t in titles), titles
    if sys.platform != "win32":
        assert any("readable by others" in t for t in titles), titles
    assert "inline-secret" not in repr(report)


def test_no_inline_credential_no_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "env-token")
    cfg = load_config(_config(tmp_path, ""))

    report = build_report(config=cfg, config_path=cfg.config_path)

    assert not [f for f in report.findings if "A credential is written" in str(f.title)]


def test_fingerprint_ignores_credential_values_and_proxy_stays_out_of_repr(tmp_path):
    cfg = load_config(_config(tmp_path, '[sources.eastmoney]\nproxy = "http://u:p@h:1"\n'))

    assert "u:p@" not in repr(cfg)
    first = dataclasses.replace(cfg, ths_official_api_key="k1")
    second = dataclasses.replace(cfg, ths_official_api_key="k2")
    assert config_fingerprint(first) == config_fingerprint(second)
    assert config_fingerprint(first) != config_fingerprint(cfg)
