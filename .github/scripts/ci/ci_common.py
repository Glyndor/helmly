"""What the CI test files share: the repository root, the reference copies and the one
enumerator of the files GitHub may run as CI."""
import pathlib
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


def ci_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def references() -> "dict[pathlib.Path, pathlib.Path]":
    """Each file the CI runs from the repository, and the reference copy it must equal."""
    return {path: reference_of(path) for path in ci_files()}
