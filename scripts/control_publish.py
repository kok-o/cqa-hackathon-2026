"""Validate the control export; publish only with explicit --publish before cutoff.

No inference is performed here. Error rows are retained and require --allow-errors.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.cqa.importer import normalize
from backend.cqa.models import DatasetExport
from evaluation.submission import _read, validate_submission

TARGET = "results/prod/results.json"
DEADLINE = datetime(2026, 11, 8, 7, 0, tzinfo=timezone.utc)
COMMIT_MESSAGE = "Submit validated PROD results for SetW"


def before_deadline() -> None:
    if datetime.now(timezone.utc) >= DEADLINE:
        raise ValueError("Publication cutoff passed (2026-11-08 12:00 UTC+5); no new commit or push allowed")


def validate_candidate(dataset_path: Path, input_path: Path, *, allow_errors: bool = False):
    dataset = DatasetExport.model_validate(_read(dataset_path))
    group = dataset.data.dataset_group_id
    if dataset.error.strip():
        raise ValueError("Dataset reports an error")
    if re.search(r"dev|test|synthetic|demo|rehearsal|fixture", group, re.IGNORECASE):
        raise ValueError("DEV, test, or synthetic dataset groups cannot be published")
    conversations = normalize(dataset)
    document = _read(input_path)
    if document.get("team_name") != "SetW" or document.get("model") != {"name": "gpt-6-luna", "mode": "external_api"}:
        raise ValueError("Control metadata must be SetW / gpt-6-luna / external_api")
    report = validate_submission(document, conversations=conversations, split="prod")
    if report["errors"] and not allow_errors:
        raise ValueError("Export contains error rows; --allow-errors is required to publish them unchanged")
    return document, dict(report, group_id=group)


def git(root: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8")
    if check and process.returncode:
        raise ValueError(f"git {' '.join(args[:2])} failed: {process.stderr.strip()}")
    return process.stdout.strip()


def remote_head(root: Path) -> str:
    output = git(root, "ls-remote", "origin", "refs/heads/main")
    if not output or len(output.splitlines()) != 1:
        raise ValueError("origin/main is missing or ambiguous")
    return output.split()[0]


def protect_paths(root: Path, dataset_path: Path, input_path: Path) -> Path:
    target = root / TARGET
    resolved = target.resolve()
    if resolved != target.absolute() or not resolved.is_relative_to(root.resolve()):
        raise ValueError("Publication path must not contain symlinks or escape the repository")
    if resolved in {dataset_path.resolve(), input_path.resolve()}:
        raise ValueError("Dataset and candidate must be separate from the protected publication path")
    return target


def publish(root: Path, document: dict, dataset_path: Path, input_path: Path) -> dict:
    root = root.resolve()
    target = protect_paths(root, dataset_path, input_path)
    payload = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if git(root, "rev-parse", "--show-toplevel").replace("\\", "/").casefold() != root.as_posix().casefold():
        raise ValueError("Expected the repository root")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Publication requires a clean worktree and index; preserve and commit unrelated changes separately")
    git(root, "fetch", "origin", "main")
    remote = remote_head(root)
    if git(root, "rev-parse", "origin/main") != remote:
        raise ValueError("Remote main advanced during preflight; retry after reviewing the updated branch")
    head = git(root, "rev-parse", "HEAD")
    if git(root, "merge-base", head, remote) != remote:
        raise ValueError("Local main does not contain current origin/main")
    # Hash the exact serialized bytes without writing an object or changing files.
    hashed = subprocess.run(["git", "-C", str(root), "hash-object", "--stdin"], input=payload, capture_output=True, check=True)
    blob = hashed.stdout.decode("ascii").strip()
    if head != remote:
        # Only a failed push of this exact single-file submission can be retried.
        if (git(root, "rev-list", "--count", f"{remote}..{head}") != "1"
                or git(root, "show", "-s", "--format=%s", head) != COMMIT_MESSAGE
                or git(root, "diff", "--name-only", remote, head) != TARGET
                or git(root, "rev-parse", f"HEAD:{TARGET}") != blob):
            raise ValueError("Unpublished commits are not an exact retry of this submission; publish them separately first")
    elif target.exists() and git(root, "rev-parse", f"HEAD:{TARGET}") == blob:
        return {"published": True, "already_published": True, "commit": head, "blob": blob}
    else:
        before_deadline()
        backup = root / "data/runtime/control-publish-backups" / f"{head}-{sha256(target.read_bytes()).hexdigest() if target.exists() else 'absent'}.json"
        if target.exists():
            if backup.resolve() != backup.absolute() or not backup.resolve().is_relative_to(root):
                raise ValueError("Backup path must not contain symlinks or escape the repository")
            ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", str(backup)], capture_output=True)
            if ignored.returncode != 0 or git(root, "ls-files", "--", str(backup)):
                raise ValueError("Existing results backup must be in an ignored, untracked location")
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(target.read_bytes())
            elif backup.read_bytes() != target.read_bytes():
                raise ValueError("Existing backup differs from current results; refusing to overwrite either")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        git(root, "add", "--", TARGET)
        if git(root, "diff", "--cached", "--name-only") != TARGET:
            raise ValueError("Index changed during publication; refusing to commit unrelated changes")
        if remote_head(root) != remote:
            raise ValueError("Remote main advanced before commit; staged result retained for review")
        before_deadline()
        git(root, "commit", "-m", COMMIT_MESSAGE, "--", TARGET)
        head = git(root, "rev-parse", "HEAD")
    if remote_head(root) != remote:
        raise ValueError("Remote main advanced before push; submission commit retained for review")
    before_deadline()
    git(root, "push", "origin", "HEAD:main")
    if remote_head(root) != head:
        raise ValueError("Remote SHA verification failed after push")
    git(root, "fetch", "origin", "main")
    if git(root, "rev-parse", "origin/main") != head or git(root, "rev-parse", f"origin/main:{TARGET}") != blob:
        raise ValueError("Remote submission blob verification failed after push")
    return {"published": True, "already_published": False, "commit": head, "blob": blob}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--allow-errors", action="store_true", help="Explicitly preserve and submit visible error rows")
    args = parser.parse_args(argv)
    try:
        protect_paths(ROOT, args.dataset, args.input)
        document, report = validate_candidate(args.dataset, args.input, allow_errors=args.allow_errors)
        report["dry_run"] = not args.publish
        if args.publish:
            report.update(publish(ROOT, document, args.dataset, args.input))
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(f"Control publication error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
