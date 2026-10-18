"""Inspect revision protection and begin a non-destructive observation period."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import _cfg, config_option
from cnequity.file_lock import LockUnavailable
from cnequity.i18n import N_, _
from cnequity.storage.lifecycle import LifecycleError, LifecycleStore
from cnequity.storage.revisions import RevisionConsistencyError


class _LifecycleGroup(click.Group):
    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except (
            LifecycleError,
            RevisionConsistencyError,
            LockUnavailable,
            OSError,
            ValueError,
        ) as exc:
            raise click.ClickException(str(exc)) from exc


def _store(config_path: str) -> LifecycleStore:
    return LifecycleStore(_cfg(config_path).meta_root)


def _emit(value: dict) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False))


@cli.group(cls=_LifecycleGroup)
def storage():
    """Version retention holds and observation periods; physical deletion must be confirmed on the serve web page."""


@storage.command("inspect")
@config_option
@click.option("--keep", type=click.IntRange(min=1), default=5, show_default=True)
def inspect(config_path: str, keep: int):
    """List versions, retention reasons and registered experiments, read-only."""
    _emit(_store(config_path).inspect(keep=keep))


@storage.command("explain")
@config_option
@click.argument("object_id")
def explain(config_path: str, object_id: str):
    """Show why one version is retained."""
    report = _store(config_path).inspect()
    for obj in report["objects"]:
        if obj["object_id"] == object_id:
            _emit(obj)
            return
    reasons = report["resource_holds"].get(object_id)
    if reasons:
        _emit({"object_id": object_id, "holds": reasons})
        return
    raise click.ClickException(f"Unknown retained object: {object_id}")


@storage.command("import")
@config_option
@click.option(
    "--manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def import_references(config_path: str, manifest: Path):
    """Import a reviewed reference list; only adds protection and never releases old holds automatically."""
    value = _store(config_path).import_manifest(json.loads(manifest.read_text(encoding="utf-8")))
    _emit(
        {
            "lake_id": value["lake_id"],
            "held_objects": len(value["holds"]),
            "registered_experiments": len(value["experiments"]),
        }
    )


@storage.command("hold")
@config_option
@click.argument("object_id")
@click.option("--reason", required=True)
def hold(config_path: str, object_id: str, reason: str):
    """Retain a version, experiment, artifact or existing cleanup resource, and cancel its pending-deletion mark."""
    _store(config_path).hold(object_id, reason)
    _emit({"object_id": object_id, "held": True})


@storage.command("plan")
@config_option
@click.option("--keep", type=click.IntRange(min=1), default=5, show_default=True)
@click.option("--phase", type=click.Choice(["mark", "purge"]), default="mark", show_default=True)
def plan(config_path: str, keep: int, phase: str):
    """Check references and save an immutable version cleanup plan; deletes no data."""
    result = _store(config_path).plan(keep=keep, phase=phase)
    summary = {
        k: result[k]
        for k in (
            "plan_id",
            "logical_bytes_selected",
            "candidate_ids",
            "grace_days",
            "purge_available",
            "phase",
        )
    }
    if phase == "purge":
        summary.update({k: result[k] for k in ("purge_ids", "logical_bytes_to_purge")})
    _emit(summary)


@storage.command("apply")
@config_option
@click.argument("plan_id")
@click.option(
    "--phase",
    type=click.Choice(["mark", "purge"]),
    default="mark",
    show_default=True,
    help=N_(
        "mark marks in place; purge is disabled, use the serve storage operations page instead."
    ),
)
@click.option(
    "--maintenance-window",
    is_flag=True,
    help=N_("Compatibility option; cannot bypass the web delete confirmation."),
)
def apply(config_path: str, plan_id: str, phase: str, maintenance_window: bool):
    """Mark after reviewing the plan; the CLI does not allow physical deletion."""
    if phase == "purge":
        raise click.ClickException(
            _(
                "Physical deletion must be reviewed and confirmed on the storage operations page of cne serve; the CLI only allows previewing and marking."
            )
        )
    else:
        _emit(_store(config_path).mark(plan_id))


@storage.command("experiment-create")
@config_option
@click.option("--parent", type=click.Path(path_type=Path), required=True)
@click.option("--case-id", required=True)
def experiment_create(config_path: str, parent: Path, case_id: str):
    """Create an empty experiment directory with its own instance identity, and register it as active."""
    from cnequity.storage.lifecycle.artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).create_experiment(parent, case_id=case_id))


@storage.command("archive")
@config_option
@click.argument("object_id")
@click.option("--destination", type=click.Path(path_type=Path), required=True)
def archive(config_path: str, object_id: str, destination: Path):
    """Copy a registered experiment and verify it file by file; seal the copy, keeping the original directory and its protection."""
    from cnequity.storage.lifecycle.artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).archive(object_id, destination))


@storage.command("artifact-verify")
@config_option
@click.argument("object_id")
def artifact_verify(config_path: str, object_id: str):
    """Verify the full contents of an archived artifact against its sealed manifest."""
    from cnequity.storage.lifecycle.artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).verify(object_id))


@storage.command("resolve")
@config_option
@click.argument("old_path", type=click.Path(path_type=Path))
@click.option(
    "--artifact-id", help=N_("Explicitly select one artifact when several sealed versions exist.")
)
def resolve(config_path: str, old_path: Path, artifact_id: str | None):
    """Verify and resolve the archived location of an old path; does not rewrite reports or create symlinks."""
    from cnequity.storage.lifecycle.artifacts import ArtifactStore

    path = ArtifactStore(_store(config_path)).resolve(old_path, artifact_id=artifact_id)
    _emit({"old_path": str(old_path), "resolved_path": str(path)})


@storage.command("experiment-plan")
@config_option
@click.option("--phase", type=click.Choice(["mark", "purge"]), default="mark", show_default=True)
def experiment_plan(config_path: str, phase: str):
    """Generate a retirement plan for an experiment's original directory that has no references, is not active, and already has a complete archive."""
    from cnequity.storage.lifecycle.experiments import ExperimentRetirement

    _emit(ExperimentRetirement(_store(config_path)).plan(phase=phase))


