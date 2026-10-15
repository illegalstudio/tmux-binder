#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION_PATTERN = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


class ReleaseError(RuntimeError):
    pass


def git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "--no-optional-locks", *arguments], cwd=root, text=True,
        capture_output=True, timeout=120,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise ReleaseError(result.stderr.strip() or f"git {arguments[0]} failed")
    return result.stdout.strip()


def version_parts(value: str) -> tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ReleaseError(f"Version must look like v1.2.3, without leading zeros (got {value!r})")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class Checkout:
    head: str
    tags: set[str]

    @property
    def latest(self) -> str:
        versions = [tag for tag in self.tags if tag.startswith("v") and VERSION_PATTERN.fullmatch(tag)]
        return max(versions, key=version_parts, default="")

    def validate(self, tag: str) -> None:
        if tag in self.tags:
            raise ReleaseError(f"Tag {tag} already exists locally or on origin")
        if self.latest and version_parts(tag) <= version_parts(self.latest):
            raise ReleaseError(f"Version {tag} must be newer than {self.latest}")


def inspect(root: Path) -> Checkout:
    if git(root, "status", "--porcelain"):
        raise ReleaseError("Working tree is not clean; commit or stash changes first")
    if git(root, "rev-parse", "--abbrev-ref", "HEAD") != "main":
        raise ReleaseError("Release from the main branch")
    head = git(root, "rev-parse", "HEAD")
    refs = dict(
        (ref, oid) for oid, ref in (
            line.split("\t", 1) for line in git(root, "ls-remote", "origin", "refs/heads/main", "refs/tags/v*").splitlines()
        )
    )
    if refs.get("refs/heads/main") != head:
        raise ReleaseError("HEAD must match origin/main; push main or update your checkout before releasing")
    tags = set(git(root, "tag", "--list").splitlines())
    tags.update(ref.removeprefix("refs/tags/") for ref in refs if ref.startswith("refs/tags/") and not ref.endswith("^{}"))
    return Checkout(head, tags)


def release(root: Path, chosen: str, dry_run: bool) -> None:
    checkout = inspect(root)
    latest = checkout.latest
    if latest:
        major, minor, patch = version_parts(latest)
        proposed = f"v{major}.{minor}.{patch + 1}"
        print(f"Latest tag: {latest}")
    else:
        proposed = "v1.0.0"
        print("No existing version tags found.")
    print(f"Proposed next tag: {proposed}")
    if not chosen:
        chosen = proposed if dry_run else input(f"Version to release [{proposed}]: ").strip() or proposed
    tag = "v" + ".".join(str(part) for part in version_parts(chosen))
    checkout.validate(tag)
    print(f"\nWill create and push an annotated tag:\n  tag:    {tag}\n  branch: main\n  commit: {checkout.head}\n  remote: origin")
    if dry_run:
        print("\nDry run: no tag created or pushed.")
        return
    if input("\nProceed? [y/N] ").strip().lower() not in {"y", "yes"}:
        raise ReleaseError("Aborted; no tag created")
    current = inspect(root)
    if current.head != checkout.head:
        raise ReleaseError("HEAD changed during confirmation; start the release again")
    current.validate(tag)
    git(root, "tag", "-a", tag, checkout.head, "-m", f"Release {tag}")
    print(f"Created local tag {tag}.", flush=True)
    try:
        git(root, "push", "--no-follow-tags", "origin", f"refs/tags/{tag}:refs/tags/{tag}")
    except (ReleaseError, subprocess.TimeoutExpired) as error:
        raise ReleaseError(
            f"Tag push did not complete: {error}\nLocal tag {tag} was retained. "
            f"Check git ls-remote --tags origin refs/tags/{tag} before retrying the push."
        ) from error
    print(f"Tagged and pushed {tag}. GitHub Actions will test this commit and publish its release.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create and push a tmux-binder release tag.")
    parser.add_argument("--version", default=os.environ.get("RELEASE_VERSION", ""), help="version to release instead of prompting")
    parser.add_argument("--dry-run", action="store_true", help="check and show the proposed release without writing")
    arguments = parser.parse_args()
    try:
        release(Path(__file__).resolve().parents[1], arguments.version, arguments.dry_run)
    except (ReleaseError, OSError, subprocess.TimeoutExpired) as error:
        print(f"release: {error}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nrelease: Aborted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
