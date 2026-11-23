#!/usr/bin/env python3
"""No source file that production is built from may be one the CodeQL configuration leaves out.

    python3 -I .github/scripts/codeql/check_paths_ignore.py      (run in the job that builds the server)

It runs `cargo check` for the library, the binaries and the build script of the server (no
`--tests`), once with the features that are published and once with all of them, takes the files
the compiler read from the dep-info of each artifact (see `rust_sources.py`), and fails when one
matches a pattern of `paths-ignore` in `.github/codeql/codeql-config.yml`. It also fails when a
pattern matches no file of the repository (a dead pattern), when a pattern reaches a file that is
not Rust, and when the compiler's list does not hold the files it must (reading nothing is not
finding nothing). The configuration is read with a reader that accepts the shape that file has and
nothing else; the map job checks that it reads the same as a YAML parser does.

It starts with the cases of `rust_sources_cases.py`, so that the reading is shown to work on small
crates that are compiled for the purpose before it is trusted on the server.

Exit 0 when it holds, 1 with one finding per line.
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rust_sources  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / ".github/codeql/codeql-config.yml"
WORKSPACE = ROOT / "internal"
PACKAGE = "helmly-dashboard-server"
MUST_BE_READ = ("internal/dashboard/server/src/lib.rs", "internal/dashboard/server/src/main.rs")


def parse_config(text):
    """The `name` and the `paths-ignore` list of a configuration that has exactly that shape."""
    name, patterns, in_list = None, [], False
    for number, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            raise ValueError(f"line {number}: a tab")
        if match := re.fullmatch(r"name: ([A-Za-z0-9_-]+)", line):
            name, in_list = match.group(1), False
        elif line == "paths-ignore:":
            in_list = True
        elif in_list and (match := re.fullmatch(r'  - "([^"\\\n]+)"', line)):
            patterns.append(match.group(1))
        else:
            raise ValueError(f"line {number}: this reader does not know it: {line!r}")
    if name is None or not patterns:
        raise ValueError("a name and a list of patterns are expected")
    return {"name": name, "paths-ignore": patterns}


def check(patterns, tracked, production):
    """The findings, given the patterns, the tracked files and the files the compiler read."""
    findings = []
    for pattern in patterns:
        hidden = sorted(f for f in production if rust_sources.matches(pattern, f))
        findings += [f"{pattern} leaves out {f}, which production is built from" for f in hidden]
        reached = [f for f in tracked if rust_sources.matches(pattern, f)]
        if not reached:
            findings.append(f"{pattern} matches no file of the repository")
        findings += [f"{pattern} reaches {f}, which is not Rust" for f in reached if not f.endswith(".rs")]
    findings += [f"the compiler did not read {f}" for f in MUST_BE_READ if f not in production]
    return findings


def cargo_check(*extra):
    done = subprocess.run(
        ["cargo", "check", "--locked", "--package", PACKAGE, "--lib", "--bins", "--message-format=json", *extra],
        cwd=WORKSPACE, capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise ValueError(f"cargo check {' '.join(extra)} failed:\n{done.stderr[-2000:]}")
    return done.stdout


def run_cases():
    """The cases of `rust_sources_cases.py`, on small crates that are really compiled: the reading
    of the dep-info has to find what it must before it is trusted on the server."""
    import unittest

    import rust_sources_cases

    suite = unittest.defaultTestLoader.loadTestsFromModule(rust_sources_cases)
    return unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()


def main():
    if not run_cases():
        print("::error::the cases of the reading of the dep-info fail")
        return 1
    try:
        patterns = parse_config(CONFIG.read_text(encoding="utf-8"))["paths-ignore"]
        production = set()
        for extra in ((), ("--all-features",)):
            production |= rust_sources.production_files(cargo_check(*extra), ROOT, WORKSPACE)
        tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\0")[:-1]
    except (ValueError, OSError, subprocess.SubprocessError) as err:
        print(f"::error::{err}")
        return 1
    findings = check(patterns, tracked, production)
    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        print(f"codeql paths: {len(patterns)} patterns, {len(production)} production source files read by the compiler, none left out")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
