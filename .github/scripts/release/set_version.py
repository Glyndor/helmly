#!/usr/bin/env python3
"""Write the version of a release tag into the `version` field of a Cargo.toml.

    TAG=dashboard@1.2.3 python3 set_version.py <path-to-Cargo.toml>

The tag arrives through the environment and is checked against the release tag shape before
anything is read or written, so a value with a quote, a slash, a newline or a shell
metacharacter stops here and never reaches a program text. The first `version = "..."` line
of the file is replaced, which is the one of `[package]`.
"""
import os
import pathlib
import re
import sys

TAG_SHAPE = re.compile(r"dashboard@(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)")
VERSION_LINE = re.compile(r'^version = "[^"\n]*"[ \t]*$', re.M)


def version_of(tag: str) -> str:
    match = TAG_SHAPE.fullmatch(tag)
    if not match:
        raise ValueError("the tag is not dashboard@MAJOR.MINOR.PATCH[-prerelease]")
    return match.group(1)


def main(argv: "list[str]") -> int:
    if len(argv) != 2:
        print("usage: TAG=<tag> set_version.py <Cargo.toml>", file=sys.stderr)
        return 2
    try:
        version = version_of(os.environ.get("TAG", ""))
    except ValueError as err:
        print(f"::error::{err}", file=sys.stderr)
        return 1
    path = pathlib.Path(argv[1])
    text = path.read_text(encoding="utf-8")
    if not VERSION_LINE.search(text):
        print(f"::error::{path} has no version line", file=sys.stderr)
        return 1
    path.write_text(VERSION_LINE.sub(f'version = "{version}"', text, count=1), encoding="utf-8")
    print(f"Set {path} to version {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
