"""Helpers to read the workflows the CI tests look at.

The workflows are not parsed here. What GitHub runs is what the file says, and a reader
that skips what it does not recognise is a way through a check, so test_ci_wiring.py
compares the bytes of every file the CI runs with a reference copy in golden/, comments
and blank lines included, and fails on any difference. The helpers below only cut that
text into jobs and scripts for the behaviour tests, and they fail on anything they
cannot find.
"""
import re


def strip_comments(text: str) -> str:
    """Whole-line comments and blank lines removed. Only for property checks that look
    for a word in a job; never for comparing files and never for taking a script."""
    return "\n".join(l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")) + "\n"


def split_jobs(text: str) -> "dict[str, str]":
    jobs_part = text.split("\njobs:\n", 1)[1]
    return {
        m.group(1): m.group(2)
        for m in re.finditer(r"^  (\w[\w-]*):\n(.*?)(?=^  \w[\w-]*:\n|\Z)", jobs_part, re.S | re.M)
    }


def run_block(job_body: str, step_name: str, first_line: "str | None" = None) -> str:
    """The `run: |` script of the step with this name, exactly as written (comments and
    blank lines inside it are part of the script); raises if there is none."""
    lines = job_body.splitlines()
    try:
        start = lines.index(first_line or f"      - name: {step_name}")
    except ValueError:
        raise AssertionError(f"no step named {step_name!r}") from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("      - ")), len(lines))
    step = lines[start:end]
    try:
        at = step.index("        run: |")
    except ValueError:
        raise AssertionError(f"step {step_name!r} has no `run: |` block") from None
    body: "list[str]" = []
    for line in step[at + 1 :]:
        if line.strip() == "":
            body.append("")
        elif line.startswith("          "):
            body.append(line[10:])
        else:
            break
    while body and body[-1] == "":
        body.pop()
    if not body:
        raise AssertionError(f"step {step_name!r} has an empty script")
    return "\n".join(body)
