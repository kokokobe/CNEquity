"""Built-in defaults under a user config: ``extends = "builtin"``.

A config written by ``cne config create`` used to be the whole packaged example,
copied once. From then on every release drifted away from it: a step added to a
schedule group never ran until ``cne config upgrade`` patched it in, and a key
left at its template value pinned that value against later releases. Measured on
a long-lived lake config: 154 keys, 23 of them actually changed, 35 keys of the
current template missing.

A config that starts with ``extends = "builtin"`` holds only what differs. The
packaged example, adjusted for this platform exactly as ``cne config create``
adjusts it, is the layer underneath:

* tables merge key by key, recursively (inline tables too);
* any other value — scalars, arrays, arrays of tables such as
  ``[[failover.datasets]]`` — replaces the default whole;
* ``[data].root`` never comes from the defaults: the template's relative root
  describes a repository checkout, not where this config's lake lives.

Schedule groups (``[job.daily.groups.*]``, ``[job.events.groups.*]``) accept,
in any config:

* ``steps_add`` — steps appended to the group's (inherited) ``steps``;
* ``steps_remove`` — steps dropped from them;
* ``enabled = false`` — the group is not scheduled at all.

so swapping one step for another does not freeze the rest of the group: a
step a later release adds to it still runs.
"""

from __future__ import annotations

import copy
import logging
import sys
from functools import lru_cache
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore

from cnequity.config.errors import ConfigError

logger = logging.getLogger(__name__)

#: The only base layer there is today; the key leaves room for a shared file.
BUILTIN = "builtin"
EXTENDS_KEY = "extends"
GROUP_SCOPES = ("daily", "events")
GROUP_EDIT_KEYS = ("steps_add", "steps_remove", "enabled")
#: Where `effective_raw` leaves the steps a config removed on purpose, for the loader.
OPTED_OUT_KEY = "__opted_out_steps__"


class ConfigLayerError(ConfigError):
    """A malformed layering key. The message names keys only, never values, so it is safe to print."""


def is_layered(raw: dict) -> bool:
    """Whether *raw* (a parsed user file) builds on the built-in defaults."""
    return raw.get(EXTENDS_KEY) is not None


@lru_cache(maxsize=4)
def _builtin_text(platform: str) -> str:
    from cnequity.config.bootstrap import render_example_toml

    return render_example_toml(platform=platform)


def builtin_defaults(platform: str | None = None) -> dict:
    """The default layer as data: the platform-adjusted example, minus ``[data].root``."""
    raw = tomllib.loads(_builtin_text(platform or sys.platform))
    raw.get("data", {}).pop("root", None)
    return raw


def merge(base: dict, override: dict) -> dict:
    """*override* on top of *base*: tables merge recursively, everything else replaces."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def effective_raw(raw: dict, *, apply_opt_outs: bool = True) -> dict:
    """The parsed config a loader should read: defaults layered in and group edits applied.

    *apply_opt_outs* False keeps ``steps_remove`` steps and ``enabled = false``
    groups in place, which is how drift tells what the operator opted out of on
    purpose from a step the config was never given.
    """
    value = raw.get(EXTENDS_KEY)
    if value is None:
        out = copy.deepcopy(raw)
    elif value == BUILTIN:
        out = merge(builtin_defaults(), {k: v for k, v in raw.items() if k != EXTENDS_KEY})
    else:
        raise ConfigLayerError(f'extends must be "{BUILTIN}"')
    out.pop(EXTENDS_KEY, None)
    opted_out = _apply_group_edits(out, apply_opt_outs=apply_opt_outs)
    if apply_opt_outs and opted_out:
        out[OPTED_OUT_KEY] = sorted(opted_out)
    return out


def _apply_group_edits(raw: dict, *, apply_opt_outs: bool) -> set[str]:
    """Apply group edits; return the steps the config opted out of on purpose."""
    opted_out: set[str] = set()
    job = raw.get("job")
    if not isinstance(job, dict):
        return opted_out
    for scope in GROUP_SCOPES:
        groups = (job.get(scope) or {}).get("groups")
        if not isinstance(groups, dict):
            continue
        for name in list(groups):
            group = groups[name]
            if not isinstance(group, dict):
                continue
            location = f"job.{scope}.groups.{name}"
            enabled = group.pop("enabled", True)
            if not isinstance(enabled, bool):
                raise ConfigLayerError(f"{location}.enabled must be true or false")
            add = _step_list(group.pop("steps_add", []), f"{location}.steps_add")
            remove = _step_list(group.pop("steps_remove", []), f"{location}.steps_remove")
            if not enabled and apply_opt_outs:
                opted_out.update(group.get("steps", []) or [])
                del groups[name]
                continue
            steps = list(group.get("steps", []) or [])
            if apply_opt_outs:
                absent = [step for step in remove if step not in steps]
                if absent:
                    logger.warning(
                        "%s.steps_remove names steps the group does not run: %s",
                        location,
                        ", ".join(absent),
                    )
                opted_out.update(step for step in remove if step in steps)
                steps = [step for step in steps if step not in remove]
            steps.extend(step for step in add if step not in steps)
            if add or remove:
                group["steps"] = steps
    return opted_out


def _step_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigLayerError(f"{location} must be a list of step names")
    return value
