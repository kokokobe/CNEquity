"""Read-only manifest queries release connections even when a query fails."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date

import pytest

from cnequity.config import Config
from cnequity.diagnostics.source_limits import _latest_run_metrics
from cnequity.orchestrator.manifest import Manifest
from cnequity.orchestrator.run_window import _ran_in_manifest
from cnequity.serve.ops.job import launch_runs


@pytest.mark.parametrize(
    "read",
    [
        lambda cfg: _ran_in_manifest(cfg, "daily", date(2024, 6, 28)),
        _latest_run_metrics,
        lambda cfg: launch_runs(cfg, "test-launch", None),
    ],
    ids=["run-window", "source-limits", "launch-runs"],
)
@pytest.mark.parametrize("missing_table", [False, True], ids=["success", "query-error"])
def test_manifest_reader_closes_connection(tmp_path, monkeypatch, read, missing_table):
    cfg = Config(data_root=tmp_path / "lake")
    Manifest(cfg.manifest_path).start_run(
        "daily", {"trade_date": "2024-06-28", "launch": {"id": "test-launch"}}
    )
    if missing_table:
        with closing(sqlite3.connect(cfg.manifest_path)) as conn:
            conn.execute("DROP TABLE ingestion_runs")
            conn.commit()

    connections = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)

    read(cfg)

    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
