"""`[[job.events.schedules]]`: several event cadences under the packaged timer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cnequity.config import EventSchedule, load_config, validate_config
from cnequity.serve.ops import event_schedules as es
from cnequity.serve.ops.records import atomic_write
from cnequity.serve.ops.scheduled import _start_event_schedule, claim_session
from cnequity.serve.ops.scheduler import state_dir

NEWS = EventSchedule(
    every_minutes=15, group="news", jitter_seconds=120, quiet_hours="1-7", quiet_every_minutes=60
)
ALL = EventSchedule(every_minutes=360, jitter_seconds=300)


def _at(hour_beijing: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, 24, hour_beijing, minute, tzinfo=es.BEIJING)


def test_config_parses_schedules_and_all_groups(config):
    path = config.config_path
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n[job.events.groups.news]\nsteps = ["news_headlines"]\n'
        '[[job.events.schedules]]\ngroup = "news"\nevery_minutes = 15\njitter_seconds = 120\n'
        'quiet_hours = "1-7"\nquiet_every_minutes = 60\n'
        "[[job.events.schedules]]\nevery_minutes = 360\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert [s.id for s in cfg.event_schedules] == ["news", "all"]
    assert cfg.event_schedules[0].quiet_hours == "1-7"
    assert not [e for e in validate_config(cfg) if "schedules" in e]


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        (EventSchedule(every_minutes=0), "1-1440"),
        (EventSchedule(every_minutes=5, jitter_seconds=9999), "jitter_seconds"),
        (EventSchedule(every_minutes=5, quiet_hours="7-7"), "quiet_hours"),
        (EventSchedule(every_minutes=5, group="nope"), "unknown event group"),
    ],
)
def test_invalid_schedules_are_reported(config, spec, fragment):
    config.event_schedules = [spec]
    assert any(fragment in e for e in validate_config(config))


def test_duplicate_schedules_are_reported(config):
    config.event_schedules = [ALL, ALL]
    assert any("same event group" in e for e in validate_config(config))


def test_quiet_hours_follow_beijing_time_and_wrap_midnight():
    assert es.in_quiet_hours(NEWS, _at(1)) and es.in_quiet_hours(NEWS, _at(6, 59))
    assert not es.in_quiet_hours(NEWS, _at(7)) and not es.in_quiet_hours(NEWS, _at(0, 59))
    wrap = EventSchedule(every_minutes=15, quiet_hours="23-6")
    assert es.in_quiet_hours(wrap, _at(23)) and es.in_quiet_hours(wrap, _at(5))
    assert not es.in_quiet_hours(wrap, _at(12))


def test_due_uses_the_quiet_spacing_with_slack(tmp_path):
    atomic_write(
        tmp_path / es.STATE_FILE, {"news": {"at": _at(2).isoformat(), "offset_seconds": 0}}
    )
    assert not es.is_due(NEWS, tmp_path, _at(2, 54))
    assert es.is_due(NEWS, tmp_path, _at(2, 55))  # 60 minutes less 5 of slack
    atomic_write(
        tmp_path / es.STATE_FILE, {"news": {"at": _at(10).isoformat(), "offset_seconds": 0}}
    )
    assert not es.is_due(NEWS, tmp_path, _at(10, 14))
    assert es.is_due(NEWS, tmp_path, _at(10, 15))


def test_jitter_delays_the_next_run_within_bounds(tmp_path):
    es.record_attempt(NEWS, tmp_path, _at(10))
    offset = es.last_attempts(tmp_path)["news"]["offset_seconds"]
    assert 0 <= offset <= 120
    assert not es.is_due(NEWS, tmp_path, _at(10, 15) + timedelta(seconds=offset - 1))
    assert es.is_due(NEWS, tmp_path, _at(10, 15) + timedelta(seconds=offset))


def test_a_missing_time_zone_is_refused(tmp_path):
    atomic_write(tmp_path / es.STATE_FILE, {"news": {"at": "2026-10-24T10:00:00"}})
    with pytest.raises(ValueError):
        es.is_due(NEWS, tmp_path, _at(11))


class _Service:
    def __init__(self):
        self.started: list[dict] = []

    def preview(self, operation, params):
        assert operation == "events.run"
        return {"launch_token": "t", "preview_id": "p", "acknowledgements": [], "params": params}

    def start(self, preview_id, token, acks, *, requested_by, scheduled):
        self.started.append(scheduled)
        return {"job_id": f"job-{len(self.started)}"}


def test_the_timer_starts_the_first_due_schedule_and_claims_it(config):
    config.events_groups = {"news": object()}
    config.event_schedules = [NEWS, ALL]
    directory = state_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    es.record_attempt(NEWS, directory, datetime.now(timezone.utc))
    service, report = _Service(), {"jobs": [], "status": "idle"}

    assert _start_event_schedule(config, service, None, report)

    assert service.started[0]["schedule"] == "all"  # news ran a moment ago
    atomic_write(
        directory / "settings.json", {"config_path": str(config.config_path), "events": True}
    )
    claim_session(config, service.started[0])
    assert "all" in es.last_attempts(directory)
