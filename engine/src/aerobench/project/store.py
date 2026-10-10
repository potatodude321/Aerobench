"""Project directory, configuration persistence, geometry blobs and revisions (ADR-0004)."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import atomic_write_bytes, atomic_write_json, sha256_bytes
from ..core.ids import check_id, short_id, utc_now_iso
from ..domain.migrations import migrate
from ..domain.project import SCHEMA_VERSION, ProjectConfig, stage_hashes
from ..geometry.pose import place_in_cfd_frame
from ..geometry.trisurface import TriSurface
from .db import ProjectDB, dec

PROJECT_FILE = "project.json"
DB_FILE = "project.db"
LAYOUT = ("geometry/blobs", "geometry/sources", "meshes", "runs", "jobs", "cache", "exports", "reports", ".autosave")
AUTOSAVE_KEEP = 20


def is_project_dir(path: Path) -> bool:
    return (Path(path) / PROJECT_FILE).exists()


class Project:
    """An open project.

    The engine service and the project worker are separate processes that both read and
    write ``project.json``.  Every read of :attr:`config` picks up changes written by the
    other process (cheap mtime check), and :meth:`update` performs its read-modify-write
    under an inter-process lock on the freshest copy, so neither process can overwrite the
    other's edits with a stale configuration.
    """

    def __init__(self, root: Path, config: ProjectConfig, db: ProjectDB):
        self.root = Path(root)
        self._config = config
        self._mtime_ns: Optional[tuple[int, int]] = self._stat_mtime()
        self.db = db
        self._surface_cache: dict[str, TriSurface] = {}

    # ------------------------------------------------------------------ configuration (multi-process safe)
    def _stat_mtime(self) -> Optional[tuple[int, int]]:
        try:
            st = (self.root / PROJECT_FILE).stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _reload_if_changed(self, force: bool = False) -> None:
        m = self._stat_mtime()
        if m is None or (m == self._mtime_ns and not force):
            return
        try:
            raw = json.loads((self.root / PROJECT_FILE).read_text(encoding="utf-8"))
            self._config = ProjectConfig.model_validate(migrate(raw))
            self._mtime_ns = m
        except Exception:  # noqa: BLE001 - keep the last good configuration; the next write repairs the file
            pass

    @property
    def config(self) -> ProjectConfig:
        self._reload_if_changed()
        return self._config

    @config.setter
    def config(self, value: ProjectConfig) -> None:
        self._config = value

    # ------------------------------------------------------------------ lifecycle
    @classmethod
    def create(cls, root: Path, name: str, config: Optional[ProjectConfig] = None) -> "Project":
        root = Path(root)
        if is_project_dir(root):
            raise AeroBenchError.make("project.exists", ErrorCategory.PROJECT, f"A project already exists at {root}.")
        if root.exists() and any(root.iterdir()):
            raise AeroBenchError.make("project.dir_not_empty", ErrorCategory.PROJECT, f"The folder {root} is not empty.",
                                      action="Choose an empty or new folder for the project.")
        for d in LAYOUT:
            (root / d).mkdir(parents=True, exist_ok=True)
        cfg = config.model_copy(deep=True) if config else ProjectConfig()
        cfg.name = name
        db = ProjectDB(root / DB_FILE)
        db.set_meta("project_id", cfg.project_id)
        from ..domain.project import HASH_SCHEME

        db.set_meta("hash_scheme", HASH_SCHEME)
        db.set_meta("created_at", cfg.created_at)
        p = cls(root, cfg, db)
        p.save(snapshot=False)
        db.log_event("project_created", cfg.project_id, name=name)
        return p

    @classmethod
    def open(cls, root: Path) -> "Project":
        root = Path(root)
        f = root / PROJECT_FILE
        if not f.exists():
            raise AeroBenchError.make("project.not_found", ErrorCategory.PROJECT, f"No project found at {root}.",
                                      action="Select a folder containing project.json.")
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            backup = cls._latest_autosave(root)
            raise AeroBenchError.make("project.corrupt", ErrorCategory.PROJECT, "project.json is corrupt.",
                                      likely_cause="The file was truncated (e.g. power loss) or edited by hand.",
                                      action=f"Restore the latest autosave ({backup.name if backup else 'none available'}).",
                                      details=str(exc), autosave=str(backup) if backup else None)
        v = int(raw.get("schema_version", 0))
        if v < SCHEMA_VERSION:
            shutil.copy2(f, root / f"{PROJECT_FILE}.v{v}.bak")
        cfg = ProjectConfig.model_validate(migrate(raw))
        for d in LAYOUT:
            (root / d).mkdir(parents=True, exist_ok=True)
        db = ProjectDB(root / DB_FILE)
        p = cls(root, cfg, db)
        p.rehash_records()
        if v < SCHEMA_VERSION:
            p.save(snapshot=False)
            db.log_event("project_migrated", cfg.project_id, from_version=v, to_version=SCHEMA_VERSION)
        return p

    def close(self) -> None:
        self.db.close()

    @staticmethod
    def _latest_autosave(root: Path) -> Optional[Path]:
        snaps = sorted((root / ".autosave").glob("project-*.json"))
        return snaps[-1] if snaps else None

    def save(self, snapshot: bool = True) -> None:
        self._config.touch()
        data = self._config.to_json()
        atomic_write_json(self.root / PROJECT_FILE, data)
        self._mtime_ns = self._stat_mtime()
        if snapshot:
            stamp = utc_now_iso().replace(":", "").replace("-", "")
            atomic_write_json(self.root / ".autosave" / f"project-{stamp}.json", data)
            snaps = sorted((self.root / ".autosave").glob("project-*.json"))
            for old in snaps[:-AUTOSAVE_KEEP]:
                old.unlink(missing_ok=True)

    def update(self, fn: Callable[[ProjectConfig], None], snapshot: bool = True) -> ProjectConfig:
        """Apply a mutation atomically to the freshest configuration (validated by
        constructing a new model) under the inter-process project lock."""
        def mutate(cfg: ProjectConfig) -> ProjectConfig:
            new = cfg.model_copy(deep=True)
            fn(new)
            return new

        return self.transform(mutate, snapshot=snapshot)

    def transform(self, fn: Callable[[ProjectConfig], ProjectConfig], snapshot: bool = True) -> ProjectConfig:
        """Replace the configuration by ``fn(fresh_config)`` under the inter-process lock."""
        from ..core.filelock import file_lock

        with file_lock(self.root / ".project.lock"):
            self._reload_if_changed(force=True)  # timestamps can be too coarse to see a just-written change
            new = fn(self._config.model_copy(deep=True))
            self._config = ProjectConfig.model_validate(new.model_dump(mode="json"))
            self.save(snapshot=snapshot)
        return self._config

    # ------------------------------------------------------------------ paths
    def mesh_dir(self, mesh_id: str) -> Path:
        return self.root / "meshes" / check_id(mesh_id, "mesh id")

    def run_dir(self, run_id: str) -> Path:
        return self.root / "runs" / check_id(run_id, "run id")

    def job_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / check_id(job_id, "job id")

    def cache_dir(self, *parts: str) -> Path:
        p = self.root.joinpath("cache", *(check_id(x, "cache key") for x in parts))
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ------------------------------------------------------------------ geometry blobs
    @staticmethod
    def _surface_bytes(surf: TriSurface) -> bytes:
        # an .npz (readable by np.load) written with fast deflate: level 1 is several times
        # quicker than numpy's default on large meshes and the files are barely larger
        import zipfile

        buf = io.BytesIO()
        arrays = {"points": surf.points.astype(np.float64), "faces": surf.faces.astype(np.int64),
                  "regions": surf.regions.astype(np.int32), "names": np.array(json.dumps(surf.region_names))}
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for key, arr in arrays.items():
                with zf.open(f"{key}.npy", "w", force_zip64=True) as fh:
                    np.lib.format.write_array(fh, np.asanyarray(arr), allow_pickle=False)
        return buf.getvalue()

    @staticmethod
    def surface_digest(surf: TriSurface) -> str:
        import hashlib

        h = hashlib.sha256()
        h.update(np.ascontiguousarray(surf.points, dtype="<f8").tobytes())
        h.update(np.ascontiguousarray(surf.faces, dtype="<i8").tobytes())
        h.update(np.ascontiguousarray(surf.regions, dtype="<i4").tobytes())
        h.update(json.dumps(surf.region_names).encode())
        return h.hexdigest()

    def store_surface(self, surf: TriSurface) -> str:
        sha = self.surface_digest(surf)
        p = self.root / "geometry" / "blobs" / f"{sha}.npz"
        if not p.exists():
            atomic_write_bytes(p, self._surface_bytes(surf))
        self._surface_cache[sha] = surf
        return sha

    def load_surface(self, sha: str) -> TriSurface:
        if sha in self._surface_cache:
            return self._surface_cache[sha]
        p = self.root / "geometry" / "blobs" / f"{sha}.npz"
        if not p.exists():
            raise AeroBenchError.make("project.blob_missing", ErrorCategory.PROJECT, f"Geometry blob {sha[:12]}… is missing.",
                                      likely_cause="Files were deleted from the project folder.",
                                      action="Restore the project folder from backup or re-import the geometry.")
        with np.load(p, allow_pickle=False) as z:
            surf = TriSurface(z["points"], z["faces"], z["regions"], json.loads(str(z["names"])))
        if self.surface_digest(surf) != sha:
            raise AeroBenchError.make("project.blob_corrupt", ErrorCategory.PROJECT, f"Geometry blob {sha[:12]}… is corrupt.")
        self._surface_cache[sha] = surf
        return surf

    # ------------------------------------------------------------------ geometry revisions
    def add_geometry_revision(
        self,
        surf: TriSurface,
        kind: str,
        source_filename: Optional[str] = None,
        source_bytes: Optional[bytes] = None,
        parent_id: Optional[str] = None,
        operations: Optional[list[dict]] = None,
        report: Optional[dict] = None,
        label: Optional[str] = None,
        units: Optional[str] = None,
        parameters: Optional[dict] = None,
    ) -> str:
        sha = self.store_surface(surf)
        src_sha = None
        if source_bytes is not None:
            src_sha = sha256_bytes(source_bytes)
            sp = self.root / "geometry" / "sources" / f"{src_sha}{Path(source_filename or '').suffix.lower()}"
            if not sp.exists():
                atomic_write_bytes(sp, source_bytes)
        rid = short_id("geo")
        self.db.insert("geometry_revisions", {
            "id": rid, "parent_id": parent_id, "created_at": utc_now_iso(), "label": label or source_filename or kind,
            "kind": kind, "source_filename": source_filename, "source_sha256": src_sha, "blob_sha256": sha, "units": units,
            "operations_json": operations or [], "report_json": report, "n_faces": surf.n_faces,
            "region_names_json": surf.region_names, "parameters_json": parameters,
        })
        self.db.log_event("geometry_revision", rid, kind=kind, parent=parent_id)
        return rid

    def geometry_revision(self, rid: str) -> dict[str, Any]:
        r = dec(self.db.one("SELECT * FROM geometry_revisions WHERE id = ?", (rid,)))
        if r is None:
            raise AeroBenchError.make("project.geometry_revision_missing", ErrorCategory.PROJECT, f"Unknown geometry revision {rid}.")
        return r

    def geometry_revisions(self) -> list[dict[str, Any]]:
        return [dec(r) for r in self.db.all("SELECT * FROM geometry_revisions ORDER BY created_at")]

    def raw_surface(self, rid: Optional[str] = None) -> TriSurface:
        rid = rid or self.config.geometry.revision_id
        if rid is None:
            raise AeroBenchError.make("geometry.missing", ErrorCategory.GEOMETRY, "No geometry has been imported.")
        return self.load_surface(self.geometry_revision(rid)["blob_sha256"])

    def cfd_surface(self, rid: Optional[str] = None, cfg: Optional[ProjectConfig] = None) -> TriSurface:
        cfg = cfg or self.config
        s, _ = place_in_cfd_frame(self.raw_surface(rid or cfg.geometry.revision_id), cfg.geometry.transform)
        return s

    # ------------------------------------------------------------------ staleness
    def staleness(self) -> dict[str, Any]:
        """How the meshes and runs relate to the current configuration (BUILD_SPEC §2).

        ``state`` per stage: ``none`` (nothing made yet), ``current`` (a finished mesh / run for
        exactly these settings), ``running`` (one for these settings is queued or running),
        ``stopped`` (a run for these settings was stopped; its partial results are kept),
        ``cancelled`` (the latest one for these settings was cancelled by the user; meshing that
        was stopped counts as cancelled), ``failed`` (the latest attempt for these settings
        failed) or ``stale`` (there are meshes /
        runs, but none for the current settings: something was changed). Only ``stale`` means
        "the setup changed"."""
        h = stage_hashes(self.config)
        out: dict[str, Any] = {"hashes": h}
        for stage, table, col, active, stopped, cancelled in (
            ("mesh", "mesh_revisions", "input_hash", ("queued", "starting", "running"), (), ("cancelled", "stopped", "interrupted")),
            ("solve", "runs", "solve_hash", ("queued", "starting", "running", "stopping"), ("stopped", "interrupted"), ("cancelled",)),
        ):
            current = self.db.one(f"SELECT id, status, created_at FROM {table} WHERE {col} = ? AND status = 'completed' "
                                  "ORDER BY created_at DESC LIMIT 1", (h[stage],))
            matching = self.db.one(f"SELECT id, status, created_at FROM {table} WHERE {col} = ? ORDER BY created_at DESC LIMIT 1",
                                   (h[stage],))
            latest = self.db.one(f"SELECT id, {col} AS input_hash, status FROM {table} ORDER BY created_at DESC LIMIT 1")
            if latest is None:
                state = "none"
            elif matching is not None and matching["status"] in active:
                state = "running"
            elif current is not None:
                state = "current"
            elif matching is not None and matching["status"] in stopped:
                state = "stopped"
            elif matching is not None and matching["status"] in cancelled:
                state = "cancelled"
            elif matching is not None:
                state = "failed"
            else:
                state = "stale"
            out[stage] = {"current": current, "matching": matching, "latest": latest, "state": state, "stale": state == "stale"}
        return out

    def rehash_records(self) -> int:
        """Re-key stored mesh/run hashes to the current hashing scheme.

        A stored hash is converted only if it provably matches the record's own frozen
        configuration under the old scheme (so a mesh made by an older case generator, or a
        record whose snapshot cannot be read, stays out of date). Idempotent."""
        from ..domain.project import HASH_SCHEME, v1_mesh_stage_input, v1_solve_stage_input
        from ..core.fs import sha256_json

        if self.db.get_meta("hash_scheme", 1) >= HASH_SCHEME:
            return 0
        changed = 0
        for table, col, old_fn, new_key in (("mesh_revisions", "input_hash", v1_mesh_stage_input, "mesh"),
                                            ("runs", "solve_hash", v1_solve_stage_input, "solve")):
            for row in self.db.all(f"SELECT id, {col} AS h, config_snapshot_json FROM {table}"):
                try:
                    snap = ProjectConfig.from_json(json.loads(row["config_snapshot_json"]))
                except Exception:  # noqa: BLE001 - unreadable snapshot: leave the record as it is
                    continue
                if row["h"] == sha256_json(old_fn(snap)):
                    self.db.update(table, "id", row["id"], {col: stage_hashes(snap)[new_key]})
                    changed += 1
        self.db.set_meta("hash_scheme", HASH_SCHEME)
        self.db.log_event("hashes_rekeyed", None, records=changed, scheme=HASH_SCHEME)
        return changed
