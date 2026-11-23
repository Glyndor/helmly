"""The contract of the CI workflows, stated fact by fact and read key by key.

test_ci_wiring.py compares the files with their reference copies, but a pull request that
changes a workflow brings its reference copy too, so a matching copy proves nothing. These
tests state what must hold whatever the copies say, and read the workflows with the strict
loader (`load_yaml` in ci_common.py: PyYAML from hashed wheels, no key twice, no anchors): the planner reads the base commit
and its entry point decides what it should; each suite runs the command it is named for and
nothing around it can turn that command into a success; no step has a shell, a
`continue-on-error` or a condition of its own making; the aggregate is what it is; the
analysis cannot be skipped or cancelled; and a pull request from a fork cannot pass as the
branch `develop`.

    python3 .github/scripts/ci/test_ci_contract.py
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ci_map  # noqa: E402
import decide  # noqa: E402
from ci_common import ROOT, ci_files, load_yaml, unpin_ref, workflow_files  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"
CHECKOUT = "actions/checkout@<sha>"  # the commit is Dependabot's to move; see ci_common.unpin_ref
PLAN_IF = "env.PLAN != 'false'"
PLANNER_REF = "${{ github.event_name == 'pull_request' && github.event.pull_request.base.sha || github.sha }}"
SUITES = {
    "fmt": "server",
    "urls": "server",
    "clippy": "server",
    "test": "server",
    "typecheck": "ui",
    "unit": "ui",
    "e2e": "ui",
    "shellcheck": "shell",
}
ALWAYS = ("map", "tree", "pins", "audit", "audit-bun")
SETUP_RUST = ("uses", "./.github/actions/setup-rust")
SETUP_BUN = ("uses", "./.github/actions/setup-bun")
SQLX = ("uses", "./.github/actions/cargo-tool")
MIGRATE = ("run", "sqlx migrate run --source dashboard/server/migrations")
CHECKOUT_STEP = ("uses", CHECKOUT)

# What each suite job runs after its guard, in order: the kind of step and its exact text.
COMMANDS = {
    "fmt": [CHECKOUT_STEP, SETUP_RUST, ("run", "cargo fmt --package helmly-dashboard-server -- --check")],
    "urls": [CHECKOUT_STEP, ("run", "python3 .github/scripts/audit-urls.py")],
    "clippy": [
        CHECKOUT_STEP,
        SETUP_RUST,
        SQLX,
        ("run", "bash .github/scripts/ci/start-services.sh postgres"),
        MIGRATE,
        ("run", "cargo sqlx prepare --check"),
        ("run", "cargo clippy --locked --package helmly-dashboard-server --all-targets -- -D warnings"),
    ],
    "test": [
        CHECKOUT_STEP,
        SETUP_RUST,
        SQLX,
        ("run", "bash .github/scripts/ci/start-services.sh postgres valkey"),
        MIGRATE,
        ("run", "sudo apt-get install -y --no-install-recommends python3-cryptography"),
        ("run", "cargo test --locked --package helmly-dashboard-server -- --test-threads=1"),
    ],
    "typecheck": [CHECKOUT_STEP, SETUP_BUN, ("run", "bun run typecheck")],
    "unit": [CHECKOUT_STEP, SETUP_BUN, ("run", "bun run test")],
    "e2e": [
        CHECKOUT_STEP,
        SETUP_BUN,
        ("run", "bun x playwright install chromium --with-deps"),
        ("run", "bun run build"),
        ("run", "bun run test:e2e"),
        ("uses", "actions/upload-artifact@<sha>"),
    ],
    "shellcheck": [
        CHECKOUT_STEP,
        ("run", "git ls-files -z '*.sh' | xargs -0 -n1 bash -n"),
        ("run", "bash .github/scripts/install-shellcheck.sh"),
        ("run", "bash scripts/lint.sh"),
    ],
}
WORKDIR = {
    "fmt": "internal",
    "clippy": "internal",
    "test": "internal",
    "typecheck": "internal/dashboard/ui",
    "unit": "internal/dashboard/ui",
    "e2e": "internal/dashboard/ui",
    "audit": "internal",
    "audit-bun": "internal/dashboard/ui",
}


def load(name: str) -> dict:
    return load_yaml((WORKFLOWS / name).read_text(encoding="utf-8"))


def kind(step: dict) -> "tuple[str, str]":
    if "uses" in step:
        return "uses", unpin_ref(step["uses"])
    return "run", step["run"]


class StrictReading(unittest.TestCase):
    def test_every_workflow_and_action_is_readable_by_the_strict_loader(self):
        for path in workflow_files() + [p for p in ci_files() if p.name == "action.yml"]:
            self.assertIsInstance(load_yaml(path.read_text(encoding="utf-8")), dict, path)

    def test_the_loader_adds_its_three_refusals_to_pyyaml_safe_loader(self):
        for what, text in (
            ("a key twice", "a: 1\na: 2\n"),
            ("a key twice, nested", "a:\n  b: 1\n  b: 2\n"),
            ("an anchor", "a: &x 1\n"),
            ("an alias", "a: &x 1\nb: *x\n"),
            ("a second document", "a: 1\n---\nb: 2\n"),
        ):
            with self.subTest(what), self.assertRaises(Exception):
                load_yaml(text)

    def test_every_plain_scalar_stays_the_text_it_was_written_as(self):
        self.assertEqual(
            load_yaml("on: true\nn: 10\nv: ~\nd: 2026-01-01\ne:\nl: [1, no]\n"),
            {"on": "true", "n": "10", "v": "~", "d": "2026-01-01", "e": "", "l": ["1", "no"]},
        )


class NothingChangesHowAStepRuns(unittest.TestCase):
    """A shell, a continue-on-error or a defaults block of its own makes a step succeed without
    running what it says, so none is allowed in the workflows that judge a change."""

    FILES = ("ci.yml", "codeql.yml", "audit.yml", "main-guard.yml", "release-dashboard.yml")

    def test_no_shell_no_continue_on_error_anywhere(self):
        for name in self.FILES:
            doc = load(name)
            self.assertNotIn("continue-on-error", doc, name)
            self.assertNotIn("shell", doc.get("defaults", {}).get("run", {}), name)
            for job, body in doc["jobs"].items():
                where = f"{name}:{job}"
                self.assertNotIn("continue-on-error", body, where)
                self.assertNotIn("shell", body.get("defaults", {}).get("run", {}), where)
                for step in body["steps"]:
                    self.assertNotIn("shell", step, where)
                    self.assertNotIn("continue-on-error", step, where)

    def test_defaults_are_only_a_working_directory_and_the_expected_one(self):
        doc = load("ci.yml")
        self.assertNotIn("defaults", doc)
        for job, body in doc["jobs"].items():
            if job in WORKDIR:
                self.assertEqual(body["defaults"], {"run": {"working-directory": WORKDIR[job]}}, job)
            else:
                self.assertNotIn("defaults", body, job)
        for name in ("codeql.yml", "audit.yml", "main-guard.yml"):
            for job, body in load(name)["jobs"].items():
                if name == "audit.yml":
                    continue
                self.assertNotIn("defaults", body, f"{name}:{job}")

    def test_no_step_of_the_suites_has_a_condition_of_its_own_making(self):
        doc = load("ci.yml")
        for job, expected in COMMANDS.items():
            steps = doc["jobs"][job]["steps"]
            for step in steps[1:]:
                want = PLAN_IF + (" && failure()" if "upload-artifact" in step.get("uses", "") else "")
                self.assertEqual(step.get("if"), want, f"{job}: {kind(step)}")


class ThePlannerIsTheBases(unittest.TestCase):
    def setUp(self):
        self.doc = load("ci.yml")
        self.job = self.doc["jobs"]["decide"]

    def test_the_planner_checks_out_the_base_commit_of_a_pull_request(self):
        first = self.job["steps"][0]
        self.assertEqual(unpin_ref(first["uses"]), CHECKOUT)
        self.assertEqual(first["with"], {"ref": PLANNER_REF, "fetch-depth": "0", "persist-credentials": "false"})

    def test_no_other_step_of_the_planner_checks_out_code_or_changes_the_tree(self):
        self.assertEqual(sum("uses" in s for s in self.job["steps"]), 1)
        self.assertNotIn("needs", self.job)
        self.assertNotIn("if", self.job)

    def test_the_planner_gets_the_event_values_from_their_own_expressions(self):
        step = [s for s in self.job["steps"] if s.get("id") == "decide"][0]
        self.assertEqual(
            step["env"],
            {
                "EVENT": "${{ github.event_name }}",
                "BASE_REF": "${{ github.event.pull_request.base.ref }}",
                "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
                "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
                "BEFORE_SHA": "${{ github.event.before }}",
                "AFTER_SHA": "${{ github.sha }}",
            },
        )
        self.assertNotIn("if", step)

    def test_the_planner_script_is_run_from_the_checkout_it_made_and_nothing_else_is(self):
        step = [s for s in self.job["steps"] if s.get("id") == "decide"][0]
        self.assertEqual(step["run"].count("python3 "), 1)
        self.assertIn("python3 .github/scripts/ci/decide.py", step["run"])
        self.assertNotIn("working-directory", step)
        self.assertEqual(self.job["outputs"], {k: f"${{{{ steps.decide.outputs.{k} }}}}" for k in ("server", "ui", "shell")})

    def test_the_planner_has_a_single_way_to_say_everything_runs_for_main(self):
        run = [s for s in self.job["steps"] if s.get("id") == "decide"][0]["run"]
        first = run.split("elif", 1)[0]
        self.assertIn('[ "$EVENT" = pull_request ] && [ "$BASE_REF" = main ]', first)
        self.assertIn("server=true ui=true shell=true", first)


class TheEntryPointDecides(unittest.TestCase):
    """decide.py run as the workflow runs it, in a throwaway repository: what it writes for the
    workflow to read is what the plan is. The helpers have their own tests; this is main()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "ci")
        (self.repo / ".github").mkdir()
        (self.repo / ".github/ci-map.json").write_text((ROOT / ".github/ci-map.json").read_text())
        self.out = pathlib.Path(self.tmp.name) / "out"
        self.out.touch()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=True, text=True).stdout.strip()

    def commit(self, **files):
        for rel, text in files.items():
            path = self.repo / rel.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "x")
        return self.git("rev-parse", "HEAD")

    def plan(self, **env):
        run = subprocess.run(
            [sys.executable, str(ROOT / ".github/scripts/ci/decide.py")],
            cwd=self.repo,
            env={**{k: v for k, v in os.environ.items() if k not in ("EVENT", "BASE_REF")}, "GITHUB_OUTPUT": str(self.out), **env},
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return dict(line.split("=") for line in self.out.read_text().split())

    def test_a_docs_only_pull_request_plans_no_suite(self):
        base = self.commit(README_md="a")
        head = self.commit(docs__a_md="b")
        got = self.plan(EVENT="pull_request", BASE_REF="develop", BASE_SHA=base, HEAD_SHA=head)
        self.assertEqual(got, {"server": "false", "ui": "false", "shell": "false"})

    def test_a_server_file_plans_the_server_only(self):
        base = self.commit(README_md="a")
        head = self.commit(internal__dashboard__server__src__main_rs="x")
        got = self.plan(EVENT="pull_request", BASE_REF="develop", BASE_SHA=base, HEAD_SHA=head)
        self.assertEqual(got, {"server": "true", "ui": "false", "shell": "false"})

    def test_a_pull_request_into_main_plans_everything_even_for_docs(self):
        base = self.commit(README_md="a")
        head = self.commit(docs__a_md="b")
        got = self.plan(EVENT="pull_request", BASE_REF="main", BASE_SHA=base, HEAD_SHA=head)
        self.assertEqual(got, {"server": "true", "ui": "true", "shell": "true"})

    def test_an_unreadable_diff_and_an_unknown_path_plan_everything(self):
        base = self.commit(README_md="a")
        head = self.commit(brand_new__thing_xyz="x")
        every = {"server": "true", "ui": "true", "shell": "true"}
        self.assertEqual(self.plan(EVENT="pull_request", BASE_REF="develop", BASE_SHA=base, HEAD_SHA=head), every)
        self.out.write_text("")
        self.assertEqual(self.plan(EVENT="pull_request", BASE_REF="develop", BASE_SHA="0" * 40, HEAD_SHA=head), every)

    def test_it_writes_exactly_the_outputs_the_planner_job_publishes(self):
        base = self.commit(README_md="a")
        head = self.commit(docs__a_md="b")
        got = self.plan(EVENT="pull_request", BASE_REF="develop", BASE_SHA=base, HEAD_SHA=head)
        published = set(load("ci.yml")["jobs"]["decide"]["outputs"])
        self.assertEqual(set(got), published)

    def test_the_helpers_alone_cannot_stand_for_the_entry_point(self):
        text = (ROOT / ".github/scripts/ci/decide.py").read_text(encoding="utf-8")
        self.assertIn("def main()", text)
        self.assertIn('if __name__ == "__main__":', text)
        self.assertIn("GITHUB_OUTPUT", text)
        self.assertTrue(callable(decide.main))


class EachSuiteRunsItsCommand(unittest.TestCase):
    def setUp(self):
        self.doc = load("ci.yml")

    def test_every_suite_job_runs_exactly_its_steps_after_the_guard(self):
        for job, expected in COMMANDS.items():
            steps = self.doc["jobs"][job]["steps"]
            self.assertEqual([kind(s) for s in steps[1:]], expected, job)

    def test_a_command_is_a_run_of_its_own_and_not_wrapped_or_followed_by_anything(self):
        for job, expected in COMMANDS.items():
            for (what, text), step in zip(expected, self.doc["jobs"][job]["steps"][1:]):
                if what == "run":
                    self.assertNotIn("\n", text)
                    self.assertEqual(step["run"], text)
                    for forbidden in ("|| true", "; true", "&& true", "exit 0", "echo '", 'echo "'):
                        self.assertNotIn(forbidden, step["run"], f"{job}: {text}")

    def test_the_cargo_test_command_is_the_real_one(self):
        steps = self.doc["jobs"]["test"]["steps"]
        self.assertEqual(steps[-1]["run"], "cargo test --locked --package helmly-dashboard-server -- --test-threads=1")
        self.assertEqual(steps[-1]["env"]["REQUIRE_PY_CRYPTO"], "1")

    def test_each_job_has_its_plan_bound_to_its_own_suite_and_the_guard_first(self):
        for job, suite in SUITES.items():
            body = self.doc["jobs"][job]
            self.assertEqual(body["env"]["PLAN"], f"${{{{ needs.decide.outputs.{suite} }}}}", job)
            self.assertEqual(body["if"], "${{ !cancelled() }}", job)
            self.assertIn("decide", body["needs"], job)
            guard = body["steps"][0]
            self.assertEqual(guard["env"], {"NEEDS": "${{ toJSON(needs) }}"}, job)
            self.assertNotIn("if", guard)
            self.assertIn('case "$PLAN" in', guard["run"])

    def test_the_jobs_without_a_plan_have_none_and_do_not_wait_for_it(self):
        for job in ALWAYS:
            body = self.doc["jobs"][job]
            for key in ("if", "needs", "env"):
                self.assertNotIn(key, body, job)
            for step in body["steps"]:
                self.assertNotIn("if", step, job)

    def test_only_the_planner_names_a_ref_to_check_out(self):
        for name in ("ci.yml", "audit.yml", "codeql.yml"):
            for job, body in load(name)["jobs"].items():
                for step in body["steps"]:
                    if str(step.get("uses", "")).startswith("actions/checkout"):
                        has_ref = "ref" in step.get("with", {})
                        self.assertEqual(has_ref, (name, job) == ("ci.yml", "decide"), f"{name}:{job}")
                        self.assertEqual(step["with"].get("persist-credentials"), "false", f"{name}:{job}")


class TheAggregate(unittest.TestCase):
    def setUp(self):
        self.doc = load("ci.yml")
        self.gate = self.doc["jobs"]["gate"]

    def test_it_always_runs_waits_for_everything_and_has_one_step(self):
        self.assertEqual(self.gate["if"], "${{ always() }}")
        self.assertEqual(set(self.gate["needs"]), set(self.doc["jobs"]) - {"gate"})
        self.assertEqual(len(self.gate["steps"]), 1)
        self.assertEqual(set(self.gate) & {"continue-on-error", "defaults", "strategy", "container"}, set())

    def test_its_suites_are_the_ones_the_jobs_serve(self):
        self.assertEqual(json.loads(self.gate["steps"][0]["env"]["SUITES"]), SUITES)
        self.assertEqual(self.gate["steps"][0]["env"]["NEEDS"], "${{ toJSON(needs) }}")

    def test_its_script_is_inline_and_the_one_the_tests_run(self):
        run = self.gate["steps"][0]["run"]
        self.assertTrue(run.startswith("set -euo pipefail\n"))
        self.assertIn('exit 1', run)
        for forbidden in ("|| true", "exit 0\n", "continue"):
            self.assertNotIn(forbidden, run)


class TheTriggers(unittest.TestCase):
    def test_ci_runs_on_pull_requests_into_develop_and_main_and_on_pushes_to_develop(self):
        self.assertEqual(
            load("ci.yml")["on"],
            {
                "pull_request": {"branches": ["develop", "main"], "types": ["opened", "synchronize", "reopened"]},
                "push": {"branches": ["develop"]},
            },
        )

    def test_the_base_filter_is_what_keeps_a_branch_named_in_another_case_out(self):
        # An expression compares without regard to case; this filter does not, and it is what
        # stops a base like `Develop` from ever producing the name a ruleset requires.
        for name in ("ci.yml", "codeql.yml"):
            self.assertEqual(load(name)["on"]["pull_request"]["branches"], ["develop", "main"])

    def test_the_permissions_and_concurrency_of_ci(self):
        doc = load("ci.yml")
        self.assertEqual(doc["permissions"], {"contents": "read"})
        self.assertEqual(
            doc["concurrency"],
            {
                "group": "ci-${{ github.event.pull_request.number || github.ref }}",
                "cancel-in-progress": "${{ github.event_name == 'pull_request' }}",
            },
        )


class TheAnalysisCannotBeSkippedOrCancelled(unittest.TestCase):
    def setUp(self):
        self.doc = load("codeql.yml")

    def test_no_concurrency_group_at_all(self):
        self.assertNotIn("concurrency", self.doc)
        for body in self.doc["jobs"].values():
            self.assertNotIn("concurrency", body)

    def test_the_job_has_no_condition_and_no_way_to_succeed_without_analysing(self):
        body = self.doc["jobs"]["analyze"]
        for key in ("if", "continue-on-error", "needs", "container", "services"):
            self.assertNotIn(key, body)
        self.assertEqual(body["strategy"]["fail-fast"], "false")
        self.assertEqual(
            [m["language"] for m in body["strategy"]["matrix"]["include"]],
            ["actions", "javascript-typescript", "rust"],
        )

    def test_its_steps_are_a_checkout_an_init_and_an_analysis_and_nothing_else(self):
        steps = self.doc["jobs"]["analyze"]["steps"]
        self.assertEqual(
            [s["uses"].split("@")[0] for s in steps],
            ["actions/checkout", "github/codeql-action/init", "github/codeql-action/analyze"],
        )
        for step in steps:
            self.assertNotIn("if", step)
            self.assertNotIn("run", step)
        self.assertEqual(steps[2]["with"], {"category": "/language:${{ matrix.language }}"})

    def test_it_runs_on_every_pull_request_and_every_push_of_the_two_branches(self):
        self.assertEqual(self.doc["on"]["pull_request"], {"branches": ["develop", "main"]})
        self.assertEqual(self.doc["on"]["push"], {"branches": ["develop", "main"]})
        self.assertIn("schedule", self.doc["on"])


class DevelopOnlyLooksAtTheRepository(unittest.TestCase):
    def setUp(self):
        doc = load("main-guard.yml")
        self.step = doc["jobs"]["develop-only"]["steps"][0]

    def run_guard(self, head_ref, head_repo, repo="Glyndor/helmly"):
        env = {
            **{k: v for k, v in os.environ.items() if k not in self.step["env"]},
            "HEAD_REF": head_ref,
            "HEAD_REPO": head_repo,
            "REPO": repo,
        }
        return subprocess.run(["bash", "-c", self.step["run"]], env=env, capture_output=True, text=True).returncode

    def test_the_values_reach_the_script_only_through_env_and_from_the_right_fields(self):
        self.assertEqual(
            self.step["env"],
            {
                "HEAD_REF": "${{ github.head_ref }}",
                "HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name }}",
                "REPO": "${{ github.repository }}",
            },
        )
        self.assertNotIn("${{", self.step["run"])

    def test_develop_of_this_repository_passes(self):
        self.assertEqual(self.run_guard("develop", "Glyndor/helmly"), 0)

    def test_a_branch_called_develop_in_a_fork_does_not_pass(self):
        self.assertEqual(self.run_guard("develop", "someone/helmly"), 1)

    def test_a_deleted_fork_with_no_repository_does_not_pass(self):
        self.assertEqual(self.run_guard("develop", ""), 1)

    def test_another_branch_of_this_repository_does_not_pass(self):
        for ref in ("feature/x", "Develop", "develop2", "", "develop\nx"):
            self.assertEqual(self.run_guard(ref, "Glyndor/helmly"), 1, repr(ref))

    def test_a_repository_that_only_looks_like_this_one_does_not_pass(self):
        for repo in ("Glyndor/helmly-fork", "glyndor/helmly", "Glyndor/helmly ", "xGlyndor/helmly"):
            self.assertEqual(self.run_guard("develop", repo), 1, repr(repo))

    def test_the_job_is_the_required_one_and_has_no_condition(self):
        body = load("main-guard.yml")["jobs"]["develop-only"]
        self.assertEqual(body["name"], "develop-only")
        for key in ("if", "continue-on-error", "needs"):
            self.assertNotIn(key, body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
