#!/usr/bin/env python3
"""Check that every pin of the CI says what it claims to say.

    python3 -I check_pins.py [--base <commit>]      (run at the repository root)

The reference copies of the workflows are compared but for the value of a pin, so that a
Dependabot pull request can move one. That leaves the value to be judged here. The references are
read from the loaded YAML (every `uses`, `image` and `container` key, wherever it is), so what
GitHub reads and this script does not cannot go unjudged. For every remote action,
`uses: <owner>/<repo>[/path]@<sha>  # vMAJOR.MINOR.PATCH`:

- the owner is one of ALLOWED_OWNERS;
- the reference is a full 40-hex commit and the comment is a version, exactly `vN.N.N`;
- the commit is exactly the one the tag `vX.Y.Z` of that repository points at (the commit the tag
  peels to, when it is annotated), asked of github.com with `git ls-remote` from outside any
  repository, and a repository that cannot be asked fails the check;
- the action has one pair (commit, version) in the whole repository;
- with `--base`, the version is not lower than the one the base commit pins for that action.
  Lowering an action needs the owner, because an old version is where its advisories are.

An image (`image`, `container`, `docker://`) must be one of ALLOWED_IMAGES, from Docker Hub, with or
without a tag or a digest.

The network is only used by `main`; `problems` takes the function that asks, so the logic is tested
without one. Besides PyYAML the script imports nothing from the tree, so the CI can run the copy of
the base commit on a change that edits it.

Exit 0 when every pin holds, 1 with one finding per line, 2 when nothing could be read.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ALLOWED_OWNERS = ("actions", "github")
ALLOWED_IMAGES = ("postgres", "valkey/valkey")
SKIPPED_DIRS = {".git", "node_modules", "target", ".next"}

USES_LINE = re.compile(r"^[ \t]*(?:- )?uses:[ \t]+(?P<ref>\S+)(?P<rest>.*)$", re.M)
REF = re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)(?P<path>/[A-Za-z0-9_./-]*)?@(?P<sha>.+)$")
VERSION = re.compile(r"^[ \t]+#[ \t]*(?P<version>v[0-9]+\.[0-9]+\.[0-9]+)[ \t]*$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def load_yaml(text: str):
    """PyYAML's SafeLoader, refusing a key written twice in a mapping, an anchor or an alias and a
    second document, with every plain scalar kept as the text it was written as."""
    import yaml

    class StrictLoader(yaml.SafeLoader):
        yaml_implicit_resolvers = {}

        def compose_node(self, parent, index):
            event = self.peek_event()
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise yaml.constructor.ConstructorError(None, None, "an anchor or an alias", event.start_mark)
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            seen = set()
            for key_node, _ in node.value:
                key = self.construct_object(key_node, deep=deep)
                if key in seen:
                    raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
                seen.add(key)
            return super().construct_mapping(node, deep)

    return yaml.load(text, Loader=StrictLoader)  # noqa: S506 - a SafeLoader subclass


def is_ci_path(rel: str) -> bool:
    """A file GitHub may run as CI: a .yml or .yaml under a .github/workflows directory, or an
    action.yml or action.yaml anywhere. The reference copies are not."""
    parts = rel.split("/")
    if SKIPPED_DIRS & set(parts) or parts[:4] == [".github", "scripts", "ci", "golden"]:
        return False
    in_workflows = ".github/workflows/" in "/" + rel and rel.endswith((".yml", ".yaml"))
    return in_workflows or parts[-1] in ("action.yml", "action.yaml")


def ci_files(root: pathlib.Path) -> "list[pathlib.Path]":
    return sorted(p for p in root.rglob("*") if p.is_file() and is_ci_path(p.relative_to(root).as_posix()))


def keys_named(node, names=("uses", "image", "container")):
    """(key, value) for every key of the loaded YAML with one of these names, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in names:
                yield key, value
            yield from keys_named(value, names)
    elif isinstance(node, list):
        for item in node:
            yield from keys_named(item, names)


def image_name(ref: str) -> "tuple[str, str]":
    """(registry host or '', repository path) of an image reference, without tag or digest."""
    ref = ref.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    if ":" in last:
        ref = ref[: len(ref) - len(last)] + last.split(":", 1)[0]
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        return first, rest
    return "", ref


def image_finding(rel: str, ref: str) -> str:
    host, name = image_name(ref)
    if host in ("", "docker.io", "index.docker.io") and name in ALLOWED_IMAGES:
        return ""
    return f"{rel}: the image {ref} is not one of the allowed ones ({', '.join(ALLOWED_IMAGES)}, from Docker Hub)"


def version_tuple(version: str) -> "tuple[int, int, int]":
    major, minor, patch = version[1:].split(".")
    return int(major), int(minor), int(patch)


