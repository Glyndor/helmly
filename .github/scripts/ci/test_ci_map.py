"""Tests of the CI map: what each change runs, and that the map says it truthfully.

Run by the `CI map` job on every change, from the pull request checkout, so a new
file nobody owns, a pattern that matches nothing, a suite no job uses and a job no
suite owns each fail here. The decisions themselves (decide.py) are read from the
base commit; this file guards the map they read.

    python3 .github/scripts/ci/test_ci_map.py
"""
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

ROOT = pathlib.Path(__file__).resolve().parents[3]


def tracked_files() -> "list[str]":
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [p for p in out.split("\0") if p]


class MapShape(unittest.TestCase):
    def setUp(self):
        self.map = ci_map.load(ROOT / ".github/ci-map.json")
        self.files = tracked_files()

    def test_every_tracked_file_has_an_owner(self):
        orphans = [
            f
            for f in self.files
            if not ci_map.matches(f, self.map["common"])
            and not any(ci_map.matches(f, p) for p in self.map["suites"].values())
            and not ci_map.matches(f, self.map["no_suite"])
        ]
        self.assertEqual(orphans, [], "files no suite runs and the map does not list as untested")

    def test_no_file_is_both_in_a_suite_and_declared_untested(self):
        both = [
            f
            for f in self.files
            if ci_map.matches(f, self.map["no_suite"])
            and (
                ci_map.matches(f, self.map["common"])
                or any(ci_map.matches(f, p) for p in self.map["suites"].values())
            )
        ]
        self.assertEqual(both, [])

    def test_every_pattern_matches_something(self):
        dead = []
        groups = {"common": self.map["common"], "no_suite": self.map["no_suite"]}
        groups.update({f"suites.{k}": v for k, v in self.map["suites"].items()})
        for group, patterns in groups.items():
            for pattern in patterns:
                if not any(ci_map.matches(f, [pattern]) for f in self.files):
                    dead.append(f"{group}: {pattern}")
        self.assertEqual(dead, [], "patterns that match no tracked file")

    def test_every_suite_has_files_of_its_own(self):
        for name, patterns in self.map["suites"].items():
            own = [f for f in self.files if ci_map.matches(f, patterns)]
            self.assertTrue(own, f"suite {name} owns no file")


EXPECTED_NO_SUITE = [
    ".editorconfig",
    ".github/CODEOWNERS",
    ".github/dependabot.yml",
    ".github/scripts/sign.py",
    ".gitignore",
    ".vscode/**",
    "LICENSE",
    "README.md",
    "docs/**",
    "internal/dashboard/.env.example",
    "internal/dashboard/docker-compose.yml",
    "internal/dashboard/nginx/**",
    "internal/dashboard/secrets/**",
]

EVERYTHING = {"server", "ui", "shell"}
# The suite each kind of file that affects what is built or released must run. A file that
# moves to the "no suite" side, or a pattern that stops matching, changes one of these.
EXPECTED_SUITES = {
    "internal/rust-toolchain.toml": {"server"},
    "internal/Cargo.toml": {"server"},
    "internal/Cargo.lock": {"server"},
    "internal/.cargo/audit.toml": {"server"},
    "internal/.cargo/config.toml": {"server"},
    "internal/.sqlx/query-x.json": {"server"},
    "internal/dashboard/server/.sqlx/query-x.json": {"server"},
    "internal/dashboard/server/migrations/001_x.sql": {"server"},
    "internal/dashboard/server/src/main.rs": {"server"},
    "internal/dashboard/release-verify-key.b64": {"server", "shell"},
    "internal/dashboard/server/tests/auth.rs": {"server"},
    ".github/scripts/audit-urls.py": {"server"},
    "internal/dashboard/ui/package.json": {"ui"},
    "internal/dashboard/ui/bun.lock": {"ui"},
    "internal/dashboard/ui/bunfig.toml": {"ui"},
    "internal/dashboard/ui/src/app/page.tsx": {"ui"},
    "install.sh": {"server", "shell"},
    "scripts/lint.sh": {"shell"},
    "internal/dashboard/setup-dashboard.sh": {"server", "shell"},
    "internal/dashboard/update-dashboard.sh": {"server", "shell"},
    ".github/scripts/install-bun.sh": EVERYTHING,
    ".github/scripts/install-shellcheck.sh": EVERYTHING,
    ".github/actions/setup-rust/action.yml": EVERYTHING,
    ".github/actions/cargo-tool/action.yml": EVERYTHING,
    ".github/ci-map.json": EVERYTHING,
    ".github/required-checks.json": EVERYTHING,
    ".github/scripts/ci/decide.py": EVERYTHING,
    ".github/scripts/ci/test_ci_wiring.py": EVERYTHING,
    ".github/scripts/ci/golden/.github__workflows__ci.yml": EVERYTHING,
    ".github/scripts/ci/golden/.github__actions__cargo-tool__action.yml": EVERYTHING,
    ".github/scripts/ci/wiring.py": EVERYTHING,
    ".github/scripts/ci/ci_common.py": EVERYTHING,
    ".github/scripts/ci/requirements.txt": EVERYTHING,
    ".github/scripts/ci/structure.py": EVERYTHING,
    ".github/scripts/ci/test_ci_structure.py": EVERYTHING,
    ".github/scripts/hygiene/check-secrets.sh": EVERYTHING,
    ".github/codeql/codeql-config.yml": EVERYTHING,
    ".github/scripts/codeql/test_codeql_paths.py": EVERYTHING,
    ".github/scripts/hygiene/tests/check-dco.test.sh": EVERYTHING,
    ".github/structure.json": EVERYTHING,
    ".github/scripts/ci/tree_checks.py": EVERYTHING,
    ".github/scripts/ci/test_ci_contract.py": EVERYTHING,
    ".github/scripts/ci/test_ci_tree.py": EVERYTHING,
    ".github/scripts/ci/test_ci_pins.py": EVERYTHING,
    ".github/scripts/ci/check_pins.py": EVERYTHING,
    ".github/scripts/ci/test_ci_environment.py": EVERYTHING,
    ".github/scripts/ci/test_ci_release.py": EVERYTHING,
    ".github/scripts/release/set_version.py": EVERYTHING,
    ".github/scripts/release/sign-artifacts.sh": EVERYTHING,
    ".github/scripts/install-cargo-audit.sh": EVERYTHING,
    ".github/workflows/ci.yml": EVERYTHING,
    ".github/workflows/audit.yml": EVERYTHING,
    ".github/workflows/codeql.yml": EVERYTHING,
    ".github/workflows/main-guard.yml": EVERYTHING,
    ".github/workflows/release-dashboard.yml": EVERYTHING,
    "docs/security-architecture.md": set(),
    "README.md": set(),
    "LICENSE": set(),
    ".github/dependabot.yml": set(),
    "internal/dashboard/docker-compose.yml": set(),
}


