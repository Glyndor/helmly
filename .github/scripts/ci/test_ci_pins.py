"""The pins of the workflows: what Dependabot may move and what it may not.

The reference copies are compared with the files but for the value of a pin (the commit of an
action with its version comment, the digest of an image), so a Dependabot pull request passes
without regenerating them. The first class shows that the comparison tolerates a moved pin and
nothing else. The second tests the check that judges the value (`check_pins.py`), with a
`git ls-remote` that is made up, so that no test needs the network; the real one runs in the
`Tree checks` job.

    python3 .github/scripts/ci/test_ci_pins.py
"""
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_pins  # noqa: E402
from ci_common import ROOT, ci_files, load_yaml, unpin  # noqa: E402

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


class Pins(unittest.TestCase):

    def test_a_pin_moved_the_way_dependabot_moves_it_still_matches_the_reference(self):
        base = "      - uses: actions/checkout@" + "a" * 40 + " # v7.0.1\n        image: valkey/valkey:8-alpine@sha256:" + "b" * 64 + "\n"
        moved = "      - uses: actions/checkout@" + "c" * 40 + " # v7.1.0\n        image: valkey/valkey:8-alpine@sha256:" + "d" * 64 + "\n"
        self.assertEqual(unpin(base), unpin(moved))
        self.assertEqual(unpin("uses: github/codeql-action/init@" + "a" * 40 + " # v4.39.4"), unpin("uses: github/codeql-action/init@" + "e" * 40 + " # v4.40.0"))

    def test_anything_but_the_value_of_a_pin_changing_still_differs_from_the_reference(self):
        sha = "a" * 40
        base = f"uses: actions/checkout@{sha} # v7.0.1\nimage: valkey/valkey:8-alpine@sha256:" + "b" * 64 + "\n"
        for what, changed in (
            ("another action", base.replace("actions/checkout", "actions/cache")),
            ("another owner", base.replace("actions/checkout", "evil/checkout")),
            ("the pin removed", base.replace(f"@{sha} # v7.0.1", "")),
            ("a tag instead of a commit", base.replace(f"@{sha} # v7.0.1", "@v7")),
            ("a short hash", base.replace(sha, "a" * 39)),
            ("a hash of the wrong alphabet", base.replace(sha, "g" * 40)),
            ("another image", base.replace("valkey/valkey", "evil/valkey")),
            ("another tag of the image", base.replace("8-alpine", "9-alpine")),
            ("the digest removed", base.replace("@sha256:" + "b" * 64, "")),
            ("a short digest", base.replace("b" * 64, "b" * 63)),
            ("a line added", base + "run: x\n"),
            ("a comment that is not a version", base.replace("# v7.0.1", "# trusted")),
            ("a second uses of the same action", base + base),
        ):
            with self.subTest(what):
                self.assertNotEqual(unpin(base), unpin(changed))

    def test_the_unpin_does_not_touch_a_local_action_or_a_step_name(self):
        text = "- uses: ./.github/actions/setup-rust\n- name: uses: actions/checkout@" + "a" * 40 + "\n"
        self.assertEqual(unpin("- uses: ./.github/actions/setup-rust\n"), "- uses: ./.github/actions/setup-rust\n")
        self.assertIn("@<sha>", unpin(text))


def fake(tags):
    """A `git ls-remote` that knows these tags: {(repo url, version): (commit, peeled commit or None)}."""

    def ls_remote(url, *patterns):
        if url not in {u for u, _ in tags}:
            raise subprocess.CalledProcessError(128, "git ls-remote")
        lines = []
        for (known, version), (direct, peeled) in tags.items():
            if known == url and f"refs/tags/{version}" in patterns:
                lines.append(f"{direct}\trefs/tags/{version}")
                if peeled:
                    lines.append(f"{peeled}\trefs/tags/{version}^{{}}")
        return "\n".join(lines) + ("\n" if lines else "")

    return ls_remote


CHECKOUT = {("https://github.com/actions/checkout", "v7.0.1"): (SHA_A, None)}


def workflow(*lines):
    return "jobs:\n  a:\n    steps:\n" + "".join(f"      - {line}\n" for line in lines)


def found(files, tags=CHECKOUT, base=None):
    return check_pins.problems(files, fake(tags), base)