def pins_of(text: str, rel: str) -> "tuple[list[tuple[str, str, str, str]], list[str]]":
    """The remote pins of one file as (file, owner/repo, sha, version), and the findings about the file."""
    findings = []
    pins = []
    try:
        doc = load_yaml(text)
    except Exception as err:  # noqa: BLE001 - whatever the reader refuses is a finding
        return [], [f"{rel}: cannot be read as strict YAML ({type(err).__name__})"]
    uses, images = [], []
    for key, value in keys_named(doc):
        if key == "uses":
            uses.append(value)
        elif isinstance(value, str):
            images.append(value)
        elif key == "image" or not isinstance(value, dict):
            findings.append(f"{rel}: a `{key}` that is neither a string nor (for a container) a mapping")
    for value in uses:
        if not isinstance(value, str):
            findings.append(f"{rel}: a `uses` that is not a string")
    uses = [u for u in uses if isinstance(u, str)]
    commented = {}
    for match in USES_LINE.finditer(text):
        commented.setdefault(match.group("ref"), []).append(match.group("rest"))
    for value in uses:
        if value.startswith("./"):
            continue
        if value.startswith("docker://"):
            finding = image_finding(rel, value[len("docker://"):])
            if finding:
                findings.append(finding)
            continue
        rests = commented.get(value)
        if not rests:
            findings.append(f"{rel}: `uses: {value}` is not written as a plain `uses: ref  # vN.N.N` line")
            continue
        rest = rests.pop(0)
        ref = REF.match(value)
        if not ref:
            findings.append(f"{rel}: `{value}` is not owner/repo@commit")
            continue
        name = f"{ref.group('owner')}/{ref.group('repo')}"
        sha = ref.group("sha")
        version = VERSION.match(rest)
        if ref.group("owner") not in ALLOWED_OWNERS:
            findings.append(f"{rel}: {name} is not from an allowed owner ({', '.join(ALLOWED_OWNERS)})")
        elif not FULL_SHA.match(sha):
            findings.append(f"{rel}: {name}@{sha} is not a full 40-hex commit")
        elif not version:
            findings.append(f"{rel}: {name}@{sha[:12]} has no `# vMAJOR.MINOR.PATCH` comment naming its version")
        else:
            pins.append((rel, name, sha, version.group("version")))
    for value in images:
        finding = image_finding(rel, value)
        if finding:
            findings.append(finding)
    return pins, findings


def read_tree(root: pathlib.Path) -> "dict[str, str]":
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in ci_files(root)}


def read_base(root: pathlib.Path, base: str) -> "dict[str, str]":
    """The CI files of a commit, read from git (never from the working directory)."""
    names = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", base], cwd=root, check=True, capture_output=True, text=True).stdout
    files = {}
    for rel in filter(None, names.split("\0")):
        if is_ci_path(rel):
            files[rel] = subprocess.run(["git", "show", f"{base}:{rel}"], cwd=root, check=True, capture_output=True, text=True).stdout
    return files


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


def problems(files: "dict[str, str]", ls_remote, base_files: "dict[str, str] | None" = None) -> "list[str]":
    """Every finding about the pins in `files` ({path: text}). `ls_remote(repo_url, *patterns)` returns
    the text of `git ls-remote` and raises when it cannot be asked. `base_files` is the same for the
    base commit: no action may be pinned to a lower version than the base pins it to."""
    findings = []
    seen: "dict[str, set[tuple[str, str]]]" = {}
    for rel, text in sorted(files.items()):
        pins, found = pins_of(text, rel)
        findings += found
        for _, name, sha, version in pins:
            seen.setdefault(name, set()).add((sha, version))
    floor: "dict[str, tuple[int, int, int]]" = {}
    for rel, text in sorted((base_files or {}).items()):
        for _, name, _, version in pins_of(text, rel)[0]:
            floor[name] = max(floor.get(name, (0, 0, 0)), version_tuple(version))
    for name, pairs in sorted(seen.items()):
        if len(pairs) > 1:
            listed = ", ".join(f"{sha[:12]} {version}" for sha, version in sorted(pairs, key=lambda p: p[1]))
            findings.append(f"{name} is pinned to more than one commit or version in this repository: {listed}")
        for sha, version in sorted(pairs):
            if name in floor and version_tuple(version) < floor[name]:
                findings.append(f"{name} goes down to {version} from v{'.'.join(map(str, floor[name]))}, which the base commit pins; lowering an action needs the owner")
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
    return findings


def git_ls_remote(url: str, *patterns: str) -> str:
    """Ask from an empty directory outside any repository, with no configuration, so that a
    `url.<base>.insteadOf` of the repository under judgement cannot rewrite the address."""
    with tempfile.TemporaryDirectory() as empty:
        return subprocess.run(
            ["git", "ls-remote", url, *patterns],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=empty,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "GIT_TERMINAL_PROMPT": "0", "HOME": empty, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout


def main(argv: "list[str]") -> int:
    root = pathlib.Path.cwd()
    base = None
    if argv[:1] == ["--base"] and len(argv) == 2:
        base = argv[1]
    elif argv:
        print("usage: check_pins.py [--base <commit>]", file=sys.stderr)
        return 2
    if not (root / ".github").is_dir():
        print("check_pins: run it at the repository root", file=sys.stderr)
        return 2
    try:
        files = read_tree(root)
        base_files = read_base(root, base) if base else None
    except (OSError, UnicodeDecodeError, subprocess.SubprocessError) as err:
        print(f"check_pins: cannot read the files: {type(err).__name__}", file=sys.stderr)
        return 2
    findings = problems(files, git_ls_remote, base_files)
    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        names = {name for rel, text in files.items() for _, name, _, _ in pins_of(text, rel)[0]}
        print(f"pins: {len(names)} actions, each at the commit of the tag its comment names")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
