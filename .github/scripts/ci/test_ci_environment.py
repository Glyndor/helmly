"""Everything that changes how a step runs, written out and compared whole.

The keys of the workflow, of each job and of each step must be in an allowed set (no `container`, no
`continue-on-error`, no job-level `uses` or `secrets`); the `env` of the workflow, of each job and
of each step, and the `working-directory` of each step, must equal the tables below, the exact
values of today; and no name that changes how a process starts (BASH_ENV, PATH, LD_PRELOAD,
PYTHONPATH and the like) may be set, whatever the value. So nothing that changes how a step runs
can be added without being written here, in a file a reviewer reads. A step is named by its position,
so reordering the steps of a job is a change to this file as well.

    python3 .github/scripts/ci/test_ci_environment.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_common import ROOT, load_yaml  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"


def load(name: str) -> dict:
    return load_yaml((WORKFLOWS / name).read_text(encoding="utf-8"))


WORKFLOW_ENV = {
    "ci.yml": {
        "CARGO_TERM_COLOR": "always",
        "RUST_BACKTRACE": "1",
        "DATABASE_URL": "postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci",
    },
}

JOB_ENV = {('ci.yml', 'fmt'): {'PLAN': '${{ needs.decide.outputs.server }}'},
 ('ci.yml', 'urls'): {'PLAN': '${{ needs.decide.outputs.server }}'},
 ('ci.yml', 'clippy'): {'PLAN': '${{ needs.decide.outputs.server }}',
                        'DATABASE_URL': 'postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci'},
 ('ci.yml', 'test'): {'PLAN': '${{ needs.decide.outputs.server }}',
                      'DATABASE_URL': 'postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci'},
 ('ci.yml', 'typecheck'): {'PLAN': '${{ needs.decide.outputs.ui }}'},
 ('ci.yml', 'unit'): {'PLAN': '${{ needs.decide.outputs.ui }}'},
 ('ci.yml', 'e2e'): {'PLAN': '${{ needs.decide.outputs.ui }}'},
 ('ci.yml', 'shellcheck'): {'PLAN': '${{ needs.decide.outputs.shell }}'},
 ('release-dashboard.yml', 'ci-server'): {'CARGO_TERM_COLOR': 'always',
                                          'RUST_BACKTRACE': '1',
                                          'DATABASE_URL': 'postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci',
                                          'REDIS_URL': 'redis://localhost:6379'}}

STEP_ENV = {('ci.yml', 'decide', 1): {'PR': '${{ github.event.pull_request.number }}'},
 ('ci.yml', 'decide', 2): {'EVENT': '${{ github.event_name }}',
                           'BASE_REF': '${{ github.event.pull_request.base.ref }}',
                           'BASE_SHA': '${{ github.event.pull_request.base.sha }}',
                           'HEAD_SHA': '${{ github.event.pull_request.head.sha }}',
                           'BEFORE_SHA': '${{ github.event.before }}',
                           'AFTER_SHA': '${{ github.sha }}'},
 ('ci.yml', 'tree', 1): {'EVENT': '${{ github.event_name }}',
                         'BASE_SHA': '${{ github.event.pull_request.base.sha }}'},
 ('ci.yml', 'pins', 1): {'EVENT': '${{ github.event_name }}',
                         'BASE_SHA': '${{ github.event.pull_request.base.sha }}',
                         'BASE_REF': '${{ github.event.pull_request.base.ref }}'},
 ('ci.yml', 'commits', 1): {'BASE_SHA': '${{ github.event.pull_request.base.sha }}',
                            'HEAD_SHA': '${{ github.event.pull_request.head.sha }}'},
 ('ci.yml', 'fmt', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'urls', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'clippy', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'test', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'test', 7): {'REDIS_URL': 'redis://localhost:6379',
                         'REQUIRE_PY_CRYPTO': '1',
                         'HELMLY_TEST_PYTHON': '/usr/bin/python3'},
 ('ci.yml', 'typecheck', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'unit', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'e2e', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'e2e', 4): {'BACKEND_URL': 'http://localhost:8080'},
 ('ci.yml', 'e2e', 5): {'CI': 'true', 'BACKEND_URL': 'http://localhost:8080'},
 ('ci.yml', 'shellcheck', 0): {'NEEDS': '${{ toJSON(needs) }}'},
 ('ci.yml', 'gate', 0): {'NEEDS': '${{ toJSON(needs) }}',
                         'SUITES': '{"fmt":"server","urls":"server","clippy":"server","test":"server","typecheck":"ui","unit":"ui","e2e":"ui","shellcheck":"shell"}'},
 ('audit.yml', 'cargo', 1): {'AUDITED': '${{ matrix.ref }}'},
 ('audit.yml', 'bun', 1): {'AUDITED': '${{ matrix.ref }}'},
 ('main-guard.yml', 'develop-only', 0): {'HEAD_REF': '${{ github.head_ref }}',
                                         'HEAD_REPO': '${{ github.event.pull_request.head.repo.full_name }}',
                                         'REPO': '${{ github.repository }}'},
 ('release-dashboard.yml', 'tag', 0): {'TAG': "${{ github.event_name == 'workflow_dispatch' && inputs.tag || "
                                              'github.ref_name }}'},
 ('release-dashboard.yml', 'prepare', 3): {'DATABASE_URL': 'postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci'},
 ('release-dashboard.yml', 'prepare', 4): {'DATABASE_URL': 'postgres://helmly_ci:helmly_ci@localhost:5432/helmly_ci'},
 ('release-dashboard.yml', 'build', 4): {'TAG': '${{ needs.tag.outputs.tag }}'},
 ('release-dashboard.yml', 'build', 5): {'SQLX_OFFLINE': 'true'},
 ('release-dashboard.yml', 'build', 9): {'NEXT_TELEMETRY_DISABLED': '1'},
 ('release-dashboard.yml', 'sign-dry-run', 5): {'SIGN_PYTHON': '${{ runner.temp }}/signvenv/bin/python'},
 ('release-dashboard.yml', 'release', 1): {'TAG': '${{ needs.tag.outputs.tag }}',
                                           'RUN_SHA': '${{ github.sha }}',
                                           'EVENT': '${{ github.event_name }}',
                                           'REF': '${{ github.ref }}'},
 ('release-dashboard.yml', 'release', 5): {'GLYNDOR_RELEASE_ED25519_KEY': '${{ '
                                                                          'secrets.GLYNDOR_RELEASE_ED25519_KEY '
                                                                          '}}',
                                           'SIGN_PYTHON': '${{ runner.temp }}/signvenv/bin/python'},
 ('release-dashboard.yml', 'release', 6): {'GH_TOKEN': '${{ github.token }}',
                                           'GH_REPO': '${{ github.repository }}',
                                           'TAG': '${{ needs.tag.outputs.tag }}'}}

STEP_WORKDIR = {('ci.yml', 'fmt', 0): '${{ github.workspace }}',
 ('ci.yml', 'urls', 0): '${{ github.workspace }}',
 ('ci.yml', 'clippy', 0): '${{ github.workspace }}',
 ('ci.yml', 'clippy', 4): '${{ github.workspace }}',
 ('ci.yml', 'clippy', 6): 'internal/dashboard/server',
 ('ci.yml', 'clippy', 8): '${{ github.workspace }}',
 ('ci.yml', 'test', 0): '${{ github.workspace }}',
 ('ci.yml', 'test', 4): '${{ github.workspace }}',
 ('ci.yml', 'test', 6): '${{ github.workspace }}',
 ('ci.yml', 'typecheck', 0): '${{ github.workspace }}',
 ('ci.yml', 'unit', 0): '${{ github.workspace }}',
 ('ci.yml', 'e2e', 0): '${{ github.workspace }}',
 ('ci.yml', 'shellcheck', 0): '${{ github.workspace }}',
 ('audit.yml', 'cargo', 3): '${{ runner.temp }}/clean',
 ('audit.yml', 'bun', 3): '${{ runner.temp }}/clean',
 ('release-dashboard.yml', 'prepare', 4): 'internal/dashboard/server',
 ('release-dashboard.yml', 'ci-server', 3): '${{ github.workspace }}',
 ('release-dashboard.yml', 'ci-ui', 1): '${{ github.workspace }}',
 ('release-dashboard.yml', 'build', 6): '${{ github.workspace }}',
 ('release-dashboard.yml', 'build', 7): '${{ github.workspace }}',
 ('release-dashboard.yml', 'build', 8): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 9): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 10): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 11): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 12): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 13): 'internal/dashboard/ui',
 ('release-dashboard.yml', 'build', 14): '${{ github.workspace }}',
 ('release-dashboard.yml', 'release', 6): '${{ runner.temp }}/artifacts'}


# The services a job starts beside its steps. Only these two jobs have any, each block is compared whole
# (image, env, ports, options), so a volume, a privileged option or another image is a change here.
SERVICES = {('release-dashboard.yml', 'prepare'): {'postgres': {'image': 'postgres:18-alpine',
                                                     'env': {'POSTGRES_USER': 'helmly_ci',
                                                             'POSTGRES_PASSWORD': 'helmly_ci',
                                                             'POSTGRES_DB': 'helmly_ci'},
                                                     'ports': ['5432:5432'],
                                                     'options': '--health-cmd pg_isready --health-interval '
                                                                '5s --health-timeout 5s --health-retries '
                                                                '10'}},
 ('release-dashboard.yml', 'ci-server'): {'postgres': {'image': 'postgres:18-alpine',
                                                       'env': {'POSTGRES_USER': 'helmly_ci',
                                                               'POSTGRES_PASSWORD': 'helmly_ci',
                                                               'POSTGRES_DB': 'helmly_ci'},
                                                       'ports': ['5432:5432'],
                                                       'options': '--health-cmd pg_isready --health-interval '
                                                                  '5s --health-timeout 5s --health-retries '
                                                                  '10'},
                                          'valkey': {'image': 'valkey/valkey:8-alpine',
                                                     'ports': ['6379:6379'],
                                                     'options': '--health-cmd "valkey-cli ping" '
                                                                '--health-interval 5s --health-timeout 5s '
                                                                '--health-retries 10'}}}

# The defaults of each job. A `shell` in a default changes how every `run` of the job is executed, and
# the working directory changes where it runs, so both are written out for every file.
JOB_DEFAULTS = {('ci.yml', 'fmt'): {'run': {'working-directory': 'internal'}},
 ('ci.yml', 'audit'): {'run': {'working-directory': 'internal'}},
 ('ci.yml', 'clippy'): {'run': {'working-directory': 'internal'}},
 ('ci.yml', 'test'): {'run': {'working-directory': 'internal'}},
 ('ci.yml', 'typecheck'): {'run': {'working-directory': 'internal/dashboard/ui'}},
 ('ci.yml', 'unit'): {'run': {'working-directory': 'internal/dashboard/ui'}},
 ('ci.yml', 'audit-bun'): {'run': {'working-directory': 'internal/dashboard/ui'}},
 ('ci.yml', 'e2e'): {'run': {'working-directory': 'internal/dashboard/ui'}},
 ('release-dashboard.yml', 'prepare'): {'run': {'working-directory': 'internal'}},
 ('release-dashboard.yml', 'ci-server'): {'run': {'working-directory': 'internal'}},
 ('release-dashboard.yml', 'ci-ui'): {'run': {'working-directory': 'internal/dashboard/ui'}},
 ('release-dashboard.yml', 'build'): {'run': {'working-directory': 'internal'}}}

ALL_FILES = ("ci.yml", "codeql.yml", "audit.yml", "main-guard.yml", "release-dashboard.yml")
WORKFLOW_KEYS = {"name", "on", "permissions", "concurrency", "env", "jobs"}
JOB_KEYS = {"name", "needs", "if", "runs-on", "timeout-minutes", "outputs", "strategy", "permissions", "defaults", "env", "steps"}
STEP_KEYS = {"name", "id", "if", "uses", "with", "run", "env", "working-directory"}
# Names that change how a process starts or what it loads. None may be set in a workflow, whatever
# the value; the exact tables already refuse any name that is not listed, and this says why.
FORBIDDEN_ENV = re.compile(
    r"^(BASH_ENV|ENV|PATH|LD_.*|PYTHON.*|NODE_OPTIONS|NODE_PATH|RUSTC_WRAPPER|RUSTFLAGS|RUSTC|CARGO_BUILD_RUSTC.*|"
    r"SHELLOPTS|BASHOPTS|PS4|IFS|PROMPT_COMMAND|BASH_FUNC_.*|GIT_SSH_COMMAND|GIT_EXEC_PATH|GIT_CONFIG.*|"
    r"NPM_CONFIG_.*|BUN_CONFIG_.*|DYLD_.*|PERL5OPT|RUBYOPT|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS)$"
)


def all_env_dicts(doc):
    if "env" in doc:
        yield "workflow", doc["env"]
    for job, body in doc["jobs"].items():
        if "env" in body:
            yield f"job {job}", body["env"]
        for i, step in enumerate(body["steps"]):
            if "env" in step:
                yield f"step {job}[{i}]", step["env"]


class EverythingThatChangesHowAStepRunsIsListed(unittest.TestCase):
    """The keys, the environments and the directories of the workflows are compared whole with
    what is expected, so an unknown key, a container, a new variable or another directory fails."""

    def test_the_keys_at_each_level_are_known(self):
        for name in ALL_FILES:
            doc = load(name)
            self.assertLessEqual(set(doc), WORKFLOW_KEYS, name)
            for job, body in doc["jobs"].items():
                allowed = JOB_KEYS | ({"services"} if (name, job) in SERVICES else set())
                extra = (set(body) - allowed) - ({"environment"} if (name, job) == ("release-dashboard.yml", "release") else set())
                self.assertEqual(extra, set(), f"{name}:{job}")
                for key in ("container", "continue-on-error", "uses", "with", "secrets", "concurrency"):
                    self.assertNotIn(key, body, f"{name}:{job}")
                self.assertEqual(body["runs-on"], "ubuntu-latest", f"{name}:{job}")
                for i, step in enumerate(body["steps"]):
                    self.assertLessEqual(set(step), STEP_KEYS, f"{name}:{job}[{i}]")
                    self.assertTrue(("uses" in step) != ("run" in step), f"{name}:{job}[{i}] must have one of uses and run")

    def test_the_environment_of_the_workflow_of_each_job_and_of_each_step_is_exactly_the_listed_one(self):
        for name in ALL_FILES:
            doc = load(name)
            self.assertEqual(doc.get("env", {}), WORKFLOW_ENV.get(name, {}), name)
            for job, body in doc["jobs"].items():
                self.assertEqual(body.get("env", {}), JOB_ENV.get((name, job), {}), f"{name}:{job}")
                for i, step in enumerate(body["steps"]):
                    self.assertEqual(step.get("env", {}), STEP_ENV.get((name, job, i), {}), f"{name}:{job}[{i}]")

    def test_the_working_directory_of_each_step_is_exactly_the_listed_one(self):
        for name in ALL_FILES:
            for job, body in load(name)["jobs"].items():
                for i, step in enumerate(body["steps"]):
                    self.assertEqual(step.get("working-directory"), STEP_WORKDIR.get((name, job, i)), f"{name}:{job}[{i}]")

    def test_the_services_of_each_job_are_exactly_the_listed_blocks(self):
        for name in ALL_FILES:
            for job, body in load(name)["jobs"].items():
                self.assertEqual(body.get("services"), SERVICES.get((name, job)), f"{name}:{job}")

    def test_the_defaults_of_each_job_are_exactly_the_listed_ones_in_every_file(self):
        for name in ALL_FILES:
            doc = load(name)
            self.assertNotIn("defaults", doc, name)
            for job, body in doc["jobs"].items():
                self.assertEqual(body.get("defaults"), JOB_DEFAULTS.get((name, job)), f"{name}:{job}")
                self.assertNotIn("shell", (body.get("defaults") or {}).get("run", {}), f"{name}:{job}")

    def test_no_table_names_a_step_or_a_job_that_does_not_exist(self):
        docs = {name: load(name) for name in ALL_FILES}
        for table in (JOB_ENV, SERVICES, JOB_DEFAULTS):
            for (name, job) in table:
                self.assertIn(job, docs[name]["jobs"])
        for table in (STEP_ENV, STEP_WORKDIR):
            for (name, job, i) in table:
                self.assertLess(i, len(docs[name]["jobs"][job]["steps"]), (name, job, i))

    def test_no_name_that_changes_how_a_process_starts_is_set_anywhere(self):
        for name in ALL_FILES:
            for where, env in all_env_dicts(load(name)):
                for var in env:
                    self.assertIsNone(FORBIDDEN_ENV.match(var), f"{name}: {var} in {where}")
        for table in (JOB_ENV, STEP_ENV):
            for key, env in table.items():
                for var in env:
                    self.assertIsNone(FORBIDDEN_ENV.match(var), f"{key}: {var}")
        for var in WORKFLOW_ENV.get("ci.yml", {}):
            self.assertIsNone(FORBIDDEN_ENV.match(var), var)

    def test_the_checker_refuses_the_names_it_is_for(self):
        for var in ("BASH_ENV", "ENV", "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PYTHONSTARTUP", "NODE_OPTIONS", "RUSTC_WRAPPER", "BASH_FUNC_x%%", "GIT_SSH_COMMAND"):
            self.assertIsNotNone(FORBIDDEN_ENV.match(var), var)
        for var in ("PLAN", "NEEDS", "DATABASE_URL", "CARGO_TERM_COLOR", "TAG", "PATHS"):
            self.assertIsNone(FORBIDDEN_ENV.match(var), var)

    def test_a_run_writes_only_the_variable_it_is_meant_to_to_the_environment_file(self):
        for name in ALL_FILES:
            for job, body in load(name)["jobs"].items():
                for step in body["steps"]:
                    run = step.get("run", "")
                    if "GITHUB_ENV" in run or "GITHUB_PATH" in run:
                        self.assertEqual((name, job), ("release-dashboard.yml", "sign-dry-run"), f"{name}:{job}")
                        self.assertNotIn("GITHUB_PATH", run)
                        names = re.findall(r'echo "([A-Za-z0-9_]+)=', run)
                        self.assertEqual(names, ["GLYNDOR_RELEASE_ED25519_KEY"])

    def test_the_environment_of_a_job_is_asked_for_by_the_release_job_and_by_no_other(self):
        for name in ALL_FILES:
            for job, body in load(name)["jobs"].items():
                want = "release" if (name, job) == ("release-dashboard.yml", "release") else None
                self.assertEqual(body.get("environment"), want, f"{name}:{job}")

    def test_the_release_job_checks_the_event_and_the_ref_in_shell_before_it_trusts_anything(self):
        steps = load("release-dashboard.yml")["jobs"]["release"]["steps"]
        check = [s for s in steps if "RUN_SHA" in s.get("env", {})][0]
        self.assertEqual(check["env"]["EVENT"], "${{ github.event_name }}")
        self.assertEqual(check["env"]["REF"], "${{ github.ref }}")
        run = check["run"]
        self.assertIn('case "$EVENT:$REF" in', run)
        self.assertIn("push:refs/tags/dashboard@* | workflow_dispatch:refs/tags/dashboard@* | workflow_dispatch:refs/heads/main) ;;", run)
        self.assertLess(run.index('case "$EVENT:$REF"'), run.index("git fetch"))
        order = [("RUN_SHA" in s.get("env", {})) for s in steps]
        self.assertEqual(order.index(True), 1)
        self.assertEqual(steps[0]["with"]["fetch-depth"], "0")
        self.assertNotIn("--depth", run)
        self.assertIn('git fetch --force --no-tags origin main:refs/remotes/origin/main', run)
        self.assertIn('if ! git merge-base --is-ancestor "$RUN_SHA" origin/main; then', run)
        key_step = [i for i, s in enumerate(steps) if "GLYNDOR_RELEASE_ED25519_KEY" in s.get("env", {})][0]
        self.assertGreater(key_step, 1)


class TheReleaseGuardRunsAgainstARealRepository(unittest.TestCase):
    """The text of the guard step, run for real against a local origin: a tag on a commit main
    holds passes; a tag on a commit main does not hold, a tag that names another commit and a ref
    that is not a release tag or main fail."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.origin = base / "origin"
        self.work = base / "work"
        self.git(base, "init", "-q", "-b", "main", str(self.origin))
        self.commit(self.origin, "one")
        self.commit(self.origin, "two")
        self.main_sha = self.rev(self.origin, "main")
        self.git(self.origin, "checkout", "-q", "-b", "side")
        self.commit(self.origin, "only on side")
        self.side_sha = self.rev(self.origin, "side")
        self.git(self.origin, "checkout", "-q", "main")
        self.git(self.origin, "tag", "dashboard@1.0.0", self.main_sha)
        self.git(self.origin, "tag", "dashboard@2.0.0", self.side_sha)
        self.git(self.origin, "tag", "dashboard@0.9.0", self.rev(self.origin, "main~1"))
        step = [s for s in load("release-dashboard.yml")["jobs"]["release"]["steps"] if "RUN_SHA" in s.get("env", {})][0]
        self.script = step["run"]

    def tearDown(self):
        self.tmp.cleanup()

    # No configuration of the person running the tests (signing of commits and tags, for one).
    CLEAN = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}

    @classmethod
    def git(cls, cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=cls.CLEAN)

    @classmethod
    def rev(cls, cwd, ref):
        return subprocess.run(["git", "-C", str(cwd), "rev-parse", ref], check=True, capture_output=True, text=True, env=cls.CLEAN).stdout.strip()

    def commit(self, cwd, text):
        self.git(cwd, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", text)

    def run_guard(self, event, ref, tag, sha, shallow=True):
        if self.work.exists():
            subprocess.run(["rm", "-rf", str(self.work)], check=True)
        args = ["clone", "-q", "--no-tags", f"file://{self.origin}", str(self.work)]
        subprocess.run(["git", *args], check=True, capture_output=True, env=self.CLEAN)
        self.git(self.work, "checkout", "-q", "--detach", sha)
        env = {**self.CLEAN, "TAG": tag, "RUN_SHA": sha, "EVENT": event, "REF": ref}
        # the real checkout asks for the whole history, and so does this clone
        script = "git remote set-url origin file://" + str(self.origin) + "\n" + self.script
        done = subprocess.run(["bash", "-c", script], cwd=self.work, env=env, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_a_tag_on_a_commit_main_holds_passes(self):
        code, out = self.run_guard("push", "refs/tags/dashboard@1.0.0", "dashboard@1.0.0", self.main_sha)
        self.assertEqual(code, 0, out)

    def test_an_older_commit_of_main_passes_when_the_tag_names_it(self):
        old = self.rev(self.origin, "main~1")
        code, out = self.run_guard("push", "refs/tags/dashboard@0.9.0", "dashboard@0.9.0", old)
        self.assertEqual(code, 0, out)

    def test_a_tag_on_a_commit_main_does_not_hold_fails(self):
        code, out = self.run_guard("push", "refs/tags/dashboard@2.0.0", "dashboard@2.0.0", self.side_sha)
        self.assertEqual(code, 1, out)
        self.assertIn("is not on main", out)

    def test_a_run_whose_commit_is_not_the_one_the_tag_names_fails(self):
        code, out = self.run_guard("push", "refs/tags/dashboard@1.0.0", "dashboard@1.0.0", self.side_sha)
        self.assertEqual(code, 1, out)
        self.assertIn("start the run from the tag", out)

    def test_a_manual_run_on_main_with_the_tag_of_main_passes_and_with_another_fails(self):
        self.assertEqual(self.run_guard("workflow_dispatch", "refs/heads/main", "dashboard@1.0.0", self.main_sha)[0], 0)
        self.assertEqual(self.run_guard("workflow_dispatch", "refs/heads/main", "dashboard@0.9.0", self.main_sha)[0], 1)

    def test_a_branch_other_than_main_or_another_event_fails_before_anything_is_fetched(self):
        for event, ref in (("workflow_dispatch", "refs/heads/side"), ("push", "refs/heads/main"), ("pull_request", "refs/pull/1/merge"), ("workflow_dispatch", "refs/tags/v1.0.0")):
            with self.subTest(event=event, ref=ref):
                code, out = self.run_guard(event, ref, "dashboard@1.0.0", self.main_sha)
                self.assertEqual(code, 1, out)
                self.assertIn("a release is made from", out)


if __name__ == "__main__":
    unittest.main()