class CheckPins(unittest.TestCase):
    def one(self, *lines, tags=CHECKOUT, base=None):
        return found({".github/workflows/ci.yml": workflow(*lines)}, tags, base)

    def test_a_pin_at_the_commit_of_its_tag_passes(self):
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_A} # v7.0.1"), [])

    def test_an_annotated_tag_is_judged_by_the_commit_it_peels_to(self):
        tags = {("https://github.com/actions/checkout", "v7.0.1"): (SHA_A, SHA_B)}
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_B} # v7.0.1", tags=tags), [])
        self.assertEqual(len(self.one(f"uses: actions/checkout@{SHA_A} # v7.0.1", tags=tags)), 1, "the id of the tag object is not the commit")

    def test_another_commit_of_the_same_repository_fails(self):
        result = self.one(f"uses: actions/checkout@{SHA_B} # v7.0.1")
        self.assertEqual(len(result), 1)
        self.assertIn("is not the commit of v7.0.1", result[0])

    def test_a_tag_that_does_not_exist_fails(self):
        self.assertIn("does not exist", self.one(f"uses: actions/checkout@{SHA_A} # v9.9.9")[0])

    def test_a_repository_that_cannot_be_asked_fails_closed(self):
        result = self.one(f"uses: actions/cache@{SHA_A} # v6.1.0")
        self.assertEqual(len(result), 1)
        self.assertIn("cannot ask github.com", result[0])

    def test_an_owner_outside_the_list_fails_before_anything_is_asked(self):
        result = self.one(f"uses: someone/checkout@{SHA_A} # v7.0.1", tags={})
        self.assertEqual(len(result), 1)
        self.assertIn("allowed owner", result[0])

    def test_a_reference_that_is_not_a_full_commit_with_a_strict_version_comment_fails(self):
        for line, why in (
            ("uses: actions/checkout@v7", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{SHA_A[:39]}  # v7.0.1", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{'g' * 40} # v7.0.1", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{SHA_A}", "has no `# vMAJOR.MINOR.PATCH` comment"),
            (f"uses: actions/checkout@{SHA_A} # trusted", "has no `# vMAJOR.MINOR.PATCH` comment"),
            (f"uses: actions/checkout@{SHA_A} # v7", "has no `# vMAJOR.MINOR.PATCH` comment"),
            (f"uses: actions/checkout@{SHA_A} # v7.0", "has no `# vMAJOR.MINOR.PATCH` comment"),
            (f"uses: actions/checkout@{SHA_A} # v7.0.1-rc1", "has no `# vMAJOR.MINOR.PATCH` comment"),
            (f"uses: actions/checkout@{SHA_A} # vx.y.z", "has no `# vMAJOR.MINOR.PATCH` comment"),
            ("uses: actions/checkout", "is not owner/repo@commit"),
        ):
            with self.subTest(line):
                result = self.one(line)
                self.assertEqual(len(result), 1, result)
                self.assertIn(why, result[0])

    def test_a_form_the_line_reader_would_not_see_is_still_judged_from_the_loaded_yaml(self):
        for line in (
            f'uses: "actions/checkout@{SHA_A}" # v7.0.1',
            f"uses: 'actions/checkout@{SHA_A}' # v7.0.1",
            f"{{uses: actions/checkout@{SHA_A}}} # v7.0.1",
        ):
            with self.subTest(line):
                result = self.one(line)
                self.assertTrue(result, "a pin written in a way the line reader cannot read is a finding")

    def test_a_uses_anywhere_in_the_document_is_a_pin(self):
        text = "jobs:\n  a:\n    uses: someone/else/.github/workflows/x.yml@main\n"
        result = found({".github/workflows/ci.yml": text}, {})
        self.assertEqual(len(result), 1)

    def test_a_file_the_strict_reader_refuses_is_a_finding(self):
        result = found({".github/workflows/ci.yml": "a: 1\na: 2\n"})
        self.assertEqual(len(result), 1)
        self.assertIn("cannot be read as strict YAML", result[0])

    def test_one_action_has_one_pair_of_commit_and_version_in_every_file(self):
        tags = {**CHECKOUT, ("https://github.com/actions/checkout", "v7.0.2"): (SHA_B, None)}
        files = {
            ".github/workflows/ci.yml": workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1"),
            ".github/workflows/other.yaml": workflow(f"uses: actions/checkout@{SHA_B} # v7.0.2"),
        }
        result = found(files, tags)
        self.assertEqual(len(result), 1)
        self.assertIn("more than one commit or version", result[0])

    def test_two_paths_of_one_repository_share_the_pin(self):
        tags = {("https://github.com/github/codeql-action", "v4.39.4"): (SHA_C, None)}
        self.assertEqual(self.one(f"uses: github/codeql-action/init@{SHA_C} # v4.39.4", f"uses: github/codeql-action/analyze@{SHA_C} # v4.39.4", tags=tags), [])

    def test_a_local_action_and_a_step_named_like_one_are_not_pins(self):
        self.assertEqual(self.one("uses: ./.github/actions/setup-rust", "name: uses actions/checkout", tags={}), [])

    def test_lowering_an_action_below_what_the_base_pins_fails_and_raising_or_keeping_it_passes(self):
        tags = {
            ("https://github.com/actions/checkout", "v7.0.1"): (SHA_A, None),
            ("https://github.com/actions/checkout", "v6.9.9"): (SHA_B, None),
            ("https://github.com/actions/checkout", "v7.1.0"): (SHA_C, None),
        }
        base = {".github/workflows/ci.yml": workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1")}
        down = self.one(f"uses: actions/checkout@{SHA_B} # v6.9.9", tags=tags, base=base)
        self.assertEqual(len(down), 1)
        self.assertIn("goes down to v6.9.9 from v7.0.1", down[0])
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_C} # v7.1.0", tags=tags, base=base), [])
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_A} # v7.0.1", tags=tags, base=base), [])
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_B} # v6.9.9", tags=tags), [], "without a base there is nothing to compare")

    def test_the_versions_are_compared_as_numbers(self):
        tags = {("https://github.com/actions/checkout", "v7.9.0"): (SHA_A, None), ("https://github.com/actions/checkout", "v7.10.0"): (SHA_B, None)}
        base = {".github/workflows/ci.yml": workflow(f"uses: actions/checkout@{SHA_A} # v7.9.0")}
        self.assertEqual(self.one(f"uses: actions/checkout@{SHA_B} # v7.10.0", tags=tags, base=base), [])

    def test_an_image_outside_the_list_fails_with_or_without_a_digest_or_tag_and_wherever_it_is(self):
        digest = "@sha256:" + "d" * 64
        for text in (
            f"services:\n  x:\n    image: evil/postgres:18{digest}\n",
            "services:\n  x:\n    image: evil/postgres:18\n",
            "services:\n  x:\n    image: evil/postgres\n",
            f"services:\n  x:\n    image: ghcr.io/postgres{digest}\n",
            "services:\n  x:\n    image: ghcr.io/postgres:18\n",
            "services:\n  x:\n    image: localhost:5000/postgres:18\n",
            "services:\n  x:\n    image: registry.example.com:5000/valkey/valkey\n",
            "services:\n  x:\n    image: postgresql:18\n",
            "container: node:20\n",
            "container:\n  image: node:20\n",
            "steps:\n  - uses: docker://alpine:3\n",
        ):
            with self.subTest(text):
                result = found({".github/workflows/ci.yml": text}, {})
                self.assertEqual(len(result), 1, result)
                self.assertIn("not one of the allowed ones", result[0])

    def test_an_image_of_the_list_passes_with_or_without_a_tag_or_a_digest(self):
        digest = "@sha256:" + "d" * 64
        for ref in ("postgres", "postgres:18-alpine", f"postgres:18{digest}", f"postgres{digest}", "valkey/valkey:8-alpine", "docker.io/valkey/valkey:8", "docker.io/postgres:18"):
            with self.subTest(ref):
                self.assertEqual(found({".github/workflows/ci.yml": f"services:\n  x:\n    image: {ref}\n"}, {}), [])

    def test_the_host_the_name_and_the_tag_of_an_image_are_told_apart(self):
        self.assertEqual(check_pins.image_name("postgres:18"), ("", "postgres"))
        self.assertEqual(check_pins.image_name("valkey/valkey:8@sha256:" + "a" * 64), ("", "valkey/valkey"))
        self.assertEqual(check_pins.image_name("localhost:5000/postgres"), ("localhost:5000", "postgres"))
        self.assertEqual(check_pins.image_name("ghcr.io/valkey/valkey:8"), ("ghcr.io", "valkey/valkey"))
        self.assertEqual(check_pins.image_name("evil.example/postgres"), ("evil.example", "postgres"))

    def test_the_tags_of_an_unrelated_commit_in_the_answer_are_not_taken(self):
        out = f"{SHA_B}\trefs/tags/v7.0.10\n{SHA_A}\trefs/tags/v7.0.1\n"
        self.assertEqual(check_pins.tag_commit(out, "v7.0.1"), SHA_A)
        self.assertIsNone(check_pins.tag_commit(f"{SHA_B}\trefs/tags/v7.0.10\n", "v7.0.1"))

    def test_the_enumerator_is_the_one_the_other_tests_use(self):
        self.assertEqual(check_pins.ci_files(ROOT), ci_files())

    def test_the_reader_of_the_tests_is_the_reader_of_the_check(self):
        self.assertEqual(load_yaml("a: 1\n"), check_pins.load_yaml("a: 1\n"))

    def test_this_repository_passes_with_a_remote_that_agrees(self):
        files = check_pins.read_tree(ROOT)
        pins = [p for rel, text in files.items() for p in check_pins.pins_of(text, rel)[0]]
        self.assertGreater(len(pins), 20)
        asked = []

        def everything_is_fine(url, *patterns):
            asked.append(url)
            version = patterns[0].removeprefix("refs/tags/")
            repo = url.removeprefix("https://github.com/")
            return "".join(f"{sha}\trefs/tags/{v}\n" for _, name, sha, v in pins if name == repo and v == version)

        self.assertEqual(check_pins.problems(files, everything_is_fine, files), [])
        self.assertEqual(len(asked), len(set(asked)), "each action is asked once")

    def test_the_remote_is_asked_from_an_empty_directory_with_no_configuration(self):
        calls = []
        real = subprocess.run

        def spy(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        subprocess.run = spy
        try:
            check_pins.git_ls_remote("https://github.com/actions/checkout", "refs/tags/v1.0.0")
        finally:
            subprocess.run = real
        (cmd, kw), = calls
        self.assertEqual(cmd[:3], ["git", "ls-remote", "https://github.com/actions/checkout"])
        self.assertNotEqual(pathlib.Path(kw["cwd"]).resolve(), ROOT)
        self.assertNotIn(ROOT, pathlib.Path(kw["cwd"]).resolve().parents)
        self.assertEqual(kw["env"]["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(kw["env"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(kw["env"]["HOME"], kw["cwd"])

    def test_the_base_is_read_from_git_and_not_from_the_working_directory(self):
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)

            def git(*args):
                subprocess.run(["git", "-C", tmp, *args], check=True, capture_output=True, env=env)

            git("init", "-q", "-b", "main")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/ci.yml").write_text(workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1"))
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
            (root / ".github/workflows/ci.yml").write_text(workflow(f"uses: actions/checkout@{SHA_B} # v6.0.0"))
            base = check_pins.read_base(root, "HEAD")
            self.assertIn(SHA_A, base[".github/workflows/ci.yml"])
            self.assertIn(SHA_B, check_pins.read_tree(root)[".github/workflows/ci.yml"])
            with self.assertRaises(subprocess.CalledProcessError):
                check_pins.read_base(root, "0" * 40)


