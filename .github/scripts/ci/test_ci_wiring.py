"""Tests of the CI workflows' wiring.

Run by the `CI map` job on every change. Every workflow and every composite action is
compared, whole and without comments, with the reference copy under golden/, and any
difference fails: a duplicated key, a step with another indentation, a continuation
line, a crossed command or condition, a plan fixed to false, a job nobody listed. The
file is not parsed, so nothing it says can escape the comparison, and the reference is
what a reviewer reads when the workflow changes. The tests after that run the scripts
the workflow carries (the guard of each suite job and the gate) and state the
properties the references must keep.

    python3 .github/scripts/ci/test_ci_wiring.py
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ci_map  # noqa: E402
from wiring import run_block, split_jobs, strip_comments  # noqa: E402

from ci_common import GOLDEN, ROOT, WORKFLOW, ci_files, ci_text, load_yaml, reference_of, references, workflow_files  # noqa: E402

SUITE_JOBS = {
    "fmt": "server",
    "urls": "server",
    "clippy": "server",
    "test": "server",
    "typecheck": "ui",
    "unit": "ui",
    "e2e": "ui",
    "shellcheck": "shell",
}
ALWAYS_JOBS = ("map", "tree", "audit", "audit-bun")
ALL_JOBS = set(SUITE_JOBS) | set(ALWAYS_JOBS) | {"decide", "gate"}
GUARD_STEP = "Check the plan and what this job needs"
GATE_STEP = "Every suite passed, or the plan left it out"


class WholeFilesAreAsReviewed(unittest.TestCase):
    def test_every_file_the_ci_runs_is_byte_for_byte_its_reference(self):
        for actual, reference in references().items():
            self.assertTrue(reference.exists(), f"{actual.relative_to(ROOT)} has no reference copy")
            self.assertEqual(
                actual.read_bytes(),
                reference.read_bytes(),
                f"{actual.relative_to(ROOT)} differs from {reference.relative_to(ROOT)}",
            )

    def test_the_references_are_exactly_the_files(self):
        found = {p for p in GOLDEN.rglob("*") if p.is_file()}
        self.assertEqual(found, set(references().values()), "a reference with no file, or a file with none")

    def test_the_enumerator_sees_the_files_that_are_there(self):
        names = {p.relative_to(ROOT).as_posix() for p in ci_files()}
        for expected in (".github/workflows/ci.yml", ".github/workflows/audit.yml", ".github/actions/setup-rust/action.yml"):
            self.assertIn(expected, names)
        self.assertGreaterEqual(len(names), 8)

    def test_the_references_hold_no_tab_and_no_job_id_twice(self):
        for reference in references().values():
            text = reference.read_text(encoding="utf-8")
            self.assertNotIn("\t", text, reference.name)
            if "\njobs:\n" in text:
                heads = re.findall(r"^  ([\w-]+):$", text.split("\njobs:\n", 1)[1], re.M)
                self.assertEqual(len(heads), len(set(heads)), f"{reference.name}: a job id twice")


class ExactShape(unittest.TestCase):
    def setUp(self):
        self.text = ci_text()
        self.jobs = split_jobs(self.text)

    def test_the_jobs_are_exactly_the_expected_ones(self):
        self.assertEqual(set(self.jobs), ALL_JOBS)

    def test_no_job_calls_a_reusable_workflow(self):
        for name, body in self.jobs.items():
            self.assertNotRegex(body.split("    steps:", 1)[0], r"^    uses:", f"{name} calls a workflow")
        self.assertNotRegex(self.text, r"uses:\s*\S*\.github/workflows/")

    def test_a_suite_job_gets_its_own_suite_plan_and_nothing_fixed(self):
        for name, suite in SUITE_JOBS.items():
            env = self.jobs[name].split("    steps:", 1)[0]
            self.assertIn(f"      PLAN: ${{{{ needs.decide.outputs.{suite} }}}}\n", env, name)
            self.assertEqual(len(re.findall(r"PLAN:", env)), 1, name)

    def test_the_map_suites_are_the_ones_the_jobs_serve(self):
        data = ci_map.load(ROOT / ".github/ci-map.json")
        self.assertEqual(set(data["suites"]), set(SUITE_JOBS.values()))
        decided = set(re.findall(r"^      (\w+): \$\{\{ steps\.decide\.outputs\.\1 \}\}", self.text, re.M))
        self.assertEqual(decided, set(data["suites"]))

    def test_the_gate_waits_for_every_other_job_and_knows_each_suite(self):
        needs = set(re.search(r"needs: \[([^\]]*)\]", self.jobs["gate"]).group(1).replace(" ", "").split(","))
        self.assertEqual(needs, ALL_JOBS - {"gate"})
        suites = json.loads(re.search(r"SUITES: '(\{.*\})'", self.jobs["gate"]).group(1))
        self.assertEqual(suites, SUITE_JOBS)

    def test_the_audits_and_the_map_have_no_plan_and_no_condition(self):
        for name in ALWAYS_JOBS:
            head = self.jobs[name].split("    steps:", 1)[0]
            self.assertNotRegex(head, r"^    (if|needs):", name)
            self.assertNotIn("PLAN", self.jobs[name], name)

    def test_every_suite_job_starts_even_when_the_plan_failed(self):
        for name in SUITE_JOBS:
            self.assertIn("    if: ${{ !cancelled() }}\n", self.jobs[name].split("    steps:", 1)[0], name)

    def test_no_condition_runs_only_on_an_explicit_yes(self):
        for name in SUITE_JOBS:
            self.assertNotIn("== 'true'", self.jobs[name], name)

    def test_every_test_of_the_ci_scripts_is_run_by_the_map_job(self):
        tests = sorted((ROOT / ".github/scripts").rglob("test_*.py"))
        self.assertTrue(tests)
        for test in tests:
            self.assertIn(f'"$py" -I {test.relative_to(ROOT).as_posix()}\n', self.jobs["map"], test.name)

    def test_the_tests_run_with_the_python_of_a_venv_built_from_the_hashed_requirements_before_any_test(self):
        body = self.jobs["map"]
        install = body.index('python3 -m venv "$RUNNER_TEMP/venv"')
        first = body.index('"$py" -I ')
        self.assertLess(install, first)
        self.assertIn('"$RUNNER_TEMP/venv/bin/pip" install --quiet --only-binary=:all: --require-hashes -r .github/scripts/ci/requirements.txt', body)
        self.assertIn('py="$RUNNER_TEMP/venv/bin/python"', body)
        self.assertNotIn("secrets.", body)
        self.assertNotIn("python3 .github/scripts/ci/test_", body)
        requirements = (ROOT / ".github/scripts/ci/requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"^PyYAML==\d+\.\d+\.\d+ \\\n(    --hash=sha256:[0-9a-f]{64}( \\)?\n)+$")
        self.assertEqual(requirements.count("==") , 1, "a dependency of the YAML reader would need its own hashes")

    def test_the_guard_is_the_same_text_in_every_suite_job(self):
        guards = {run_block(self.jobs[name], GUARD_STEP) for name in SUITE_JOBS}
        self.assertEqual(len(guards), 1)

    def test_the_security_audit_is_downloaded_and_checked_not_built_or_restored(self):
        for text in (self.jobs["audit"], strip_comments((ROOT / ".github/workflows/audit.yml").read_text(encoding="utf-8"))):
            self.assertIn("install-cargo-audit.sh", text)
            self.assertNotIn("cargo install", text)
            self.assertNotIn("cargo-tool", text)
            self.assertNotIn("actions/cache", text)
        script = (ROOT / ".github/scripts/install-cargo-audit.sh").read_text(encoding="utf-8")
        self.assertIn("sha256sum --check --strict", script)
        self.assertRegex(script, r'CARGO_AUDIT_SHA256="[0-9a-f]{64}"')

    def test_the_cache_is_used_only_for_tools_that_are_not_security_tools(self):
        action = (ROOT / ".github/actions/cargo-tool/action.yml").read_text(encoding="utf-8")
        self.assertIn("actions/cache", action)
        for text in ci_text().split("uses: ./.github/actions/cargo-tool")[1:]:
            self.assertRegex(text.lstrip(), r"^with:\n\s+crate: sqlx-cli\n")


class NoComment(unittest.TestCase):
    def test_no_comment_points_outside_the_repository(self):
        forbidden = re.compile(r"\b(session|reviewer|informe|brief|audit round)\b", re.I)
        for path in (ROOT / ".github").rglob("*"):
            if path.suffix not in (".yml", ".py", ".sh", ".json") or path.name == "test_ci_wiring.py":
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#"):
                    self.assertIsNone(forbidden.search(line), f"{path.relative_to(ROOT)}:{n}")


def guard_script() -> str:
    return run_block(split_jobs(ci_text())["fmt"], GUARD_STEP)


class Guard(unittest.TestCase):
    """The first step of every suite job, run for real: it must fail whenever the plan or
    a job before it is not good, because a job that is skipped instead reads as passed."""

    def run_guard(self, decide="success", plan="true", others=None):
        needs = {"decide": {"result": decide, "outputs": {}}}
        needs.update({k: {"result": v, "outputs": {}} for k, v in (others or {}).items()})
        done = subprocess.run(
            ["bash", "-c", guard_script()],
            env={**os.environ, "NEEDS": json.dumps(needs), "PLAN": plan},
            capture_output=True,
            text=True,
        )
        return done.returncode, done.stdout

    def test_a_good_plan_and_good_requirements_pass_for_yes_and_for_no(self):
        for plan in ("true", "false"):
            self.assertEqual(self.run_guard(plan=plan, others={"fmt": "success"})[0], 0, plan)

    def test_a_failed_plan_fails_the_job(self):
        for result in ("failure", "cancelled", "skipped"):
            self.assertEqual(self.run_guard(decide=result)[0], 1, result)

    def test_a_missing_or_odd_plan_fails_the_job_instead_of_skipping_it(self):
        for plan in ("", "maybe", "True", "1"):
            code, out = self.run_guard(plan=plan)
            self.assertEqual(code, 1, repr(plan))
            self.assertIn("neither true nor false", out)

    def test_a_job_before_it_that_did_not_succeed_fails_the_job(self):
        for result in ("failure", "skipped", "cancelled"):
            code, out = self.run_guard(others={"fmt": result})
            self.assertEqual(code, 1, result)
            self.assertIn(f"fmt: {result}", out)


GATE_NAME = (
    "${{ github.event_name == 'pull_request' && (github.event.pull_request.base.ref == 'main' && 'CI gate (main)' "
    "|| 'CI gate') || format('CI gate ({0})', github.event_name) }}"
)
# The only names allowed to contain an expression, and what each stands for. A name with
# `${{` that is not here fails the test: what the test cannot resolve, it does not accept.
MATRIX_NAMES = {
    "Security audit (${{ matrix.ref }})": ["Security audit (develop)", "Security audit (main)"],
    "Security audit (bun) (${{ matrix.ref }})": ["Security audit (bun) (develop)", "Security audit (bun) (main)"],
    "Analyze (${{ matrix.language }})": [
        "Analyze (actions)",
        "Analyze (javascript-typescript)",
        "Analyze (rust)",
    ],
    "Build ${{ matrix.arch }}": ["Build x86_64", "Build arm64"],
}


def gate_name(event: str, base: str) -> str:
    """GATE_NAME, evaluated."""
    if event == "pull_request":
        return "CI gate (main)" if base == "main" else "CI gate"
    return f"CI gate ({event})"


def triggers_of(text: str) -> "list[tuple[str, str]]":
    """(event, base) for every trigger of a workflow: a pull_request trigger once per
    base branch it names, any other event once. Read strictly from the `on:` block."""
    top = strip_comments(text).split("\njobs:\n", 1)[0]
    m = re.search(r"^on:\n((?:  .*\n)+)", top + "\n", re.M)
    if not m:
        raise AssertionError("no `on:` block")
    block = m.group(1)
    events = re.findall(r"^  ([a-z_]+):", block, re.M)
    if not events:
        raise AssertionError("an `on:` block without events")
    out: "list[tuple[str, str]]" = []
    for event in events:
        if event == "pull_request":
            m2 = re.search(r"^  pull_request:\n(?:    .*\n)*?    branches: \[([^\]]*)\]", block, re.M)
            bases = [b.strip().strip("'\"") for b in m2.group(1).split(",")] if m2 else ["*"]
            out += [(event, base) for base in bases]
        else:
            out.append((event, "*"))
    return out


def produced_names(files=None) -> "dict[str, set[tuple[str, str, str]]]":
    """Check name -> the (workflow, event, base) triples that produce it. The name of every
    job is registered for every trigger of its workflow, whatever `if` the job has. Names are
    indexed without regard to case, because that is how the expressions that build them
    compare, and a producer called `ci GATE` is a competitor of `CI gate`. `files` is a list
    of (file name, text) for tests; by default it is every workflow of the repository."""
    if files is None:
        files = [(path.name, path.read_text(encoding="utf-8")) for path in workflow_files()]
    names: "dict[str, set[tuple[str, str, str]]]" = {}
    for filename, text in files:
        for job_id, body in split_jobs(strip_comments(text)).items():
            m = re.search(r"^    name: (.+)$", body, re.M)
            name = m.group(1).strip() if m else job_id
            for event, base in triggers_of(text):
                if name == GATE_NAME:
                    produced = [gate_name(event, base)]
                elif name in MATRIX_NAMES:
                    produced = MATRIX_NAMES[name]
                elif "${{" in name:
                    raise AssertionError(f"{filename}: job {job_id} has a name this test cannot resolve: {name}")
                else:
                    produced = [name]
                for one in produced:
                    names.setdefault(one.lower(), set()).add((filename, event, base))
    return names


class RequiredChecks(unittest.TestCase):
    def listed(self):
        return json.loads((ROOT / ".github/required-checks.json").read_text(encoding="utf-8"))

    def test_every_name_in_the_list_is_the_name_of_a_job(self):
        names = produced_names()
        listed = self.listed()
        wanted = set(listed["reported"]) | {n for group in listed["required"].values() for n in group}
        self.assertEqual(sorted(n for n in wanted if n.lower() not in names), [], "listed checks no job produces")

    def test_each_ruleset_requires_its_own_gate(self):
        required = self.listed()["required"]
        self.assertIn("CI gate", required["develop"])
        self.assertNotIn("CI gate (main)", required["develop"])
        self.assertIn("CI gate (main)", required["main"])
        self.assertNotIn("CI gate", required["main"])

    def test_a_required_check_comes_from_one_workflow_one_event_and_one_base(self):
        names = produced_names()
        for group in self.listed()["required"].values():
            for name in group:
                self.assertEqual(len(names.get(name.lower(), ())), 1, f"{name}: {sorted(names.get(name.lower(), ()))}")

    def test_the_gate_has_a_different_name_for_each_event_and_base(self):
        names = produced_names()
        self.assertEqual(names["ci gate"], {("ci.yml", "pull_request", "develop")})
        self.assertEqual(names["ci gate (main)"], {("ci.yml", "pull_request", "main")})
        self.assertEqual(names["ci gate (push)"], {("ci.yml", "push", "*")})

    def test_a_competitor_with_the_name_in_another_case_is_a_second_producer(self):
        other = "name: o\non:\n  pull_request:\n    branches: [develop]\njobs:\n  x:\n    name: ci GATE\n    runs-on: x\n    steps:\n      - run: echo\n"
        files = [(path.name, path.read_text(encoding="utf-8")) for path in workflow_files()] + [("other.yml", other)]
        names = produced_names(files)
        self.assertEqual(len(names["ci gate"]), 2)
        self.assertEqual(len(names["ci gate (main)"]), 1)

    def test_a_name_with_an_expression_this_test_cannot_resolve_is_refused(self):
        self.assertTrue(all("${{" in k for k in MATRIX_NAMES))
        self.assertNotIn(GATE_NAME, MATRIX_NAMES)

    def test_a_pull_request_into_main_runs_every_suite_with_no_plan(self):
        step = run_block(split_jobs(ci_text())["decide"], "Decide", "      - id: decide")
        self.assertIn('[ "$EVENT" = pull_request ] && [ "$BASE_REF" = main ]', step)
        first = step.split("elif", 1)[0]
        self.assertIn("server=true ui=true shell=true", first)
        self.assertNotIn("decide.py", first)

    def test_a_pull_request_runs_only_when_opened_pushed_or_reopened(self):
        # Not on `edited`: the gate's name depends on the base branch, so a pull request that
        # was pointed elsewhere is blocked until a new run whatever happens here, and every
        # edit of a title or a body would restart the whole CI.
        top = strip_comments(ci_text()).split("\njobs:\n", 1)[0]
        self.assertIn("    types: [opened, synchronize, reopened]\n", top)
        self.assertNotIn("edited", top)
        raw = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(raw, r"# `edited` is left out on purpose")


class WiringIsWatched(unittest.TestCase):
    """A change to what watches the CI must run what tests it (point 8 of the cancel rules)."""

    def test_a_change_to_the_workflows_the_map_the_list_or_the_scripts_runs_everything(self):
        data = ci_map.load(ROOT / ".github/ci-map.json")
        everything = set(data["suites"])
        for path in (
            ".github/workflows/codeql.yml",
            ".github/workflows/release-dashboard.yml",
            ".github/workflows/main-guard.yml",
            ".github/workflows/a-future-one.yml",
            ".github/ci-map.json",
            ".github/required-checks.json",
            ".github/scripts/ci/test_ci_map.py",
            ".github/actions/setup-rust/action.yml",
        ):
            self.assertEqual(ci_map.select([path], data), everything, path)

    def test_dependabot_watches_the_requirements_of_the_yaml_reader(self):
        data = load_yaml((ROOT / ".github/dependabot.yml").read_text(encoding="utf-8"))
        entries = [u for u in data["updates"] if u["package-ecosystem"] == "pip"]
        self.assertEqual([u["directory"] for u in entries], ["/.github/scripts/ci"])
        self.assertTrue((ROOT / ".github/scripts/ci/requirements.txt").is_file())
        self.assertEqual(entries[0]["target-branch"], "develop")

    def test_the_map_job_has_no_condition(self):
        body = split_jobs(strip_comments(WORKFLOW.read_text(encoding="utf-8")))["map"]
        self.assertNotRegex(body.split("    steps:", 1)[0], r"^    if:", "CI map must run on every change")




def gate_script() -> str:
    return run_block(split_jobs(ci_text())["gate"], GATE_STEP)


class Gate(unittest.TestCase):
    """The text of the gate job, run for real with the results a failing plan or suite produces."""

    SUITES = {"fmt": "server", "clippy": "server", "typecheck": "ui", "shellcheck": "shell"}

    def verdict(self, decide_result="success", plan=None, results=None):
        plan = {"server": "true", "ui": "false", "shell": "false", **(plan or {})}
        results = {"fmt": "success", "clippy": "success", "typecheck": "skipped", "shellcheck": "skipped", **(results or {})}
        needs = {"decide": {"result": decide_result, "outputs": plan}, "map": {"result": "success", "outputs": {}}}
        needs.update({k: {"result": v, "outputs": {}} for k, v in results.items()})
        done = subprocess.run(
            ["bash", "-c", gate_script()],
            env={**os.environ, "NEEDS": json.dumps(needs), "SUITES": json.dumps(self.SUITES)},
            capture_output=True,
            text=True,
        )
        return done.returncode, done.stdout + done.stderr

    def test_everything_passed_and_the_plan_left_the_rest_out_is_green(self):
        self.assertEqual(self.verdict()[0], 0)

    def test_a_suite_skipped_because_the_plan_said_so_is_green(self):
        code, _ = self.verdict(plan={"ui": "false"}, results={"typecheck": "skipped"})
        self.assertEqual(code, 0)

    def test_a_failed_plan_is_red_even_when_everything_else_looks_fine(self):
        code, out = self.verdict(decide_result="failure")
        self.assertEqual(code, 1)
        self.assertIn("decide: failure", out)

    def test_a_cancelled_or_skipped_plan_is_red(self):
        for result in ("cancelled", "skipped"):
            self.assertEqual(self.verdict(decide_result=result)[0], 1, result)

    def test_a_suite_skipped_because_a_job_before_it_failed_is_red(self):
        code, out = self.verdict(results={"fmt": "failure", "clippy": "skipped"})
        self.assertEqual(code, 1)
        self.assertIn("clippy: skipped", out)
        self.assertIn("fmt: failure", out)

    def test_a_suite_the_plan_wanted_that_never_ran_is_red(self):
        code, out = self.verdict(plan={"ui": "true"}, results={"typecheck": "skipped"})
        self.assertEqual((code, "typecheck: skipped" in out), (1, True))

    def test_a_cancelled_job_is_red(self):
        self.assertEqual(self.verdict(results={"fmt": "cancelled"})[0], 1)

    def test_a_job_the_gate_does_not_know_cannot_be_skipped(self):
        code, _ = self.verdict(results={"brand-new": "skipped"})
        self.assertEqual(code, 1)

    def test_an_unreadable_needs_is_red(self):
        done = subprocess.run(
            ["bash", "-c", gate_script()],
            env={**os.environ, "NEEDS": "not json", "SUITES": "{}"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(done.returncode, 0)


class NoJobCanCancelRuns(unittest.TestCase):
    """Cancelling the other workflows of a commit needs a token that can write actions, and
    the only other workflow on a pull request is CodeQL, which a functional failure must
    never stop. So no job holds that token, whatever the way it is granted: the effective
    permissions are read (a job without its own block inherits the workflow's, and
    write-all counts)."""

    def workflows(self):
        for path in workflow_files():
            yield path.name, strip_comments(path.read_text(encoding="utf-8"))

    @staticmethod
    def permissions(block: str) -> "str | None":
        m = re.search(r"^ {0,4}permissions:(.*?)(?=^ {0,4}\S|\Z)", block, re.S | re.M)
        return m.group(1) if m else None

    @staticmethod
    def writes_actions(perms: "str | None") -> bool:
        return bool(perms) and bool(re.search(r"write-all|actions:\s*write", perms))

    def test_no_workflow_grants_write_at_the_top(self):
        for name, text in self.workflows():
            top = text.split("\njobs:\n", 1)[0]
            perms = self.permissions(top)
            self.assertIsNotNone(perms, f"{name} declares no permissions")
            self.assertFalse(self.writes_actions(perms), name)

    def test_no_job_holds_the_write_token(self):
        for name, text in self.workflows():
            for job, body in split_jobs(text).items():
                head = body.split("    steps:", 1)[0]
                self.assertFalse(self.writes_actions(self.permissions(head)), f"{name}:{job}")

    def test_no_job_calls_the_run_cancel_api(self):
        for name, text in self.workflows():
            self.assertNotRegex(text, r"gh run cancel|actions/runs/\S*/cancel", name)

    def test_the_check_recognises_the_ways_of_holding_the_token(self):
        self.assertTrue(self.writes_actions("      contents: read\n      actions: write\n"))
        self.assertTrue(self.writes_actions(" write-all"))
        self.assertFalse(self.writes_actions("      contents: read\n"))

    ALLOWED = re.compile(
        r"matrix\.[\w-]+(\.[\w-]+)*"
        r"|runner\.[\w-]+"
        r"|needs\.[\w-]+\.outputs\.[\w-]+"
        r"|steps\.[\w-]+\.outputs\.[\w-]+"
    )

    @staticmethod
    def run_blocks(text: str) -> "list[str]":
        """The text of every `run:` of a workflow, one-line or block, read strictly."""
        lines = text.splitlines()
        blocks = []
        i = 0
        while i < len(lines):
            m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
            if not m:
                i += 1
                continue
            indent = len(m.group(1)) + (2 if lines[i].lstrip().startswith("- ") else 0)
            body = [m.group(2)]
            i += 1
            while i < len(lines) and (lines[i].strip() == "" or len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i])
                i += 1
            blocks.append("\n".join(body))
        return blocks

    def expressions(self, text: str) -> "list[str]":
        return [e.strip() for block in self.run_blocks(text) for e in re.findall(r"\$\{\{(.*?)\}\}", block, re.S)]

    def test_a_run_holds_only_expressions_of_a_short_list_and_everything_else_goes_through_env(self):
        for name, text in self.workflows():
            for expr in self.expressions(text):
                self.assertRegex(expr, self.ALLOWED.pattern, f"{name}: ${{{{ {expr} }}}} inside a run")
                self.assertIsNotNone(self.ALLOWED.fullmatch(expr), f"{name}: ${{{{ {expr} }}}} inside a run")

    def test_the_checker_finds_what_it_should_refuse(self):
        bad = [
            'run: echo "${{ github.event.pull_request.title }}"',
            'run: |\n  TAG="${{ github.event_name == \'x\' && inputs.tag || github.ref_name }}"',
            "- run: echo ${{ inputs.tag }}",
            "run: echo ${{ env.X }}",
            "run: echo ${{ github.head_ref }}",
            "run: echo ${{ secrets.X }}",
            "run: echo ${{ needs.a.result }}",
        ]
        for text in bad:
            found = self.expressions(text)
            self.assertTrue(found, text)
            self.assertTrue(any(self.ALLOWED.fullmatch(e) is None for e in found), text)
        good = ["run: echo ${{ matrix.arch }}", "run: |\n  x=${{ runner.temp }}\n  y=${{ needs.tag.outputs.tag }}"]
        for text in good:
            self.assertTrue(all(self.ALLOWED.fullmatch(e) for e in self.expressions(text)), text)

    def test_the_line_that_put_the_tag_into_a_script_would_be_caught(self):
        line = "run: |\n  TAG=\"${{ github.event_name == 'workflow_dispatch' && inputs.tag || github.ref_name }}\"\n  VERSION=${TAG#dashboard@}"
        self.assertTrue(any(self.ALLOWED.fullmatch(e) is None for e in self.expressions(line)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
