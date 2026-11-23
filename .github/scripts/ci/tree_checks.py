#!/usr/bin/env python3
"""Checks that judge the whole tree and no path can excuse: they run on every pull request.

    python3 tree_checks.py            (run at the repository root)

A check that depends on which files changed is a check a change can avoid by touching other
files; these do not depend on that. Today there is one: no file of the tree carries a release
verification key that has been retired. The retired keys are written in two halves, so this
file does not carry one of them in a piece a search would find, and a file that quotes one is
reported by its path and never by its content.

What is judged is the commit checked out (`HEAD`), read as stored: the blobs of its tree, not
the files of the working directory, which a `.gitattributes` could have made differ from what
was committed (a `working-tree-encoding`, a `filter` or an `ident` rewrites a file on its way
out of the repository). A tracked `.gitattributes` that asks for one of those is a finding, so
the two views cannot drift apart unseen.

Exit 0 when the tree is clean, 1 with one finding per line, 2 when the tree cannot be read.
"""
import re
import subprocess
import sys

# A key that was embedded in the verifiers before the rotation of 2026-07-04. Each entry is
# written as two halves joined at run time.
RETIRED_RELEASE_KEYS = (("APh+kh61dJeT0HzG", "+KQXELzDjK4ccvqY9K+FptOZ3+Y"),)

# Attributes that change the bytes of a file between the repository and the working directory.
REWRITING_ATTRIBUTES = re.compile(r"(?:^|\s)-?(working-tree-encoding|filter|ident)(?:=|\s|$)")


def git(*args: str, data: bytes = b"") -> bytes:
    return subprocess.run(["git", *args], input=data, capture_output=True, check=True).stdout


def committed_blobs() -> "list[tuple[str, str]]":
    """(path, object id) of every blob of the tree of HEAD. Submodules are not blobs."""
    out = git("ls-tree", "-rz", "--full-tree", "HEAD")
    blobs = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, kind, oid = meta.decode("ascii").split(" ")
        if kind == "blob":
            blobs.append((path.decode("utf-8", "surrogateescape"), oid))
    return blobs


def read_blobs(blobs: "list[tuple[str, str]]") -> "dict[str, bytes]":
    """The stored content of each blob, in one `git cat-file --batch`."""
    request = "".join(f"{oid}\n" for _, oid in blobs).encode("ascii")
    out = git("cat-file", "--batch", data=request)
    contents = []
    pos = 0
    for _ in blobs:
        end = out.index(b"\n", pos)
        header = out[pos:end].split(b" ")
        if len(header) != 3 or header[1] != b"blob":
            raise ValueError(f"unexpected answer of git cat-file: {out[pos:end]!r}")
        size = int(header[2])
        contents.append(out[end + 1 : end + 1 + size])
        pos = end + 1 + size + 1
    return {path: data for (path, _), data in zip(blobs, contents)}


def attribute_findings(files: "dict[str, bytes]") -> "list[str]":
    findings = []
    for path, data in files.items():
        if path.rsplit("/", 1)[-1] != ".gitattributes":
            continue
        for number, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            hit = REWRITING_ATTRIBUTES.search(line)
            if hit:
                findings.append(
                    f"{path}:{number}: sets {hit.group(1)}, which makes the files of the working directory differ from the committed ones"
                )
    return findings


def retired_key_findings(files: "dict[str, bytes]") -> "list[str]":
    needles = ["".join(parts).encode("ascii") for parts in RETIRED_RELEASE_KEYS]
    return [
        f"{path}: carries a release verification key that was retired in the rotation of 2026-07-04"
        for path, data in files.items()
        if any(needle in data for needle in needles)
    ]


def main() -> int:
    try:
        blobs = committed_blobs()
        files = read_blobs(blobs)
    except (OSError, ValueError, subprocess.CalledProcessError) as err:
        print(f"tree checks: cannot read the committed tree: {err}", file=sys.stderr)
        return 2
    findings = attribute_findings(files) + retired_key_findings(files)
    for finding in findings:
        print(f"::error::{finding}")
    if not findings:
        print(f"tree checks: {len(files)} committed files carry no retired release key and no attribute that rewrites files")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
