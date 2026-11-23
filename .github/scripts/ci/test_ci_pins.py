"""The pins of the workflows: what Dependabot may move and what it may not.

The reference copies are compared with the files but for the value of a pin (the commit of an
action with its version comment, the digest of an image), so a Dependabot pull request passes
without regenerating them. The first class shows that the comparison tolerates a moved pin and
nothing else. The second tests the check that judges the value (`check_pins.py`), with a
`git ls-remote` that is made up, so that no test needs the network; the real one runs in the
`Tree checks` job.

    python3 .github/scripts/ci/test_ci_pins.py
"""
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_pins  # noqa: E402
from ci_common import ROOT, ci_files, unpin  # noqa: E402

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


class CheckPins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        (self.root / ".github/workflows").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def workflow(self, *lines, name="ci.yml"):
        (self.root / ".github/workflows" / name).write_text("jobs:\n  a:\n    steps:\n" + "".join(f"      - {line}\n" for line in lines))

    def found(self, tags):
        return check_pins.problems(self.root, fake(tags))

    CHECKOUT = {("https://github.com/actions/checkout", "v7.0.1"): (SHA_A, None)}

    def test_a_pin_at_the_commit_of_its_tag_passes(self):
        self.workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1")
        self.assertEqual(self.found(self.CHECKOUT), [])

    def test_an_annotated_tag_is_judged_by_the_commit_it_peels_to(self):
        self.workflow(f"uses: actions/checkout@{SHA_B} # v7.0.1")
        tags = {("https://github.com/actions/checkout", "v7.0.1"): (SHA_A, SHA_B)}
        self.assertEqual(self.found(tags), [])
        self.workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1")
        self.assertEqual(len(self.found(tags)), 1, "the id of the tag object is not the commit")

    def test_another_commit_of_the_same_repository_fails(self):
        self.workflow(f"uses: actions/checkout@{SHA_B} # v7.0.1")
        found = self.found(self.CHECKOUT)
        self.assertEqual(len(found), 1)
        self.assertIn("is not the commit of v7.0.1", found[0])

    def test_a_tag_that_does_not_exist_fails(self):
        self.workflow(f"uses: actions/checkout@{SHA_A} # v9.9.9")
        self.assertIn("does not exist", self.found(self.CHECKOUT)[0])

    def test_a_repository_that_cannot_be_asked_fails_closed(self):
        self.workflow(f"uses: actions/cache@{SHA_A} # v6.1.0")
        found = self.found(self.CHECKOUT)
        self.assertEqual(len(found), 1)
        self.assertIn("cannot ask github.com", found[0])

    def test_an_owner_outside_the_list_fails_before_anything_is_asked(self):
        self.workflow(f"uses: someone/checkout@{SHA_A} # v7.0.1")
        found = self.found({})
        self.assertEqual(len(found), 1)
        self.assertIn("allowed owner", found[0])

    def test_a_reference_that_is_not_a_full_commit_with_a_version_comment_fails(self):
        for line, why in (
            ("uses: actions/checkout@v7", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{SHA_A[:39]}  # v7.0.1", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{'g' * 40} # v7.0.1", "is not a full 40-hex commit"),
            (f"uses: actions/checkout@{SHA_A}", "has no `# vX.Y.Z` comment"),
            (f"uses: actions/checkout@{SHA_A} # trusted", "has no `# vX.Y.Z` comment"),
            ("uses: actions/checkout", "is not owner/repo@commit"),
        ):
            with self.subTest(line):
                self.workflow(line)
                found = self.found(self.CHECKOUT)
                self.assertEqual(len(found), 1)
                self.assertIn(why, found[0])

    def test_one_action_has_one_pair_of_commit_and_version_in_every_file(self):
        self.workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1")
        self.workflow(f"uses: actions/checkout@{SHA_B} # v7.0.2", name="other.yaml")
        tags = {**self.CHECKOUT, ("https://github.com/actions/checkout", "v7.0.2"): (SHA_B, None)}
        found = self.found(tags)
        self.assertEqual(len(found), 1)
        self.assertIn("more than one commit or version", found[0])

    def test_two_paths_of_one_repository_share_the_pin(self):
        self.workflow(f"uses: github/codeql-action/init@{SHA_C} # v4.39.4", f"uses: github/codeql-action/analyze@{SHA_C} # v4.39.4")
        self.assertEqual(self.found({("https://github.com/github/codeql-action", "v4.39.4"): (SHA_C, None)}), [])

    def test_a_local_action_is_not_a_pin(self):
        self.workflow("uses: ./.github/actions/setup-rust")
        self.assertEqual(self.found({}), [])

    def test_an_image_digest_must_come_from_the_allowed_list(self):
        (self.root / ".github/workflows/ci.yml").write_text("services:\n  x:\n    image: evil/postgres:18@sha256:" + "d" * 64 + "\n  y:\n    image: postgres:18@sha256:" + "d" * 64 + "\n")
        found = self.found({})
        self.assertEqual(len(found), 1)
        self.assertIn("evil/postgres", found[0])

    def test_the_tags_of_an_unrelated_commit_in_the_answer_are_not_taken(self):
        self.workflow(f"uses: actions/checkout@{SHA_A} # v7.0.1")
        out = f"{SHA_B}\trefs/tags/v7.0.10\n{SHA_A}\trefs/tags/v7.0.1\n"
        self.assertEqual(check_pins.tag_commit(out, "v7.0.1"), SHA_A)
        self.assertIsNone(check_pins.tag_commit(f"{SHA_B}\trefs/tags/v7.0.10\n", "v7.0.1"))

    def test_the_enumerator_is_the_one_the_other_tests_use(self):
        self.assertEqual(check_pins.ci_files(ROOT), ci_files())

    def test_this_repository_has_one_pin_per_action_and_none_malformed(self):
        pins = check_pins.pins(ROOT)
        self.assertGreater(len(pins), 20)
        self.assertEqual([p[4] for p in pins if p[4]], [])
        asked = []

        def everything_is_fine(url, *patterns):
            asked.append(url)
            version = patterns[0].removeprefix("refs/tags/")
            commit = {(name, v): sha for _, name, sha, v, _ in pins}
            repo = url.removeprefix("https://github.com/")
            return "".join(f"{sha}\trefs/tags/{v}\n" for (name, v), sha in commit.items() if name == repo and v == version)

        self.assertEqual(check_pins.problems(ROOT, everything_is_fine), [])
        self.assertEqual(len(asked), len(set(asked)), "each action is asked once")


if __name__ == "__main__":
    unittest.main()