def job_script():
    return load_yaml((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["pins"]["steps"][1]["run"]


class ThePinsJob(unittest.TestCase):
    def setUp(self):
        self.doc = load_yaml((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
        self.job = self.doc["jobs"]["pins"]
        self.run = job_script()

    def test_it_is_a_job_of_its_own_that_always_runs_and_the_gate_waits_for_it(self):
        for key in ("if", "needs", "env", "continue-on-error", "defaults", "strategy", "services", "container"):
            self.assertNotIn(key, self.job)
        self.assertEqual(self.job["name"], "Pin checks")
        self.assertIn("pins", self.doc["jobs"]["gate"]["needs"])
        self.assertEqual(len(self.job["steps"]), 2)

    def test_it_has_a_clean_checkout_of_the_whole_history_without_credentials(self):
        first = self.job["steps"][0]
        self.assertEqual(first["with"], {"fetch-depth": "0", "persist-credentials": "false"})
        self.assertNotIn("run", first)

    def test_the_step_has_only_the_event_and_the_base_commit(self):
        self.assertEqual(
            self.job["steps"][1]["env"],
            {"EVENT": "${{ github.event_name }}", "BASE_SHA": "${{ github.event.pull_request.base.sha }}"},
        )
        self.assertNotIn("if", self.job["steps"][1])

    def test_the_judge_and_its_reader_come_from_the_base_before_anything_of_the_tree_runs(self):
        run = self.run
        order = [
            run.index('git cat-file -e "$BASE_SHA^{commit}"'),
            run.index('from_base "$script" "$tmp/check_pins.py"'),
            run.index('from_base "$requirements" "$tmp/requirements.txt"'),
            run.index('reader="$tmp/requirements.txt"'),
            run.index('python3 -m venv "$tmp/venv"'),
            run.index('"$tmp/venv/bin/python" -I "$judge" --base "$BASE_SHA"'),
            run.index('"$tmp/venv/bin/python" -I "$script" --base "$BASE_SHA"'),
        ]
        self.assertEqual(order, sorted(order))
        self.assertIn('tmp="$(mktemp -d)"', run)
        self.assertIn('--isolated install --quiet --only-binary=:all: --require-hashes -r "$reader"', run)
        # the only commands before the judge of the base are git, mktemp and the install of its reader
        before = run[: run.index('"$tmp/venv/bin/python" -I "$judge"')]
        self.assertNotIn('"$script"', before.replace('from_base "$script"', "").replace("script=.github", ""))
        self.assertNotIn("bash ", before)
        self.assertNotIn("source ", before)

    def test_the_tree_copy_runs_last(self):
        run = self.run
        self.assertGreater(run.index('-I "$script"'), run.index('-I "$judge"'))
        self.assertTrue(run.rstrip().endswith('exit "$bad"'))

    def test_absence_in_the_base_is_told_from_a_failure_to_read(self):
        run = self.run
        self.assertIn('listing="$(git ls-tree "$BASE_SHA" -- "$1")" || { echo "::error::cannot list $1 in the base commit"; exit 1; }', run)
        self.assertIn('[ -n "$listing" ] || return 1', run)
        self.assertIn('git show "$BASE_SHA:$1" > "$2" || { echo "::error::cannot read $1 in the base commit"; exit 1; }', run)
        self.assertNotIn("2>/dev/null", run)
        self.assertIn("the base has the pin check but not its requirements", run)

    def run_from_base(self, base_sha, with_script):
        """The `from_base` function of the job, run against a small repository."""
        function = re.search(r"          from_base\(\) \{.*?\n          \}\n", job_script_raw(), re.S)
        self.assertIsNotNone(function)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            subprocess.run(["git", "-C", tmp, "init", "-q", "-b", "main"], check=True, capture_output=True, env=env)
            (root / "other").write_text("x")
            if with_script:
                (root / "check_pins.py").write_text("print(1)\n")
            subprocess.run(["git", "-C", tmp, "add", "-A"], check=True, capture_output=True, env=env)
            subprocess.run(["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "x"], check=True, capture_output=True, env=env)
            sha = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], check=True, capture_output=True, text=True, env=env).stdout.strip()
            script = "set -uo pipefail\n" + "\n".join(line[10:] for line in function.group(0).splitlines()) + '\nfrom_base check_pins.py "$OUT"\necho "status=$?"\n'
            done = subprocess.run(["bash", "-c", script], cwd=tmp, env={**env, "BASE_SHA": base_sha or sha, "OUT": str(root / "out")}, capture_output=True, text=True)
            return done.returncode, done.stdout + done.stderr, (root / "out").exists()

    def test_the_function_that_reads_the_base_distinguishes_absent_present_and_unreadable(self):
        code, out, copied = self.run_from_base(None, with_script=True)
        self.assertEqual((code, copied), (0, True), out)
        self.assertIn("status=0", out)
        code, out, copied = self.run_from_base(None, with_script=False)
        self.assertEqual(code, 0, out)
        self.assertIn("status=1", out)
        self.assertFalse(copied)
        code, out, copied = self.run_from_base("0" * 40, with_script=True)
        self.assertEqual(code, 1, out)
        self.assertIn("cannot list", out)
        self.assertNotIn("status=", out)


def job_script_raw():
    return (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
