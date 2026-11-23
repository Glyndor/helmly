"""The CodeQL configuration leaves out test code and only test code.

What needs the compiler (no file that production is built from matches a pattern) is checked in the
job that builds the server, by `check_paths_ignore.py` and `rust_sources_cases.py`; this file holds
what needs only the tree and a YAML parser: the shape of the configuration, that `codeql.yml` reads
it for every language, that no pattern is dead or reaches a file that is not Rust, that the job that
builds the server runs the two checks, and that the tests of `password.rs` live in a file of their
own. The configuration may hold nothing but a name and the list of paths: no query is filtered, no
suite is changed.

    python3 .github/scripts/codeql/test_codeql_paths.py
"""
import functools
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ci"))

import check_paths_ignore  # noqa: E402
import rust_sources  # noqa: E402
from ci_common import ROOT, load_yaml  # noqa: E402

CONFIG = ROOT / ".github/codeql/codeql-config.yml"
WORKFLOW = ROOT / ".github/workflows/codeql.yml"
CI = ROOT / ".github/workflows/ci.yml"
# Production code that an alert is known to be about, which the analysis has to keep reading.
PRODUCTION_WITH_AN_ALERT = ("internal/dashboard/server/src/main.rs",)


def config():
    return load_yaml(CONFIG.read_text(encoding="utf-8"))


@functools.cache
def tracked():
    return subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT, text=True).split("\0")[:-1]


def hidden_by(pattern):
    return [f for f in tracked() if rust_sources.matches(pattern, f)]


class Configuration(unittest.TestCase):
    def test_it_holds_a_name_and_the_paths_to_ignore_and_nothing_that_narrows_the_queries(self):
        self.assertEqual(set(config()), {"name", "paths-ignore"})
        self.assertIsInstance(config()["paths-ignore"], list)
        self.assertTrue(config()["paths-ignore"])

    def test_the_reader_of_the_check_reads_what_a_yaml_parser_reads(self):
        self.assertEqual(check_paths_ignore.parse_config(CONFIG.read_text(encoding="utf-8")), config())

    def test_the_patterns_use_only_what_the_documentation_supports_and_name_only_rust_tests(self):
        for pattern in config()["paths-ignore"]:
            self.assertIsInstance(pattern, str)
            for char in "?+[]!":
                self.assertNotIn(char, pattern, f"{char!r} is matched literally, not as a pattern")
            for at in [i for i in range(len(pattern)) if pattern.startswith("**", i)]:
                before, after = pattern[at - 1 : at], pattern[at + 2 : at + 3]
                self.assertTrue(before in ("", "/") and after in ("", "/"), f"`**` in {pattern} is not at an end or between slashes")
            self.assertFalse(pattern.startswith("/") or ".." in pattern, pattern)
            self.assertTrue(pattern.startswith("internal/"), f"{pattern} is outside the crates")
            self.assertTrue(pattern.endswith((".rs", "/**")), f"{pattern} would leave out more than Rust test code")

    def test_the_workflow_reads_the_configuration_for_every_language(self):
        doc = load_yaml(WORKFLOW.read_text(encoding="utf-8"))
        steps = doc["jobs"]["analyze"]["steps"]
        inits = [s for s in steps if str(s.get("uses", "")).startswith("github/codeql-action/init@")]
        self.assertEqual(len(inits), 1)
        self.assertEqual(inits[0]["with"]["config-file"], "./.github/codeql/codeql-config.yml")
        self.assertEqual(inits[0]["with"]["languages"], "${{ matrix.language }}")
        self.assertEqual(inits[0]["with"]["build-mode"], "${{ matrix.build-mode }}")
        for entry in doc["jobs"]["analyze"]["strategy"]["matrix"]["include"]:
            self.assertEqual(entry["build-mode"], "none", "paths-ignore is documented for an analysis that does not build the code")

    def test_nothing_else_of_the_codeql_setup_leaves_anything_out(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for word in ("query-filters", "paths-ignore", "queries:", "packs:", "threat-models"):
            self.assertNotIn(word, text)


class Measured(unittest.TestCase):
    def test_every_pattern_matches_something_so_none_is_dead(self):
        for pattern in config()["paths-ignore"]:
            self.assertTrue(hidden_by(pattern), f"{pattern} matches nothing")

    def test_what_a_pattern_reaches_is_rust(self):
        for pattern in config()["paths-ignore"]:
            for f in hidden_by(pattern):
                self.assertTrue(f.endswith(".rs"), f"{pattern} reaches {f}, which is not Rust")

    def test_the_code_a_known_production_alert_is_in_is_not_matched(self):
        for path in PRODUCTION_WITH_AN_ALERT:
            self.assertIn(path, tracked())
            for pattern in config()["paths-ignore"]:
                self.assertFalse(rust_sources.matches(pattern, path), f"{pattern} hides {path}")

    def test_the_job_that_builds_the_server_runs_the_checks_against_what_the_compiler_read(self):
        steps = load_yaml(CI.read_text(encoding="utf-8"))["jobs"]["clippy"]["steps"]
        run = [s for s in steps if "check_paths_ignore.py" in s.get("run", "")]
        self.assertEqual(len(run), 1)
        self.assertEqual(run[0]["if"], "env.PLAN != 'false'")
        self.assertEqual(run[0]["run"], "python3 -I .github/scripts/codeql/check_paths_ignore.py")
        clippy = next(i for i, s in enumerate(steps) if "cargo clippy" in s.get("run", ""))
        self.assertGreater(steps.index(run[0]), clippy, "after the build that fills the target directory")

    def test_the_checks_look_at_the_production_targets_with_and_without_all_features_and_no_tests(self):
        text = (HERE / "check_paths_ignore.py").read_text(encoding="utf-8")
        self.assertIn('"--lib", "--bins", "--message-format=json"', text)
        self.assertIn('("--all-features",)', text)
        command = next(line for line in text.splitlines() if '"cargo", "check"' in line)
        for flag in ("--tests", "--all-targets", "--test", "--benches", "--examples"):
            self.assertNotIn(flag, command)

    def test_a_production_file_that_had_tests_with_vectors_keeps_them_in_a_file_of_its_own(self):
        path = "internal/dashboard/server/src/crypto/password/tests.rs"
        self.assertIn(path, tracked())
        self.assertTrue(any(rust_sources.matches(p, path) for p in config()["paths-ignore"]), path)
        production = (ROOT / "internal/dashboard/server/src/crypto/password.rs").read_text(encoding="utf-8")
        self.assertNotIn("mod tests {", production)
        self.assertIn("#[cfg(test)]\nmod tests;", production)


if __name__ == "__main__":
    unittest.main()
