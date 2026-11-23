"""Decide which suites a change runs, and write the answer for the workflow.

Run by the `decide` job from the base commit of a pull request, so a pull
request cannot edit the map or this script to skip its own checks. It fails
open: anything it cannot read, and any path the map does not know, runs every
suite, because a wrong skip lets a merge through unchecked and a wrong run
costs minutes.

Environment: EVENT, BASE_SHA, HEAD_SHA (pull_request), BEFORE_SHA, AFTER_SHA
(push), GITHUB_OUTPUT. Prints `<suite>=true|false` lines to GITHUB_OUTPUT.
"""
import os
import subprocess
import sys

import ci_map


def changed_paths(event: str, env: "dict[str, str]") -> "list[str] | None":
    if event == "pull_request":
        base, head = env.get("BASE_SHA", ""), env.get("HEAD_SHA", "")
    elif event == "push":
        base, head = env.get("BEFORE_SHA", ""), env.get("AFTER_SHA", "")
        if not base.strip("0"):
            return None
    else:
        return None
    if not base or not head:
        return None
    done = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"],
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        return None
    paths = [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]
    return paths or None


def decide(event: str, env: "dict[str, str]", ci_map_data: dict) -> "tuple[set[str], str]":
    everything = set(ci_map_data["suites"])
    if event == "pull_request" and env.get("BASE_REF") == "main":
        return everything, "a pull request into main runs every suite"
    paths = changed_paths(event, env)
    if paths is None:
        return everything, "no readable diff: running every suite"
    chosen = ci_map.select(paths, ci_map_data)
    if chosen is None:
        return everything, "a changed path is not in the map: running every suite"
    return chosen, f"{len(paths)} changed paths"


def main() -> int:
    try:
        data = ci_map.load()
    except (OSError, ValueError) as err:
        print(f"::warning::{err}; running every suite")
        names = ["server", "ui", "shell"]
        chosen, reason = set(names), "unreadable map"
    else:
        names = sorted(data["suites"])
        chosen, reason = decide(os.environ.get("EVENT", ""), dict(os.environ), data)
    print(f"{reason}; suites: {', '.join(sorted(chosen)) or 'none'}")
    lines = [f"{name}={'true' if name in chosen else 'false'}" for name in names]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
