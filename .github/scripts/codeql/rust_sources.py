"""Which source files the compiler read for the production targets of the crates of this repository.

The compiler is the only reader that cannot be wrong about it: `cargo check` of the library, the
binaries and the build script (no `--tests`) leaves, beside each artifact, a dep-info file that
lists every source file `rustc` opened for it, whatever brought the file in (`mod`, `#[path]`,
`cfg_attr(..., path = ...)`, `include!`, `package.build`). A walk of the `mod` declarations that
one writes oneself understands only the forms one thought of, and every form it misses is a file of
production left out of the analysis. Used by `check_paths_ignore.py`, which runs in the job that
builds the server, and by the cases in `rust_sources_cases.py`.

Standard library only, so that it runs where the toolchain is and nothing else.
"""

import json
import re
from pathlib import Path


def to_regex(pattern):
    """A path pattern of a CodeQL configuration as the documentation describes it: `*` is any run
    of characters but `/`, `**` any run including `/` (at the start, at the end or between slashes),
    and `? + [ ] !` are literal. A pattern also covers what is under a directory it names."""
    out, i = [], 0
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
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"(?:/.*)?")


def matches(pattern, path):
    return to_regex(pattern).fullmatch(path) is not None


def parse_dep_info(text):
    """The source files of the first rule of a dep-info file (`out: a.rs b.rs`, the later `a.rs:`
    lines only repeat them). A space in a name is written `\\ `, a line may continue with a
    backslash, and a line that starts with `#` is a comment."""
    joined = re.sub(r"\\\n", " ", text)
    for line in joined.split("\n"):
        if not line or line.startswith("#"):
            continue
        target, separator, rest = line.partition(": ")
        if not separator:
            continue
        files, current, i = [], "", 0
        while i < len(rest):
            if rest[i] == "\\" and i + 1 < len(rest) and rest[i + 1] == " ":
                current += " "
                i += 2
            elif rest[i] == " ":
                if current:
                    files.append(current)
                current = ""
                i += 1
            else:
                current += rest[i]
                i += 1
        if current:
            files.append(current)
        return files
    return []


def dep_info_candidates(artifact):
    """Where the dep-info of an artifact can be: beside it with the same name, or without the `lib`
    that a library's file name starts with (`libx-<hash>.rmeta` has `x-<hash>.d`)."""
    stem = artifact.stem
    names = [stem + ".d"] + ([stem[3:] + ".d"] if stem.startswith("lib") else [])
    return [artifact.with_name(name) for name in names]


def production_files(cargo_json, root, cwd):
    """The source files of the production artifacts (see `built_files`); a test unit in that output
    is an error, because the check that produced it was meant to leave tests out."""
    return built_files(cargo_json, root, cwd, tests=False)


def test_files(cargo_json, root, cwd):
    """The source files of everything that `cargo check --tests` built: the test harnesses of the
    library and the binaries, the integration tests, and the library they link."""
    return built_files(cargo_json, root, cwd, tests=True)


def built_files(cargo_json, root, cwd, tests):
    """The repository-relative paths of the source files that the artifacts of crates under `root`
    were built from, read from the dep-info next to each artifact, in the output of
    `cargo check --message-format=json` (`cwd` is where cargo ran, which is where relative paths in
    a dep-info start). A crate with no dep-info, or an empty one, is an error: reading nothing must
    never read as finding nothing."""
    root, cwd = Path(root).resolve(), Path(cwd).resolve()
    found, units = set(), 0
    for line in cargo_json.splitlines():
        if not line.startswith("{"):
            continue
        message = json.loads(line)
        if message.get("reason") != "compiler-artifact":
            continue
        source = Path(message["target"]["src_path"]).resolve()
        if root not in source.parents:
            continue  # a dependency from a registry
        if message.get("profile", {}).get("test") and not tests:
            raise ValueError(f"{source}: a test unit in a check that was meant to leave tests out")
        dep_infos = [d for f in message["filenames"] for d in dep_info_candidates(Path(f)) if d.is_file()]
        if not dep_infos:
            raise ValueError(f"{source}: no dep-info beside {message['filenames']}")
        unit_files = set()
        for dep_info in dep_infos:
            for name in parse_dep_info(dep_info.read_text(errors="replace")):
                path = (cwd / name).resolve()
                if root in path.parents:
                    unit_files.add(path.relative_to(root).as_posix())
        if not unit_files:
            raise ValueError(f"{source}: its dep-info lists no source file of this repository")
        found |= unit_files
        units += 1
    if not units:
        raise ValueError("no artifact of a crate of this repository in the output of cargo")
    return found