class MapIsExact(unittest.TestCase):
    """The side of the map that says "nothing to run" is a closed list, and what has to
    run is named file by file, so moving a file across the line is a visible change."""

    def setUp(self):
        self.map = ci_map.load(ROOT / ".github/ci-map.json")

    def test_the_no_suite_side_is_exactly_this_list(self):
        self.assertEqual(sorted(self.map["no_suite"]), sorted(EXPECTED_NO_SUITE))

    def test_each_named_file_runs_exactly_the_suites_expected(self):
        for path, expected in EXPECTED_SUITES.items():
            self.assertEqual(ci_map.select([path], self.map), expected, path)

    def test_the_suites_and_the_common_list_are_exactly_these(self):
        self.assertEqual(set(self.map["suites"]), EVERYTHING)
        self.assertEqual(
            sorted(self.map["common"]),
            sorted([
                ".github/ci-map.json",
                ".github/required-checks.json",
                ".github/workflows/**",
                ".github/scripts/ci/**",
                ".github/actions/**",
                ".github/scripts/install-*.sh",
                ".github/scripts/release/**",
                ".github/scripts/hygiene/**",
                ".github/codeql/**",
                ".github/scripts/codeql/**",
                ".github/structure.json",
            ]),
        )

    def test_every_tracked_workflow_runs_everything(self):
        for path in tracked_files():
            if path.startswith(".github/workflows/"):
                self.assertEqual(ci_map.select([path], self.map), EVERYTHING, path)


class Decisions(unittest.TestCase):
    def setUp(self):
        self.map = ci_map.load(ROOT / ".github/ci-map.json")

    def chosen(self, *paths):
        return ci_map.select(list(paths), self.map)

    def test_documentation_only_runs_nothing(self):
        self.assertEqual(self.chosen("README.md", "docs/security-architecture.md", "LICENSE"), set())

    def test_dependabot_config_only_runs_nothing(self):
        self.assertEqual(self.chosen(".github/dependabot.yml"), set())

    def test_server_source_runs_only_the_server(self):
        self.assertEqual(self.chosen("internal/dashboard/server/src/main.rs"), {"server"})
        self.assertEqual(self.chosen("internal/Cargo.lock"), {"server"})
        self.assertEqual(self.chosen("internal/.sqlx/query-x.json"), {"server"})

    def test_ui_source_runs_only_the_ui(self):
        self.assertEqual(self.chosen("internal/dashboard/ui/src/app/page.tsx"), {"ui"})
        self.assertEqual(self.chosen("internal/dashboard/ui/bun.lock"), {"ui"})

    def test_a_shell_script_runs_shellcheck(self):
        self.assertEqual(self.chosen("scripts/lint.sh"), {"shell"})
        self.assertEqual(self.chosen("scripts/install-podman.sh"), {"shell"})

    def test_the_installers_that_embed_the_release_key_also_run_the_tests_that_hold_it(self):
        # tests/release_key.rs reads these three and runs their verifiers: a change to one of
        # them alone must run the server suite, not only shellcheck.
        for path in ("install.sh", "internal/dashboard/setup-dashboard.sh", "internal/dashboard/update-dashboard.sh"):
            self.assertEqual(self.chosen(path), {"server", "shell"}, path)

    def test_a_change_to_the_ci_itself_runs_everything(self):
        everything = set(self.map["suites"])
        for path in (
            ".github/workflows/ci.yml",
            ".github/ci-map.json",
            ".github/scripts/ci/decide.py",
            ".github/actions/setup-rust/action.yml",
            ".github/scripts/install-bun.sh",
        ):
            self.assertEqual(self.chosen(path), everything, path)

    def test_a_mixed_change_runs_the_union(self):
        self.assertEqual(
            self.chosen("README.md", "install.sh", "internal/Cargo.toml"), {"server", "shell"}
        )

    def test_an_unknown_path_fails_open(self):
        self.assertIsNone(self.chosen("internal/dashboard/brand-new/thing.rs"))
        self.assertIsNone(self.chosen("README.md", "somewhere/new.txt"))

    def test_decide_runs_everything_when_it_cannot_read_the_diff(self):
        everything = set(self.map["suites"])
        for event, env in (
            ("pull_request", {}),
            ("push", {"BEFORE_SHA": "0" * 40, "AFTER_SHA": "a" * 40}),
            ("workflow_dispatch", {}),
            ("pull_request", {"BASE_SHA": "x" * 40, "HEAD_SHA": "y" * 40}),
        ):
            chosen, reason = decide.decide(event, env, self.map)
            self.assertEqual(chosen, everything, f"{event} {env}: {reason}")