@storage.command("experiment-apply")
@config_option
@click.argument("plan_id")
@click.option(
    "--maintenance-window",
    is_flag=True,
    help=N_("Compatibility option; cannot bypass the web delete confirmation."),
)
def experiment_apply(config_path: str, plan_id: str, maintenance_window: bool):
    """Mark a redundant original directory in place; the CLI does not allow physical deletion, which must be confirmed on the serve web page."""
    from cnequity.storage.lifecycle.experiments import ExperimentRetirement

    cfg = _cfg(config_path)
    from cnequity.storage.lifecycle import _read

    if len(plan_id) != 64 or any(c not in "0123456789abcdef" for c in plan_id):
        raise click.ClickException("Invalid experiment plan ID")
    plan = _read(LifecycleStore(cfg.meta_root).root / "experiment-plans" / f"{plan_id}.json")
    if plan.get("phase") == "purge":
        raise click.ClickException(
            _(
                "Physical deletion must be reviewed and confirmed on the storage operations page of cne serve."
            )
        )
    _emit(
        ExperimentRetirement(LifecycleStore(cfg.meta_root)).apply(
            plan_id, maintenance=maintenance_window, manifest=cfg.manifest_path
        )
    )


@storage.command("experiment-seal")
@config_option
@click.argument("object_id")
@click.option("--artifact-id", required=True)
def experiment_seal(config_path: str, object_id: str, artifact_id: str):
    """Explicitly end an experiment working directory, and bind its sealed state to the verified archive."""
    from cnequity.storage.lifecycle.artifacts import ArtifactStore

    _emit(ArtifactStore(_store(config_path)).seal_experiment(object_id, artifact_id))
