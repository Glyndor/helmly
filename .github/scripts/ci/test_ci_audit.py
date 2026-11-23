"""Tests of the dependency audits: the ones that run with every change, the scheduled one and
the script that takes the lockfiles of the audited branch.

    python3 .github/scripts/ci/test_ci_audit.py
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_common import ROOT, WORKFLOW, ci_text, workflow_files  # noqa: E402
from wiring import split_jobs, strip_comments  # noqa: E402

class AuditsRunEverywhere(unittest.TestCase):
    def test_the_audits_run_on_every_pull_request_and_every_push_to_develop(self):
        top = ci_text().split("\njobs:\n", 1)[0]
        self.assertRegex(top, r"pull_request:\n    branches: \[develop, main\]")
        self.assertRegex(top, r"push:\n    branches: \[develop\]")

    def audit_jobs(self):
        text = (ROOT / ".github/workflows/audit.yml").read_text(encoding="utf-8")
        jobs = split_jobs(strip_comments(text))
        self.assertEqual(set(jobs), {"cargo", "bun"})
        return jobs

    def test_the_scheduled_audit_covers_develop_and_main(self):
        text = strip_comments((ROOT / ".github/workflows/audit.yml").read_text(encoding="utf-8"))
        self.assertIn("schedule:", text)
        self.assertNotIn("pull_request", text)
        self.assertEqual(len(re.findall(r"ref: \[develop, main\]", text)), 2)

    def test_each_audit_job_has_one_checkout_the_events_and_no_cache_and_no_local_action(self):
        for name, body in self.audit_jobs().items():
            self.assertEqual(len(re.findall(r"uses: actions/checkout@", body)), 1, f"{name}: a second checkout")
            self.assertEqual(len(re.findall(r"\buses: ", body)), 1, f"{name}: another action")
            checkout = re.search(r"uses: actions/checkout@\S+ # v[\d.]+\n        with:\n((?:          .*\n)+)", body)
            self.assertEqual([l.strip() for l in checkout.group(1).splitlines()], ["persist-credentials: false"], name)
            for forbidden in ("path:", "actions/cache", "cache:", "uses: ./", "setup-", "cargo install", "bun install"):
                self.assertNotIn(forbidden, body, f"{name}: {forbidden}")
            self.assertNotRegex(body, r"^\s+ref: ", f"{name}: a ref is checked out")
            for line in re.findall(r"^\s+run: (.*)$", body, re.M):
                self.assertNotRegex(line, r"audited/", f"{name}: a command reads a tree of the audited branch")

    def test_the_audited_branch_reaches_the_job_only_through_env_and_the_fetch_script(self):
        for name, body in self.audit_jobs().items():
            self.assertEqual(body.count("${{ matrix.ref }}"), 2, name)  # the job name and the env below
            self.assertIn("        env:\n          AUDITED: ${{ matrix.ref }}\n", body, name)
            self.assertIn("fetch-audit-data.sh", body, name)
            self.assertIn('working-directory: ${{ runner.temp }}/clean', body, name)

    def test_the_tools_run_from_the_clean_directory_on_the_lockfile_there(self):
        jobs = self.audit_jobs()
        self.assertIn(
            'run: cargo-audit audit --deny warnings --file "$RUNNER_TEMP/clean/Cargo.lock"',
            jobs["cargo"],
        )
        self.assertIn("run: bun audit --audit-level high", jobs["bun"])

    def test_every_cargo_audit_denies_warnings_and_none_ignores_anything_on_its_command_line(self):
        found = 0
        for path in (
            ROOT / ".github/workflows/ci.yml",
            ROOT / ".github/workflows/audit.yml",
            ROOT / ".github/workflows/release-dashboard.yml",
        ):
            for line in re.findall(r"^\s+run: (.*cargo-audit.*|.*cargo audit.*)$", path.read_text(encoding="utf-8"), re.M):
                if "install" in line:
                    continue
                found += 1
                self.assertIn("--deny warnings", line, f"{path.name}: {line}")
                self.assertNotIn("--ignore", line, f"{path.name}: {line}")
        self.assertEqual(found, 3)  # ci.yml, the scheduled one and the release's

    def test_what_the_audits_accept_is_written_once_in_the_audit_configuration(self):
        text = (ROOT / "internal/.cargo/audit.toml").read_text(encoding="utf-8")
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        self.assertEqual(re.findall(r'"(RUSTSEC-\d{4}-\d{4})"', code), ["RUSTSEC-2026-0543"])
        comment = "\n".join(l for l in text.splitlines() if l.lstrip().startswith("#"))
        for word in ("chrono", "unmaintained", "no version is patched", "jiff", "entry goes"):
            self.assertIn(word, comment)
        # the scheduled audit takes the file from the audited branch, like the lockfile
        self.assertIn('optional=("internal/.cargo/audit.toml")', (ROOT / ".github/scripts/ci/fetch-audit-data.sh").read_text(encoding="utf-8"))
        # and the audit of ci.yml runs in the directory that holds it
        audit = split_jobs(strip_comments(WORKFLOW.read_text(encoding="utf-8")))["audit"]
        self.assertIn("working-directory: internal\n", audit)

    def test_the_bun_command_that_exists_on_the_runner_is_used(self):
        # install-bun.sh puts `bun` on the path and nothing else, so `bunx` is not there.
        for path in workflow_files():
            self.assertNotRegex(path.read_text(encoding="utf-8"), r"\bbunx\b", path.name)


class FetchAuditData(unittest.TestCase):
    """The script that takes the lockfiles of the audited branch, run against a throwaway
    repository: it must fail on a missing file, an empty one, a link and a branch outside
    the list, and copy nothing but the lockfiles (and the audit configuration)."""

    SCRIPT = ROOT / ".github/scripts/ci/fetch-audit-data.sh"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        self.origin = base / "origin"
        self.work = base / "work"
        self.temp = base / "temp"
        self.temp.mkdir()
        self.git(base, "init", "-q", "-b", "develop", str(self.origin))
        self.git(self.origin, "config", "user.email", "ci@example.invalid")
        self.git(self.origin, "config", "user.name", "ci")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True, text=True).stdout.strip()

    def commit(self, files: "dict[str, str]", links: "dict[str, str] | None" = None):
        for rel, text in files.items():
            path = self.origin / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for rel, target in (links or {}).items():
            path = self.origin / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        self.git(self.origin, "add", "-A")
        self.git(self.origin, "commit", "-q", "-m", "x")
        self.git(self.origin, "branch", "-f", "main", "HEAD")
        if not self.work.exists():
            self.git(self.temp.parent, "clone", "-q", "--depth=1", str(self.origin), str(self.work))
            self.git(self.work, "remote", "set-url", "origin", f"file://{self.origin}")

    def run_script(self, kind: str, branch: str = "develop"):
        env = {**os.environ, "RUNNER_TEMP": str(self.temp), "AUDITED": branch}
        done = subprocess.run(["bash", str(self.SCRIPT), kind], env=env, cwd=self.work, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def clean(self) -> "list[str]":
        root = self.temp / "clean"
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())

    def test_it_takes_the_cargo_lockfile_and_nothing_else(self):
        self.commit({
            "internal/Cargo.lock": "lock\n",
            "internal/Cargo.toml": "toml\n",
            "internal/.cargo/config.toml": "[build]\nrustc-wrapper = 'x'\n",
            "internal/rust-toolchain.toml": "t\n",
        })
        self.assertEqual(self.run_script("cargo")[0], 0)
        self.assertEqual(self.clean(), ["Cargo.lock"])

    def test_it_takes_the_audit_configuration_when_the_branch_has_one(self):
        self.commit({"internal/Cargo.lock": "lock\n", "internal/.cargo/audit.toml": "[advisories]\n"})
        self.assertEqual(self.run_script("cargo")[0], 0)
        self.assertEqual(self.clean(), [".cargo/audit.toml", "Cargo.lock"])

    def test_it_takes_the_bun_manifest_and_lockfile_and_no_configuration(self):
        self.commit({
            "internal/dashboard/ui/package.json": "{}\n",
            "internal/dashboard/ui/bun.lock": "l\n",
            "internal/dashboard/ui/bunfig.toml": "preload = ['x']\n",
            "internal/dashboard/ui/.npmrc": "x\n",
        })
        self.assertEqual(self.run_script("bun")[0], 0)
        self.assertEqual(self.clean(), ["bun.lock", "package.json"])

    def test_it_reads_the_branch_it_was_told_to(self):
        self.commit({"internal/Cargo.lock": "develop\n"})
        (self.origin / "internal/Cargo.lock").write_text("main\n")
        self.git(self.origin, "checkout", "-q", "-b", "other")
        self.git(self.origin, "commit", "-q", "-am", "main")
        self.git(self.origin, "branch", "-f", "main", "HEAD")
        self.assertEqual(self.run_script("cargo", "main")[0], 0)
        self.assertEqual((self.temp / "clean/Cargo.lock").read_text(), "main\n")

    def test_the_workspace_is_left_as_it_was(self):
        self.commit({"internal/Cargo.lock": "develop\n"})
        (self.origin / "internal/Cargo.lock").write_text("main\n")
        self.git(self.origin, "checkout", "-q", "-b", "other")
        self.git(self.origin, "commit", "-q", "-am", "main")
        self.git(self.origin, "branch", "-f", "main", "HEAD")
        self.assertEqual(self.run_script("cargo", "main")[0], 0)
        self.assertEqual((self.work / "internal/Cargo.lock").read_text(), "develop\n")
        self.assertEqual(self.git(self.work, "status", "--porcelain"), "")

    def test_the_script_reads_the_branch_with_git_show_and_never_checks_it_out(self):
        text = "\n".join(l for l in self.SCRIPT.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        used = set(re.findall(r"\bgit (?:-C \S+ )?([a-z-]+)", text))
        self.assertEqual(used, {"fetch", "ls-tree", "show"})

    def test_a_missing_lockfile_fails(self):
        self.commit({"internal/other": "x\n"})
        code, out = self.run_script("cargo")
        self.assertEqual(code, 1)
        self.assertIn("has no internal/Cargo.lock", out)

    def test_an_empty_lockfile_fails(self):
        self.commit({"internal/Cargo.lock": ""})
        code, out = self.run_script("cargo")
        self.assertEqual(code, 1)
        self.assertIn("is empty", out)

    def test_a_lockfile_that_is_a_link_fails(self):
        self.commit({"elsewhere.lock": "x\n"}, links={"internal/Cargo.lock": "../elsewhere.lock"})
        code, out = self.run_script("cargo")
        self.assertEqual(code, 1)
        self.assertIn("is a link", out)

    def test_a_bun_lockfile_that_is_a_link_fails(self):
        self.commit({"internal/dashboard/ui/package.json": "{}\n", "x": "y\n"}, links={"internal/dashboard/ui/bun.lock": "../../../x"})
        self.assertEqual(self.run_script("bun")[0], 1)

    def test_a_branch_outside_the_list_fails_before_anything_is_fetched(self):
        self.commit({"internal/Cargo.lock": "lock\n"})
        code, out = self.run_script("cargo", "feature/x")
        self.assertEqual(code, 1)
        self.assertFalse((self.temp / "clean").exists())

    def test_a_stale_clean_directory_is_emptied_first(self):
        (self.temp / "clean").mkdir()
        (self.temp / "clean" / "bunfig.toml").write_text("x")
        self.commit({"internal/Cargo.lock": "lock\n"})
        self.assertEqual(self.run_script("cargo")[0], 0)
        self.assertEqual(self.clean(), ["Cargo.lock"])

    def test_an_unknown_kind_fails(self):
        self.commit({"internal/Cargo.lock": "lock\n"})
        self.assertEqual(self.run_script("npm")[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
