#!/usr/bin/env python3
"""Check that every pin of the CI says what it claims to say.

    python3 check_pins.py            (run at the repository root)

The reference copies of the workflows are compared but for the value of a pin, so that a
Dependabot pull request can move one. That leaves the value to be judged here: for every
`uses: <owner>/<repo>[/path]@<sha>  # vX.Y.Z` of a workflow or a local action,

- the owner is one of ALLOWED_OWNERS;
- the reference is a full 40-hex commit and the comment names a version;
- the commit is exactly the one the tag `vX.Y.Z` of that repository points at (the commit the
  tag peels to, when the tag is annotated), asked of github.com with `git ls-remote`, and a
  repository that cannot be asked fails the check (it does not pass for lack of an answer);
- an action has one pair (commit, version) in the whole repository, so the pin of a job with
  privileges cannot drift from the one every other job uses.

An image written `<repo>@sha256:<digest>` must come from ALLOWED_IMAGES.

The network is only used by `main`; `problems` takes the function that asks, so the logic is tested
without one. The script reads the tree it runs in and imports nothing from it, so the CI can run
the copy of the base commit on a change that edits it.

Exit 0 when every pin holds, 1 with one finding per line, 2 when nothing could be read.
"""
import pathlib
import re
import subprocess
import sys

ALLOWED_OWNERS = ("actions", "github")
ALLOWED_IMAGES = ("postgres", "valkey/valkey")
SKIPPED_DIRS = {".git", "node_modules", "target", ".next"}

USES = re.compile(r"^[ \t]*(?:- )?uses:[ \t]+(?!\./)(?P<ref>\S+)(?P<rest>.*)$", re.M)
REF = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?P<path>/[A-Za-z0-9_./-]*)?@(?P<sha>.+)$")
VERSION = re.compile(r"^[ \t]+#[ \t]*(?P<version>v[0-9][0-9A-Za-z.+-]*)[ \t]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE = re.compile(r"^[ \t]*image:[ \t]+(?P<image>\S+)@sha256:(?P<digest>\S+)[ \t]*$", re.M)


def ci_files(root: pathlib.Path) -> "list[pathlib.Path]":
    """Every file GitHub may run as CI: a .yml or .yaml under a .github/workflows directory, and an
    action.yml or action.yaml anywhere. The reference copies are not read."""
    found = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if not path.is_file() or SKIPPED_DIRS & set(rel.parts) or rel.parts[:5] == (".github", "scripts", "ci", "golden"):
            continue
        in_workflows = ".github/workflows/" in "/" + rel.as_posix() and path.suffix in (".yml", ".yaml")
        if in_workflows or path.name in ("action.yml", "action.yaml"):
            found.append(path)
    return sorted(found)


def pins(root: pathlib.Path) -> "list[tuple[str, str, str, str, str]]":
    """(file, owner/repo, sha, version, finding) for each remote `uses`; the finding is empty when the
    line is well formed."""
    found = []
    for path in ci_files(root):
        rel = path.relative_to(root).as_posix()
        for match in USES.finditer(path.read_text(encoding="utf-8")):
            ref = REF.match(match.group("ref"))
            if not ref:
                found.append((rel, match.group("ref"), "", "", f"{rel}: `{match.group('ref')}` is not owner/repo@commit"))
                continue
            name = f"{ref.group('owner')}/{ref.group('repo')}"
            sha = ref.group("sha")
            version = VERSION.match(match.group("rest"))
            finding = ""
            if ref.group("owner") not in ALLOWED_OWNERS:
                finding = f"{rel}: {name} is not from an allowed owner ({', '.join(ALLOWED_OWNERS)})"
            elif not FULL_SHA.match(sha):
                finding = f"{rel}: {name}@{sha} is not a full 40-hex commit"
            elif not version:
                finding = f"{rel}: {name}@{sha[:12]} has no `# vX.Y.Z` comment naming its version"
            found.append((rel, name, sha, version.group("version") if version else "", finding))
    return found


def tag_commit(output: str, version: str) -> "str | None":
    """The commit a tag points at, from the output of `git ls-remote <url> refs/tags/<v> refs/tags/<v>^{}`:
    the peeled line when there is one, the direct one otherwise."""
    direct = peeled = None
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        if ref == f"refs/tags/{version}":
            direct = sha
        elif ref == f"refs/tags/{version}^{{}}":
            peeled = sha
    return peeled or direct


def problems(root: pathlib.Path, ls_remote) -> "list[str]":
    """Every finding about the pins under `root`. `ls_remote(repo_url, *patterns)` returns the text of
    `git ls-remote` and raises when it cannot be asked."""
    findings = []
    seen: "dict[str, set[tuple[str, str]]]" = {}
    for rel, name, sha, version, finding in pins(root):
        if finding:
            findings.append(finding)
            continue
        seen.setdefault(name, set()).add((sha, version))
    for name, pairs in sorted(seen.items()):
        if len(pairs) > 1:
            listed = ", ".join(f"{sha[:12]} {version}" for sha, version in sorted(pairs, key=lambda p: p[1]))
            findings.append(f"{name} is pinned to more than one commit or version in this repository: {listed}")
        for sha, version in sorted(pairs):
            try:
                answer = ls_remote(f"https://github.com/{name}", f"refs/tags/{version}", f"refs/tags/{version}^{{}}")
            except (OSError, subprocess.SubprocessError) as err:
                findings.append(f"{name} {version}: cannot ask github.com for the tag ({type(err).__name__}); a pin that cannot be checked fails")
                continue
            expected = tag_commit(answer, version)
            if expected is None:
                findings.append(f"{name}: the tag {version} does not exist, so {sha[:12]} names nothing")
            elif expected != sha:
                findings.append(f"{name}: {sha[:12]} is not the commit of {version} (that is {expected[:12]})")
    for path in ci_files(root):
        for match in IMAGE.finditer(path.read_text(encoding="utf-8")):
            image = match.group("image").split(":")[0]
            if image not in ALLOWED_IMAGES:
                findings.append(f"{path.relative_to(root).as_posix()}: the image {image} is not in the allowed list ({', '.join(ALLOWED_IMAGES)})")
    return findings


def git_ls_remote(url: str, *patterns: str) -> str:
    return subprocess.run(
        ["git", "ls-remote", url, *patterns],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
    ).stdout


def main() -> int:
    root = pathlib.Path.cwd()
    if not (root / ".github").is_dir():
        print("check_pins: run it at the repository root", file=sys.stderr)
        return 2
    findings = problems(root, git_ls_remote)
    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        names = {name for _, name, _, _, _ in pins(root)}
        print(f"pins: {len(names)} actions, each at the commit of the tag its comment names")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