class RealDiff(unittest.TestCase):
    """changed_paths against a throwaway repository, because its failure mode is silent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "user.name", "ci")
        self.cwd = os.getcwd()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, check=True
        ).stdout.decode().strip()

    def commit(self, message, **files):
        for name, content in files.items():
            path = self.repo / name.replace("__", "/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def test_a_pull_request_lists_what_it_changed_since_the_merge_base(self):
        base = self.commit("base", README_md="a", install_sh="x")
        head = self.commit("head", docs__a_md="b")
        paths = decide.changed_paths("pull_request", {"BASE_SHA": base, "HEAD_SHA": head})
        self.assertEqual(paths, ["docs/a_md"])

    def test_a_rename_lists_both_the_old_and_the_new_path(self):
        base = self.commit("base", install_sh="same content\n" * 20)
        self.git("mv", "install_sh", "moved_sh")
        self.git("commit", "-q", "-am", "move")
        head = self.git("rev-parse", "HEAD")
        paths = decide.changed_paths("pull_request", {"BASE_SHA": base, "HEAD_SHA": head})
        self.assertEqual(sorted(paths), ["install_sh", "moved_sh"])

    def test_a_push_reads_before_and_after(self):
        before = self.commit("one", a_txt="1")
        after = self.commit("two", b_txt="2")
        self.assertEqual(
            decide.changed_paths("push", {"BEFORE_SHA": before, "AFTER_SHA": after}), ["b_txt"]
        )

    def test_a_pull_request_into_main_runs_every_suite_even_when_its_diff_is_only_docs(self):
        data = ci_map.load(ROOT / ".github/ci-map.json")
        base = self.commit("base", README_md="a")
        head = self.commit("head", docs__a_md="b")
        env = {"BASE_SHA": base, "HEAD_SHA": head}
        self.assertEqual(decide.decide("pull_request", {**env, "BASE_REF": "main"}, data)[0], set(data["suites"]))
        self.assertEqual(decide.decide("pull_request", {**env, "BASE_REF": "develop"}, data)[0], set())

    def test_an_empty_or_unreadable_diff_is_not_a_decision(self):
        sha = self.commit("one", a_txt="1")
        self.assertIsNone(decide.changed_paths("pull_request", {"BASE_SHA": sha, "HEAD_SHA": sha}))
        self.assertIsNone(decide.changed_paths("pull_request", {"BASE_SHA": "0" * 39 + "1", "HEAD_SHA": sha}))
        self.assertIsNone(decide.changed_paths("schedule", {}))


class Matching(unittest.TestCase):
    def test_star_does_not_cross_a_directory_and_double_star_does(self):
        self.assertTrue(ci_map.matches("a/b.sh", ["a/*.sh"]))
        self.assertFalse(ci_map.matches("a/c/b.sh", ["a/*.sh"]))
        self.assertTrue(ci_map.matches("a/c/b.sh", ["a/**"]))
        self.assertTrue(ci_map.matches("install.sh", ["**/*.sh"]))
        self.assertTrue(ci_map.matches("a/b/install.sh", ["**/*.sh"]))
        self.assertFalse(ci_map.matches("install.sh.bak", ["**/*.sh"]))

    def test_a_pattern_must_fit_the_whole_path(self):
        self.assertFalse(ci_map.matches("xinternal/Cargo.toml", ["internal/Cargo.toml"]))
        self.assertFalse(ci_map.matches("internal/Cargo.toml.orig", ["internal/Cargo.toml"]))

    def test_regex_characters_in_a_pattern_are_literal(self):
        self.assertTrue(ci_map.matches("a(b)/c.d", ["a(b)/c.d"]))
        self.assertFalse(ci_map.matches("aXb)/cXd", ["a(b)/c.d"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
