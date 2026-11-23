#!/usr/bin/env python3
"""The reading of the compiler's dep-info and the checks built on it, tried on small crates that are
really compiled, in both directions: what production reads is found, whatever brought it in, and
what only tests read is not. Run where the toolchain is (the job that builds the server).

    python3 -I .github/scripts/codeql/rust_sources_cases.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_paths_ignore  # noqa: E402
import rust_sources  # noqa: E402

CARGO = '[package]\nname = "probe"\nversion = "0.1.0"\nedition = "2021"\n'


def build(files, extra=()):
    """Create the crate in a temporary directory, run `cargo check` for what production is built from
    and return (the files production read, the directory)."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    env = {**os.environ, "CARGO_TARGET_DIR": str(root / "target")}
    kinds = [flag for flag, file in (("--lib", "src/lib.rs"), ("--bins", "src/main.rs")) if (root / file).is_file()]
    done = subprocess.run(
        ["cargo", "check", "--offline", "--message-format=json", *kinds, *extra],
        cwd=root, env=env, capture_output=True, text=True,
    )
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return rust_sources.production_files(done.stdout, root, root), tmp


class WhatTheCompilerRead(unittest.TestCase):
    def read(self, files):
        found, tmp = build({"Cargo.toml": CARGO, **files})
        self.addCleanup(tmp.cleanup)
        return found

    def test_a_module_that_production_declares_is_read_and_a_cfg_test_one_is_not(self):
        found = self.read({"src/lib.rs": "mod a;\n#[cfg(test)]\nmod tests;\n", "src/a.rs": "", "src/tests.rs": ""})
        self.assertEqual(found, {"src/lib.rs", "src/a.rs"})

    def test_an_integration_test_is_not_read(self):
        found = self.read({"src/lib.rs": "", "tests/one.rs": "mod helpers;\n", "tests/helpers.rs": ""})
        self.assertEqual(found, {"src/lib.rs"})

    def test_an_include_of_a_file_under_tests_is_read(self):
        found = self.read({"src/lib.rs": 'include!("../tests/shared.rs");\n', "tests/shared.rs": "pub fn shared() {}\n"})
        self.assertEqual(found, {"src/lib.rs", "tests/shared.rs"})

    def test_a_path_chosen_by_cfg_attr_is_read(self):
        found = self.read({
            "src/lib.rs": '#[cfg_attr(not(test), path = "elsewhere/m.rs")]\nmod m;\n',
            "src/elsewhere/m.rs": "",
        })
        self.assertEqual(found, {"src/lib.rs", "src/elsewhere/m.rs"})

    def test_a_path_attribute_under_cfg_test_is_not_read(self):
        found = self.read({"src/lib.rs": '#[cfg(test)]\n#[path = "../tests/m.rs"]\nmod m;\n', "tests/m.rs": ""})
        self.assertEqual(found, {"src/lib.rs"})

    def test_a_build_script_with_another_name_is_read_with_what_it_declares(self):
        found = self.read({
            "Cargo.toml": CARGO + 'build = "other.rs"\n',
            "other.rs": "mod support;\nfn main() {}\n",
            "support.rs": "",
            "src/lib.rs": "",
        })
        self.assertEqual(found, {"src/lib.rs", "other.rs", "support.rs"})

    def test_a_binary_and_what_it_declares_are_read(self):
        found = self.read({"src/main.rs": "mod cli;\nfn main() {}\n", "src/cli.rs": ""})
        self.assertEqual(found, {"src/main.rs", "src/cli.rs"})

    def test_a_declaration_in_a_string_or_a_comment_declares_nothing(self):
        found = self.read({"src/lib.rs": '// mod ghost;\nconst T: &str = r#"\nmod ghost2;\n"#;\nmod real;\n', "src/real.rs": ""})
        self.assertEqual(found, {"src/lib.rs", "src/real.rs"})


