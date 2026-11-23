"""Tests of the whole-tree checks and of the job that runs them on every pull request.

    python3 .github/scripts/ci/test_ci_tree.py
"""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_common import ROOT, load_yaml  # noqa: E402

SCRIPT = ROOT / ".github/scripts/ci/tree_checks.py"
RETIRED = "APh+kh61dJeT0HzG" + "+KQXELzDjK4ccvqY9K+FptOZ3+Y"


class TreeChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "ci")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=True)

    def add(self, rel: str, data: "str | bytes"):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        self.git("add", rel)

    def commit(self):
        self.git("commit", "-q", "--allow-empty", "-m", "x")

    def run_script(self, script=SCRIPT):
        done = subprocess.run([sys.executable, str(script)], cwd=self.repo, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_a_clean_tree_passes(self):
        self.add("README.md", "nothing here\n")
        self.commit()
        self.assertEqual(self.run_script()[0], 0)

    def test_the_retired_key_in_a_document_a_script_or_a_binary_fails(self):
        for rel, data in (
            ("docs/notes.md", f"the key was {RETIRED}\n"),
            ("scripts/x.sh", f'KEY="{RETIRED}="\n'),
            ("blob.bin", b"\x00\x01" + RETIRED.encode() + b"\xff"),
            ("deep/er/.hidden", RETIRED),
        ):
            with self.subTest(rel):
                self.tearDown()
                self.setUp()
                self.add("README.md", "x\n")
                self.add(rel, data)
                self.commit()
                code, out = self.run_script()
                self.assertEqual(code, 1)
                self.assertIn(rel, out)
                self.assertNotIn(RETIRED, out, "the finding must not print the key")

    def test_what_is_judged_is_the_committed_blob_and_not_the_working_directory(self):
        self.add("README.md", "x\n")
        self.add("notes.md", f"key {RETIRED}\n")
        self.commit()
        (self.repo / "notes.md").write_text("clean now, but not committed\n")
        self.assertEqual(self.run_script()[0], 1)
        self.tearDown()
        self.setUp()
        self.add("notes.md", "clean\n")
        self.commit()
        (self.repo / "notes.md").write_text(f"key {RETIRED}\n")
        self.assertEqual(self.run_script()[0], 0)

    def test_a_file_that_is_only_staged_or_untracked_is_not_the_tree(self):
        self.add("README.md", "x\n")
        self.commit()
        self.add("staged.md", RETIRED)
        (self.repo / "untracked.md").write_text(RETIRED)
        self.assertEqual(self.run_script()[0], 0)

    def test_the_current_key_is_not_a_finding(self):
        self.add("a.md", "HFv7vg5FCY7YyKUDbJhaQSfB9SboJGSblJtFbLmLHzM\n")
        self.commit()
        self.assertEqual(self.run_script()[0], 0)

    def test_an_attribute_that_rewrites_files_on_checkout_is_a_finding(self):
        for line in (
            "*.md working-tree-encoding=UTF-16",
            "*.md filter=clean",
            "docs/* ident",
            "*.md text eol=lf working-tree-encoding=UTF-16LE",
            "*.md -filter",
        ):
            with self.subTest(line):
                self.tearDown()
                self.setUp()
                self.add("README.md", "x\n")
                self.add("sub/.gitattributes", line + "\n")
                self.commit()
                code, out = self.run_script()
                self.assertEqual(code, 1)
                self.assertIn("sub/.gitattributes:1", out)

    def test_attributes_that_do_not_rewrite_anything_are_fine(self):
        self.add(".gitattributes", "# filter and ident are words in this comment\n*.sh text eol=lf\n*.png binary\n*.lock -diff\n")
        self.commit()
        self.assertEqual(self.run_script()[0], 0)

    def test_a_repository_without_a_commit_fails_closed(self):
        self.add("README.md", "x\n")
        self.assertEqual(self.run_script()[0], 2)

    def test_outside_a_repository_it_fails_closed(self):
        with tempfile.TemporaryDirectory() as plain:
            done = subprocess.run([sys.executable, str(SCRIPT)], cwd=plain, capture_output=True, text=True)
            self.assertEqual(done.returncode, 2)

    def test_the_script_does_not_carry_the_key_it_looks_for_and_the_repository_has_none(self):
        self.assertNotIn(RETIRED, SCRIPT.read_text(encoding="utf-8"))
        done = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)

    def test_a_copy_of_the_script_with_the_entry_removed_passes_what_the_original_refuses(self):
        # why the job judges with the copy of the base as well: the tree's own copy can be edited
        weak = self.repo / "weak.py"
        text = SCRIPT.read_text(encoding="utf-8")
        start = text.index("RETIRED_RELEASE_KEYS = ((")
        end = text.index("\n", start)
        weak.write_text(text[:start] + "RETIRED_RELEASE_KEYS = ()" + text[end:])
        self.add("docs.md", RETIRED)
        self.commit()
        self.assertEqual(self.run_script(weak)[0], 0)
        self.assertEqual(self.run_script()[0], 1)


class TheJob(unittest.TestCase):
    def setUp(self):
        self.doc = load_yaml((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        self.job = self.doc["jobs"]["tree"]

    def test_it_has_no_plan_no_condition_and_nothing_to_wait_for(self):
        for key in ("if", "needs", "env", "continue-on-error", "defaults", "strategy"):
            self.assertNotIn(key, self.job)
        self.assertEqual(self.job["name"], "Tree checks")

    def test_the_gate_waits_for_it(self):
        self.assertIn("tree", self.doc["jobs"]["gate"]["needs"])

    def test_it_checks_out_the_whole_history_without_credentials(self):
        first = self.job["steps"][0]
        self.assertEqual(first["with"], {"fetch-depth": "0", "persist-credentials": "false"})

    def test_it_judges_a_pull_request_with_the_copy_of_the_base_and_with_the_copy_of_the_tree(self):
        step = self.job["steps"][1]
        self.assertEqual(step["env"], {"EVENT": "${{ github.event_name }}", "BASE_SHA": "${{ github.event.pull_request.base.sha }}"})
        run = step["run"]
        self.assertIn('git show "$BASE_SHA:.github/scripts/ci/tree_checks.py" > "$tmp/tree_checks.py"', run)
        self.assertIn('python3 "$tmp/tree_checks.py" || bad=1', run)
        self.assertIn("python3 .github/scripts/ci/tree_checks.py || bad=1", run)
        self.assertIn('exit "$bad"', run)
        self.assertEqual(len(self.job["steps"]), 2)

    def test_the_job_runs_the_judge_of_the_base_even_when_the_tree_copy_would_pass(self):
        run = self.job["steps"][1]["run"]
        self.assertLess(run.index("tree_checks.py\" > "), run.index("python3 .github/scripts/ci/tree_checks.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
