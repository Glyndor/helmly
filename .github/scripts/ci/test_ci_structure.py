"""Tests of structure.py (file length and folder size) and of how the tree job runs it.

    python3 .github/scripts/ci/test_ci_structure.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ci_common import ROOT, load_yaml  # noqa: E402

SCRIPT = ROOT / ".github/scripts/ci/structure.py"
NONE = {"files": {}, "folders": {}}


class Structure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.git("init", "-q", "-b", "develop")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=True)

    def add(self, rel: str, text: str = "x\n"):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git("add", rel)

    def lines(self, rel: str, code: int, comments=0, blanks=0, marker="#"):
        body = "".join("x = 1\n" for _ in range(code)) + "".join(f"{marker} c\n" for _ in range(comments)) + "\n" * blanks
        self.add(rel, body)

    def run_script(self, exceptions=NONE, raw: "str | None" = None):
        path = self.repo / "exceptions.json"
        path.write_text(raw if raw is not None else json.dumps(exceptions))
        done = subprocess.run([sys.executable, str(SCRIPT), "--exceptions", str(path)], cwd=self.repo, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr

    def test_a_file_of_500_code_lines_fits_and_one_of_501_does_not(self):
        self.lines("a.py", 500)
        self.assertEqual(self.run_script()[0], 0)
        self.lines("b.py", 501)
        code, out = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("b.py has 501", out)

    def test_comments_and_blank_lines_do_not_count(self):
        self.lines("a.py", 500, comments=300, blanks=300)
        self.lines("b.rs", 500, comments=40, blanks=9, marker="//")
        self.lines("c.sh", 500, comments=40, blanks=9)
        self.assertEqual(self.run_script()[0], 0)

    def test_only_rust_shell_and_python_files_are_measured(self):
        self.lines("big.md", 900)
        self.lines("big.tsx", 900)
        self.assertEqual(self.run_script()[0], 0)

    def test_a_folder_of_20_files_fits_and_one_of_21_does_not(self):
        for i in range(20):
            self.add(f"d/f{i}.txt")
        self.assertEqual(self.run_script()[0], 0)
        self.add("d/f20.txt")
        code, out = self.run_script()
        self.assertEqual(code, 1)
        self.assertIn("folder d has 21", out)

    def test_the_folders_the_tools_fix_are_not_counted(self):
        for i in range(30):
            self.add(f".github/workflows/w{i}.yml")
            self.add(f"internal/.sqlx/q{i}.json")
            self.add(f"x/y/.sqlx/q{i}.json")
            self.add(f"a/testdata/t{i}.txt")
        self.assertEqual(self.run_script()[0], 0)

    def test_an_exception_with_a_ceiling_and_a_reason_lets_the_subject_through(self):
        self.lines("a.py", 520)
        exceptions = {"files": {"a.py": {"ceiling": 520, "reason": "one installer file"}}, "folders": {}}
        self.assertEqual(self.run_script(exceptions)[0], 0)

    def test_an_exception_does_not_let_the_subject_grow(self):
        self.lines("a.py", 530)
        exceptions = {"files": {"a.py": {"ceiling": 520, "reason": "r"}}, "folders": {}}
        code, out = self.run_script(exceptions)
        self.assertEqual(code, 1)
        self.assertIn("over its ceiling of 520", out)

    def test_an_exception_needs_a_reason_and_an_integer_ceiling(self):
        self.lines("a.py", 520)
        for entry in ({"ceiling": 520, "reason": " "}, {"ceiling": "520", "reason": "r"}, {"reason": "r"}):
            code, out = self.run_script({"files": {"a.py": entry}, "folders": {}})
            self.assertEqual(code, 1, entry)
            self.assertIn("needs an integer ceiling and a written reason", out)

    def test_a_stale_exception_is_an_error(self):
        self.lines("a.py", 400)
        code, out = self.run_script({"files": {"a.py": {"ceiling": 520, "reason": "r"}}, "folders": {}})
        self.assertEqual(code, 1)
        self.assertIn("is stale", out)

    def test_a_ceiling_far_above_the_real_number_must_come_down(self):
        self.lines("a.py", 520)
        code, out = self.run_script({"files": {"a.py": {"ceiling": 700, "reason": "r"}}, "folders": {}})
        self.assertEqual(code, 1)
        self.assertIn("lower the ceiling to 520", out)

    def test_an_exception_for_something_that_is_not_tracked_is_an_error(self):
        self.lines("a.py", 10)
        code, out = self.run_script({"files": {"gone.py": {"ceiling": 520, "reason": "r"}}, "folders": {"nowhere": {"ceiling": 30, "reason": "r"}}})
        self.assertEqual(code, 1)
        self.assertIn("names a file that is not tracked", out)
        self.assertIn("names a folder with no tracked files", out)

    def test_a_folder_exception_works_like_a_file_one(self):
        for i in range(25):
            self.add(f"d/f{i}.txt")
        self.assertEqual(self.run_script({"files": {}, "folders": {"d": {"ceiling": 25, "reason": "r"}}})[0], 0)
        self.add("d/f25.txt")
        self.assertEqual(self.run_script({"files": {}, "folders": {"d": {"ceiling": 25, "reason": "r"}}})[0], 1)

    def test_an_unreadable_or_odd_exceptions_file_is_an_error_of_its_own(self):
        self.add("a.txt")
        for raw in ("not json", "[]", json.dumps({"files": {}, "folders": {}, "other": {}})):
            self.assertEqual(self.run_script(raw=raw)[0], 2, raw)
        done = subprocess.run([sys.executable, str(SCRIPT), "--exceptions", str(self.repo / "missing.json")], cwd=self.repo, capture_output=True)
        self.assertEqual(done.returncode, 2)

    def test_outside_a_repository_it_fails_closed(self):
        with tempfile.TemporaryDirectory() as plain:
            (pathlib.Path(plain) / "e.json").write_text(json.dumps(NONE))
            done = subprocess.run([sys.executable, str(SCRIPT), "--exceptions", str(pathlib.Path(plain) / "e.json")], cwd=plain, capture_output=True)
            self.assertEqual(done.returncode, 2)

    def test_this_repository_fits_with_its_exceptions_and_each_is_in_use(self):
        done = subprocess.run([sys.executable, str(SCRIPT), "--exceptions", str(ROOT / ".github/structure.json")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)


class TheJobJudgesWithTheBase(unittest.TestCase):
    def setUp(self):
        doc = load_yaml((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        self.run_text = doc["jobs"]["tree"]["steps"][1]["run"]

    def test_the_structure_check_runs_with_the_script_and_the_exceptions_of_the_base_and_then_of_the_tree(self):
        run = self.run_text
        self.assertIn('if from_base .github/scripts/ci/structure.py "$tmp/structure.py"; then', run)
        self.assertIn('from_base .github/structure.json "$tmp/structure.json" ||', run)
        self.assertIn('python3 -I "$tmp/structure.py" --exceptions "$tmp/structure.json" || bad=1', run)
        self.assertIn("python3 -I .github/scripts/ci/structure.py --exceptions .github/structure.json || bad=1", run)
        self.assertLess(run.index('"$tmp/structure.py" --exceptions'), run.index("python3 -I .github/scripts/ci/structure.py --exceptions"))

    def test_a_base_without_the_script_or_without_the_exceptions_is_judged_by_the_tree_alone(self):
        self.assertIn("the base commit has no structure check yet", self.run_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
