"""Retire verified, unreferenced legacy locations while retaining their archives."""

from __future__ import annotations

import shutil
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cnequity.file_lock import lake_mutation_lock
from cnequity.i18n import _
from cnequity.storage.atomic import replace_with_retry, write_json_atomic
from cnequity.storage.lifecycle import (
    GRACE_DAYS,
    LifecycleError,
    LifecycleStore,
    _read,
    _tree_signature,
    digest,
    reference_fingerprint,
)
from cnequity.storage.lifecycle.artifacts import ArtifactStore, _absolute, _entries
from cnequity.storage.lifecycle.purge import check_idle, unfinished


class ExperimentRetirement:
    def __init__(self, store: LifecycleStore):
        self.store = store
        self.artifacts = ArtifactStore(store)

    def _references(self, registry: dict) -> None:
        if (
            reference_fingerprint(registry["reference_roots"], meta_root=self.store.meta)
            != registry["reference_fingerprint"]
        ):
            raise LifecycleError("References changed; refresh the lifecycle import")

    def _unfinished(self) -> list[Path]:
        return unfinished(self.store)

    def _candidates(self, registry: dict, *, only: set[str] | None = None) -> list[dict]:
        result = []
        for obj in registry["experiments"]:
            oid = obj["object_id"]
            if only is not None and oid not in only:
                continue
            source = _absolute(obj["path"])
            if obj.get("status") == "active" or obj.get("references") or oid in registry["holds"]:
                continue
            if not source.exists():
                continue
            if (
                source.resolve() == self.store.meta.parent
                or source.resolve() in self.store.meta.parents
            ):
                raise LifecycleError("A production lake cannot be an experiment retirement target")
            info = source.stat()
            if [info.st_dev, info.st_ino] != obj["directory_identity"]:
                raise LifecycleError("Experiment location was replaced")
            signature = _tree_signature(source)
            matches = [
                a
                for a in registry.get("artifacts", {}).values()
                if a["source_object_id"] == oid and a["source_signature"] == signature
            ]
            if len(matches) != 1:
                continue
            archive = matches[0]
            _, manifest = self.artifacts.manifest(archive["object_id"])
            self.artifacts.verify(archive["object_id"])
            if _entries(source) != manifest["entries"] or _tree_signature(source) != signature:
                raise LifecycleError("Source changed or differs from its archive")
            result.append(
                {
                    "object_id": oid,
                    "path": str(source),
                    "directory_identity": obj["directory_identity"],
                    "source_signature": signature,
                    "artifact_id": archive["object_id"],
                    "logical_bytes": archive["logical_bytes"],
                }
            )
        return result

    @staticmethod
    def _mature(obj: dict, pending: dict) -> bool:
        item = pending.get(obj["object_id"])
        if not item or item.get("source_signature") != obj["source_signature"]:
            return False
        marked, after = (datetime.fromisoformat(item[k]) for k in ("marked_at", "not_before"))
        if (
            marked.tzinfo is None
            or after.tzinfo is None
            or after - marked < timedelta(days=GRACE_DAYS)
        ):
            raise LifecycleError("Invalid experiment observation period")
        return datetime.now(timezone.utc) >= after

    def plan(self, *, phase: str = "mark", selected_ids: list[str] | None = None) -> dict:
        if phase not in {"mark", "purge"}:
            raise LifecycleError("Unknown experiment lifecycle phase")
        if selected_ids is not None and (
            phase != "purge" or len(selected_ids) != len(set(selected_ids))
        ):
            raise LifecycleError(
                _("A manual selection can only be used for deletion, without duplicates.")
            )
        with lake_mutation_lock(self.store.meta, timeout=30):
            if self._unfinished():
                raise LifecycleError("Resume unfinished experiment purge first")
            registry = self.store.registry(required=True)
            self._references(registry)
            explicit = selected_ids is not None
            objects = self._candidates(registry, only=set(selected_ids) if explicit else None)
            if explicit:
                by_id = {o["object_id"]: o for o in objects}
                picked = []
                for oid in selected_ids:
                    obj = by_id.get(oid)
                    if obj is None:
                        raise LifecycleError(
                            _(
                                "Cannot delete experiment {oid}: it is still in use, referenced "
                                "or retained, or has no verifiable archive yet."
                            ).format(oid=oid)
                        )
                    picked.append(obj)
                objects = picked
            elif phase == "purge":
                objects = [
                    o for o in objects if self._mature(o, registry.get("experiment_pending", {}))
                ]
            plan = {
                "schema_version": 1,
                "lake_id": registry["lake_id"],
                "meta_root": str(self.store.meta),
                "phase": phase,
                "registry_digest": digest(registry),
                "objects": objects,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "logical_bytes_selected": sum(o["logical_bytes"] for o in objects),
            }
            if explicit:
                plan["explicit_selection"] = True
            plan_id = digest(plan)
            path = self.store.root / "experiment-plans" / f"{plan_id}.json"
            _absolute(path)
            write_json_atomic(path, plan, indent=2)
            return {"plan_id": plan_id, **plan}

    def apply(
        self, plan_id: str, *, maintenance: bool = False, manifest: Path | None = None
    ) -> dict:
        if len(plan_id) != 64 or any(c not in "0123456789abcdef" for c in plan_id):
            raise LifecycleError("Invalid experiment plan ID")
        plan = _read(self.store.root / "experiment-plans" / f"{plan_id}.json")
        if digest(plan) != plan_id or plan.get("meta_root") != str(self.store.meta):
            raise LifecycleError("Experiment plan identity changed")
        with lake_mutation_lock(self.store.meta, timeout=30):
            registry = self.store.registry(required=True)
            if plan["lake_id"] != registry["lake_id"]:
                raise LifecycleError("Experiment plan belongs to another lake")
            if plan["phase"] == "purge":
                if not maintenance:
                    raise LifecycleError("Experiment purge requires a confirmed maintenance window")
                return self._purge(plan_id, plan, registry, manifest)
            if plan["phase"] != "mark" or self._unfinished():
                raise LifecycleError("Cannot mark during an unfinished purge")
            self._references(registry)
            if (
                digest(registry) != plan["registry_digest"]
                or self._candidates(registry) != plan["objects"]
            ):
                raise LifecycleError("Experiment plan is stale")
            pending = registry.setdefault("experiment_pending", {})
            now = datetime.now(timezone.utc)
            for obj in plan["objects"]:
                previous = pending.get(obj["object_id"])
                if previous and previous["source_signature"] == obj["source_signature"]:
                    continue
                pending[obj["object_id"]] = {
                    "marked_at": now.isoformat(),
                    "not_before": (now + timedelta(days=GRACE_DAYS)).isoformat(),
                    "source_signature": obj["source_signature"],
                    "artifact_id": obj["artifact_id"],
                }
            self.store._save(registry)
            return {
                "marked": len(plan["objects"]),
                "logical_bytes_selected": plan["logical_bytes_selected"],
                "logical_bytes_deleted": 0,
                "pending": pending,
            }

    def _remaining(self, target: Path, obj: dict, *, partial: bool) -> None:
        _absolute(target)
        info = target.stat()
        if [info.st_dev, info.st_ino] != obj["directory_identity"]:
            raise LifecycleError("Experiment directory identity changed")
        _, manifest = self.artifacts.manifest(obj["artifact_id"])
        actual, expected = _entries(target), manifest["entries"]
        if partial:
            valid = set(actual["directories"]) <= set(expected["directories"]) and all(
                expected["files"].get(p) == info for p, info in actual["files"].items()
            )
        else:
            valid = actual == expected and _tree_signature(target) == obj["source_signature"]
        if not valid:
            raise LifecycleError("Experiment contents changed; refusing deletion")

    def _purge(self, plan_id: str, plan: dict, registry: dict, manifest: Path | None) -> dict:
        check_idle(manifest or self.store.meta / "manifest.db")
        event = self.store.root / "experiment-purges" / f"{plan_id}.json"
        _absolute(event)
        journal = _read(event) if event.exists() else None
        if journal is not None:
            if (
                journal.get("plan_id") != plan_id
                or journal.get("lake_id") != registry["lake_id"]
                or set(journal.get("items", {})) != {o["object_id"] for o in plan["objects"]}
            ):
                raise LifecycleError("Invalid experiment purge journal")
            if journal.get("status") == "complete":
                return journal
        if any(p != event for p in self._unfinished()):
            raise LifecycleError("Another experiment purge is unfinished")
        self._references(registry)
        if digest(registry) != plan["registry_digest"]:
            raise LifecycleError("Experiment protection changed since plan")
        journal = journal or {
            "schema_version": 1,
            "plan_id": plan_id,
            "lake_id": registry["lake_id"],
            "status": "running",
            "items": {o["object_id"]: "pending" for o in plan["objects"]},
            "filesystem_free_bytes_before": shutil.disk_usage(self.store.meta).free,
        }
        with ExitStack() as locks:
            for obj in plan["objects"]:
                if not plan.get("explicit_selection") and not self._mature(
                    obj, registry.get("experiment_pending", {})
                ):
                    raise LifecycleError("Experiment observation period has not elapsed")
                self.artifacts.verify(obj["artifact_id"])
                source = _absolute(obj["path"])
                target = _absolute(
                    source.parent / f".cne-retire-{plan_id}-{digest(obj['object_id'])[:16]}"
                )
                state = journal["items"][obj["object_id"]]
                if state not in {"pending", "moving", "deleting", "deleted"}:
                    raise LifecycleError("Invalid experiment deletion state")
                if source.exists() and (target.exists() or state in {"deleting", "deleted"}):
                    raise LifecycleError(
                        "Original experiment path was reused or has a conflicting target"
                    )
                if source.exists():
                    if (source / "meta").is_dir():
                        locks.enter_context(lake_mutation_lock(source / "meta", blocking=False))
                    check_idle(source / "meta/manifest.db")
                    self._remaining(source, obj, partial=False)
                elif target.exists():
                    if state not in {"moving", "deleting"}:
                        raise LifecycleError("Unexpected experiment quarantine")
                    self._remaining(target, obj, partial=True)
                elif state not in {"deleting", "deleted"}:
                    raise LifecycleError("Experiment is unexpectedly missing")
            # All archives and all remaining sources pass before any deletion.
            write_json_atomic(event, journal, indent=2)
            try:
                for obj in plan["objects"]:
                    oid = obj["object_id"]
                    source = Path(obj["path"])
                    target = source.parent / f".cne-retire-{plan_id}-{digest(oid)[:16]}"
                    if source.exists():
                        journal["items"][oid] = "moving"
                        write_json_atomic(event, journal, indent=2)
                        replace_with_retry(source, target)
                    if target.exists():
                        journal["items"][oid] = "deleting"
                        write_json_atomic(event, journal, indent=2)
                        shutil.rmtree(target)
                    journal["items"][oid] = "deleted"
                    write_json_atomic(event, journal, indent=2)
            except Exception as exc:
                journal.update(status="error", error=str(exc), partial_deletion_possible=True)
                write_json_atomic(event, journal, indent=2)
                raise
        journal.update(
            status="complete",
            logical_bytes_deleted=plan["logical_bytes_selected"],
            filesystem_free_bytes_after=shutil.disk_usage(self.store.meta).free,
        )
        journal.pop("error", None)
        journal.pop("partial_deletion_possible", None)
        write_json_atomic(event, journal, indent=2)
        return journal
