#!/usr/bin/env python3
"""Hold the shape of the tree: file length and folder size.

    structure.py --exceptions .github/structure.json     (run at the repository root)

Two limits, both on tracked files (`git ls-files`):

- A source file (.rs, .sh, .py) has at most 500 code lines. Blank lines and comment
  lines do not count, so documenting never pushes a file over; only code does.
- A folder holds at most 20 tracked files directly. The folders GitHub and sqlx fix
  by name (`.github/workflows`, and every folder called `.sqlx`) and any `testdata`
  are not counted.

A file or folder over its limit must be listed in the exceptions file with a
ceiling and a written reason. The list can only shrink: an entry whose subject is
back under the limit fails, an entry whose subject grew past its ceiling fails,
and an entry whose ceiling sits more than 20 above the real number fails and
says what to lower it to. So an exception never outlives its cause and never
quietly grows.

Exit 0 when the tree fits, 1 with one finding per line otherwise, 2 when the
input cannot be read.
"""

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import PurePosixPath

MAX_FILE = 500
MAX_FOLDER = 20
SLACK = 20
COMMENT_PREFIXES = {".rs": ("//", "/*", "*"), ".sh": ("#",), ".py": ("#",)}
UNCOUNTED_FOLDERS = {".github/workflows"}
UNCOUNTED_NAMES = {".sqlx", "testdata"}


def tracked() -> list:
	out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
	return [p.decode("utf-8") for p in out.split(b"\0") if p]


def code_lines(path: str) -> int:
	prefixes = COMMENT_PREFIXES[PurePosixPath(path).suffix]
	count = 0
	with open(path, encoding="utf-8", errors="replace") as fh:
		for line in fh:
			text = line.strip()
			if text and not text.startswith(prefixes):
				count += 1
	return count


def folder_sizes(paths: list) -> Counter:
	sizes: Counter = Counter()
	for path in paths:
		folder = str(PurePosixPath(path).parent)
		if folder in UNCOUNTED_FOLDERS or UNCOUNTED_NAMES & set(PurePosixPath(folder).parts):
			continue
		sizes[folder] += 1
	return sizes


def judge(kind: str, subject: str, actual: int, limit: int, exceptions: dict) -> list:
	entry = exceptions.get(subject)
	if entry is None:
		if actual > limit:
			return [f"{kind} {subject} has {actual}, over the limit of {limit}; split it, or list it in the exceptions file with a ceiling and a reason"]
		return []
	problems = []
	if not str(entry.get("reason", "")).strip() or not isinstance(entry.get("ceiling"), int):
		return [f"the exception for {subject} needs an integer ceiling and a written reason"]
	if actual <= limit:
		problems.append(f"the exception for {subject} is stale: it has {actual}, within the limit of {limit}; remove the entry")
	elif actual > entry["ceiling"]:
		problems.append(f"{kind} {subject} has {actual}, over its ceiling of {entry['ceiling']}")
	elif entry["ceiling"] - actual > SLACK:
		problems.append(f"the ceiling of {subject} is {entry['ceiling']} but it has {actual}; lower the ceiling to {actual}")
	return problems


def main(argv: list) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--exceptions", required=True)
	args = parser.parse_args(argv)
	try:
		with open(args.exceptions, encoding="utf-8") as fh:
			exceptions = json.load(fh)
		if not isinstance(exceptions, dict) or set(exceptions) - {"files", "folders"}:
			raise ValueError("the exceptions file must be an object with the keys 'files' and 'folders' and no other")
		file_exc, folder_exc = exceptions.get("files", {}), exceptions.get("folders", {})
		if not isinstance(file_exc, dict) or not isinstance(folder_exc, dict):
			raise ValueError("'files' and 'folders' must be objects")
		paths = tracked()
	except (OSError, ValueError, subprocess.CalledProcessError) as exc:
		print(f"structure: cannot use the input: {exc}", file=sys.stderr)
		return 2

	findings = []
	present = set(paths)
	for path in sorted(paths):
		if PurePosixPath(path).suffix in COMMENT_PREFIXES:
			findings += judge("file", path, code_lines(path), MAX_FILE, file_exc)
	sizes = folder_sizes(paths)
	for folder in sorted(sizes):
		findings += judge("folder", folder, sizes[folder], MAX_FOLDER, folder_exc)
	for name in sorted(set(file_exc) - present):
		findings.append(f"the exception for {name} names a file that is not tracked; remove the entry")
	for name in sorted(set(folder_exc) - set(sizes)):
		findings.append(f"the exception for {name} names a folder with no tracked files; remove the entry")

	for finding in findings:
		print(f"::error::{finding}")
	if not findings:
		print(f"structure: {len(paths)} tracked files fit (files at most {MAX_FILE} code lines, folders at most {MAX_FOLDER} files)")
	return 1 if findings else 0


if __name__ == "__main__":
	sys.exit(main(sys.argv[1:]))
