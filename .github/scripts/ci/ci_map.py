"""The map of which change runs which suite, and the matching rules it uses.

Both the decision made in a pull request (decide.py) and the test that guards
the map (test_ci_map.py) import this file, so the judge and the thing judged
cannot disagree about what a pattern means.
"""
import json
import pathlib
import re

MAP_PATH = pathlib.Path(".github/ci-map.json")


def pattern_to_regex(pattern: str) -> "re.Pattern[str]":
    """Translate a path pattern: `**/` is zero or more directories, `**` any
    characters, `*` any characters but `/`, `?` one character but `/`."""
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches(path: str, patterns: "list[str]") -> bool:
    return any(pattern_to_regex(p).match(path) for p in patterns)


def load(path: pathlib.Path = MAP_PATH) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("common", "suites", "no_suite"):
        if key not in data:
            raise ValueError(f"{path}: missing key {key!r}")
    return data


def select(paths: "list[str]", ci_map: dict) -> "set[str] | None":
    """The suites a set of changed paths runs, or None when a path belongs to
    nothing in the map, which callers read as 'run everything'."""
    suites: "set[str]" = set()
    for path in paths:
        if matches(path, ci_map["common"]):
            suites.update(ci_map["suites"])
            continue
        hit = False
        for name, patterns in ci_map["suites"].items():
            if matches(path, patterns):
                suites.add(name)
                hit = True
        if not hit and not matches(path, ci_map["no_suite"]):
            return None
    return suites
