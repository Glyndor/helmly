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


def load_yaml(text: str):
    """Read a workflow or an action with PyYAML, installed from wheels whose hashes are in
    requirements.txt (the `map` job does that before it runs any test).

    The reader is PyYAML's `SafeLoader` plus three refusals, so that what a test sees is what
    the file says and nothing a reader could resolve in its own way: a key written twice in one
    mapping, an anchor or an alias, and more than one document. Every plain scalar stays the
    text it was written as (no booleans, numbers, nulls or dates), so `on` is the key "on" and
    `10` is the string "10"; the tests compare text."""
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


def ci_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def references() -> "dict[pathlib.Path, pathlib.Path]":
    """Each file the CI runs from the repository, and the reference copy it must equal."""
    return {path: reference_of(path) for path in ci_files()}
