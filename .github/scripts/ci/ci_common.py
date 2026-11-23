"""What the CI test files share: the repository root, the reference copies and the one
enumerator of the files GitHub may run as CI."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[3]
GOLDEN = pathlib.Path(__file__).resolve().parent / "golden"
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SKIPPED_DIRS = {".git", "node_modules", "target", ".next"}


def ci_files() -> "list[pathlib.Path]":
    """Every file GitHub may run as CI: anything with a .yml or .yaml suffix under a
    .github/workflows directory, and every action.yml or action.yaml anywhere in the
    repository, at any depth. One enumerator, used by every test below."""
    found = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if not path.is_file() or SKIPPED_DIRS & set(rel.parts) or GOLDEN in path.parents:
            continue
        in_workflows = ".github/workflows/" in "/" + rel.as_posix() and path.suffix in (".yml", ".yaml")
        if in_workflows or path.name in ("action.yml", "action.yaml"):
            found.append(path)
    return sorted(found)


def workflow_files() -> "list[pathlib.Path]":
    return [p for p in ci_files() if p.name not in ("action.yml", "action.yaml")]


def reference_of(path: pathlib.Path) -> pathlib.Path:
    """golden/<path from the repository root, with / written as __>"""
    return GOLDEN / path.relative_to(ROOT).as_posix().replace("/", "__")


def load_yaml(text: str):
    """Read a workflow or an action with PyYAML, installed from wheels whose hashes are in
    requirements.txt (the `map` job does that before it runs any test). The reader is the one
    `check_pins.py` carries, so the tests and the pin check read a file the same way: PyYAML's
    `SafeLoader` that refuses a key written twice, an anchor or an alias and more than one
    document, and keeps every plain scalar as the text it was written as."""
    import check_pins

    return check_pins.load_yaml(text)


def ci_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


# What Dependabot moves in a workflow, and nothing else: the commit of an action (with the version
# comment that names it) and the digest of an image. Everything else is compared exactly.
_ACTION_PIN = re.compile(r"(\buses:[ \t]+[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@[0-9a-f]{40}(?:[ \t]+#[ \t]*v[0-9][0-9A-Za-z.+-]*)?")
_IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
# A remote action as the workflows must write it: owner/name[/path]@<40 hex>  # vX.Y.Z
REMOTE_USES = re.compile(r"^[ \t]*(?:- )?uses:[ \t]+(?!\./)(?P<ref>\S+)(?P<rest>.*)$", re.M)
FULL_PIN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^[ \t]+#[ \t]*v[0-9][0-9A-Za-z.+-]*[ \t]*$")


def unpin(text: str) -> str:
    """The text with the value of its pins replaced by a placeholder. A pin written any other way
    (a tag, a short hash, no `@`) is left as it is, so it differs from a pinned reference."""
    return _IMAGE_DIGEST.sub("@sha256:<digest>", _ACTION_PIN.sub(r"\1@<sha>", text))


def unpin_ref(uses: str) -> str:
    return re.sub(r"@[0-9a-f]{40}$", "@<sha>", uses)


def references() -> "dict[pathlib.Path, pathlib.Path]":
    """Each file the CI runs from the repository, and the reference copy it must equal."""
    return {path: reference_of(path) for path in ci_files()}
