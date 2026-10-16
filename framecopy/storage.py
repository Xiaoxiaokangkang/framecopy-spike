from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from framecopy.config import Settings


@dataclass(frozen=True)
class JobPaths:
    root: Path
    input: Path
    work: Path
    evidence: Path
    output: Path


def job_paths(settings: Settings, job_id: str, *, create: bool = True) -> JobPaths:
    if not job_id or any(char not in "0123456789abcdef" for char in job_id.lower()):
        raise ValueError("invalid job id")
    root = (settings.data_dir / "jobs" / job_id).resolve()
    jobs_root = (settings.data_dir / "jobs").resolve()
    if jobs_root not in root.parents:
        raise ValueError("unsafe job path")
    paths = JobPaths(root, root / "input", root / "work", root / "evidence", root / "output")
    if create:
        for directory in (paths.input, paths.work, paths.evidence, paths.output):
            directory.mkdir(parents=True, exist_ok=True)
    return paths


def atomic_json(path: Path, payload: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def delete_job_files(settings: Settings, job_id: str) -> None:
    paths = job_paths(settings, job_id, create=False)
    if paths.root.exists():
        shutil.rmtree(paths.root)
