"""Generated case container: dictionaries → expert overrides → text → disk + manifest."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ...core.fs import atomic_write_text, sha256_text
from ...domain.expert import ExpertConfig
from ..ast import Dict, from_json
from ..parser import parse
from ..writer import write_foam_file


@dataclass
class GeneratedFile:
    path: str  # case-relative, '/' separated
    body: Optional[Dict] = None
    object_name: str = ""
    cls: str = "dictionary"
    location: Optional[str] = None
    raw_text: Optional[str] = None  # verbatim content (file overrides)
    note: str = ""

    def render(self) -> str:
        if self.raw_text is not None:
            return self.raw_text
        assert self.body is not None
        return write_foam_file(self.body, self.object_name or Path(self.path).name, cls=self.cls, location=self.location)


@dataclass
class OverrideReport:
    applied: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    replaced_files: list[str] = field(default_factory=list)


@dataclass
class GeneratedCase:
    stage: str  # "mesh" | "solve"
    files: dict[str, GeneratedFile] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    overrides: OverrideReport = field(default_factory=OverrideReport)

    def add(self, path: str, body: Dict, cls: str = "dictionary", object_name: str | None = None, location: str | None = None) -> None:
        self.files[path] = GeneratedFile(path, body, object_name or Path(path).name, cls, location)

    def apply_expert(self, expert: ExpertConfig) -> OverrideReport:
        rep = OverrideReport()
        for p in expert.patches:
            if p.stage != self.stage:
                continue
            gf = self.files.get(p.file)
            tag = f"{p.file}:{p.key_path} ({p.op})"
            if gf is None or gf.body is None:
                rep.conflicts.append(f"{tag}: file is not generated in the {self.stage} stage")
                continue
            try:
                if p.op == "delete":
                    gf.body.delete_path(p.key_path)
                else:
                    gf.body.set_path(p.key_path, from_json(p.value))
                rep.applied.append(tag)
            except KeyError as exc:
                rep.conflicts.append(f"{tag}: {exc}")
        for fo in expert.file_overrides:
            if fo.stage != self.stage:
                continue
            gf = self.files.get(fo.file)
            if gf is None:
                gf = GeneratedFile(fo.file)
                self.files[fo.file] = gf
            gf.raw_text = fo.content
            gf.note = "replaced by expert file override"
            rep.replaced_files.append(fo.file)
        self.overrides = rep
        return rep

    def render_all(self) -> dict[str, str]:
        return {p: f.render() for p, f in sorted(self.files.items())}

    def write(self, case_dir: Path) -> dict[str, str]:
        """Write all files; returns {path: sha256}."""
        manifest = {}
        for rel, text in self.render_all().items():
            atomic_write_text(Path(case_dir) / rel, text)
            manifest[rel] = sha256_text(text)
        return manifest


def diff_text(old: str, new: str, path: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))


def parse_extra_function_object(name: str, text: str) -> Dict:
    """Validate and parse an expert-supplied function-object body."""
    from ...core.errors import AeroBenchError, ErrorCategory

    try:
        d = parse(text)
    except Exception as exc:  # noqa: BLE001
        raise AeroBenchError.make("expert.function_object_parse", ErrorCategory.CONFIGURATION,
                                  f"Custom function object '{name}' could not be parsed.", details=str(exc))
    if "type" not in d:
        raise AeroBenchError.make("expert.function_object_type", ErrorCategory.CONFIGURATION,
                                  f"Custom function object '{name}' has no 'type' entry.")
    return d