class TheReading(unittest.TestCase):
    def test_a_dep_info_is_read_with_its_escapes_and_its_continuations(self):
        text = "# env-dep:X=1\nout.rmeta: src/lib.rs src/a\\ b.rs \\\n src/c.rs\nsrc/lib.rs:\nsrc/c.rs:\n"
        self.assertEqual(rust_sources.parse_dep_info(text), ["src/lib.rs", "src/a b.rs", "src/c.rs"])
        self.assertEqual(rust_sources.parse_dep_info(""), [])
        self.assertEqual(rust_sources.parse_dep_info("# nothing\n"), [])

    def artifact(self, root, src, filenames, test=False):
        return json.dumps({"reason": "compiler-artifact", "target": {"src_path": str(root / src)},
                           "profile": {"test": test}, "filenames": [str(root / f) for f in filenames]})

    def test_a_missing_or_an_empty_dep_info_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src/lib.rs").write_text("")
            line = self.artifact(root, "src/lib.rs", ["libx.rmeta"])
            with self.assertRaises(ValueError):
                rust_sources.production_files(line, root, root)  # no dep-info beside it
            (root / "x.d").write_text("")
            with self.assertRaises(ValueError):
                rust_sources.production_files(line, root, root)  # an empty one
            (root / "x.d").write_text("libx.rmeta: src/lib.rs\n")
            self.assertEqual(rust_sources.production_files(line, root, root), {"src/lib.rs"})

    def test_no_artifact_of_the_repository_and_a_test_unit_are_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                rust_sources.production_files("", root, root)
            (root / "x.d").write_text("libx.rmeta: src/lib.rs\n")
            with self.assertRaises(ValueError):
                rust_sources.production_files(self.artifact(root, "src/lib.rs", ["libx.rmeta"], test=True), root, root)

    def test_the_patterns_mean_what_the_documentation_says(self):
        m = rust_sources.matches
        self.assertTrue(m("internal/**/tests/**", "internal/a/b/tests/c/d.rs"))
        self.assertTrue(m("internal/**/tests/**", "internal/a/tests/c.rs"))
        self.assertFalse(m("internal/**/tests/**", "internal/a/tests.rs"))
        self.assertFalse(m("internal/**/tests/**", "internal/a/contests/c.rs"))
        self.assertTrue(m("internal/**/tests.rs", "internal/a/b/tests.rs"))
        self.assertFalse(m("internal/**/tests.rs", "internal/a/b/mytests.rs"))
        self.assertTrue(m("internal/**/*_tests.rs", "internal/a/update/release_key_tests.rs"))
        self.assertFalse(m("internal/**/*_tests.rs", "internal/a/update/tests.rs"))
        self.assertFalse(m("internal/*.rs", "internal/a/b.rs"))
        self.assertFalse(m("internal/**/tests/**", "other/internal/a/tests/c.rs"))
        self.assertFalse(m("a?.rs", "ab.rs"))
        self.assertTrue(m("a?.rs", "a?.rs"))
        self.assertFalse(m("[ab].rs", "a.rs"))


class TheChecks(unittest.TestCase):
    TRACKED = ["internal/x/src/lib.rs", "internal/x/src/main.rs", "internal/x/tests/a.rs", "internal/x/src/m/tests.rs", "README.md"]
    READ = {"internal/dashboard/server/src/lib.rs", "internal/dashboard/server/src/main.rs"}

    def test_a_pattern_that_matches_a_file_production_reads_is_a_finding(self):
        found = check_paths_ignore.check(["internal/**/tests/**"], self.TRACKED, self.READ | {"internal/x/tests/shared.rs"})
        self.assertEqual(found, ["internal/**/tests/** leaves out internal/x/tests/shared.rs, which production is built from"])

    def test_a_dead_pattern_and_a_pattern_that_reaches_a_file_that_is_not_rust_are_findings(self):
        self.assertEqual(check_paths_ignore.check(["internal/**/nothing.rs"], self.TRACKED, self.READ),
                         ["internal/**/nothing.rs matches no file of the repository"])
        self.assertEqual(check_paths_ignore.check(["**/README.md"], self.TRACKED, self.READ),
                         ["**/README.md reaches README.md, which is not Rust"])

    def test_a_list_that_lacks_the_entry_points_is_a_finding_and_a_clean_one_is_not(self):
        self.assertEqual(len(check_paths_ignore.check(["internal/**/tests/**"], self.TRACKED, set())), 2)
        self.assertEqual(check_paths_ignore.check(["internal/**/tests/**", "internal/**/tests.rs"], self.TRACKED, self.READ), [])

    def test_the_configuration_reader_takes_its_shape_and_nothing_else(self):
        good = 'name: x\n\n# a comment\npaths-ignore:\n  - "a/**"\n  - "b.rs"\n'
        self.assertEqual(check_paths_ignore.parse_config(good), {"name": "x", "paths-ignore": ["a/**", "b.rs"]})
        for bad in ('name: x\npaths-ignore:\n  - "a"\nqueries: x\n', 'name: x\npaths-ignore:\n  - a\n', 'name: x\npaths-ignore:\n\t- "a"\n',
                    'paths-ignore:\n  - "a"\n', 'name: x\npaths-ignore:\n', 'name: x\nquery-filters:\n  - exclude:\n'):
            with self.assertRaises(ValueError, msg=bad):
                check_paths_ignore.parse_config(bad)

    def test_the_real_configuration_is_read_by_the_reader(self):
        parsed = check_paths_ignore.parse_config(check_paths_ignore.CONFIG.read_text(encoding="utf-8"))
        self.assertEqual(set(parsed), {"name", "paths-ignore"})
        self.assertGreater(len(parsed["paths-ignore"]), 0)


if __name__ == "__main__":
    unittest.main()
