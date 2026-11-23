"""Tests of the release workflow and the scripts it runs.

    python3 .github/scripts/ci/test_ci_release.py
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

from ci_common import ROOT  # noqa: E402
from wiring import run_block, split_jobs, strip_comments  # noqa: E402

RELEASE = ROOT / ".github/workflows/release-dashboard.yml"
SET_VERSION = ROOT / ".github/scripts/release/set_version.py"
SIGN_ARTIFACTS = ROOT / ".github/scripts/release/sign-artifacts.sh"
SIGN = ROOT / ".github/scripts/sign.py"
TAG_STEP = "Check the shape of the tag"


class ReleaseWorkflow(unittest.TestCase):
    """The workflow that builds, signs and publishes: no cache, no installer piped into a
    shell, a pinned toolchain and pinned tools, the write token only where it publishes, the
    signing key only where it publishes, and a dry run that publishes nothing."""

    def setUp(self):
        self.raw = RELEASE.read_text(encoding="utf-8")
        self.text = strip_comments(self.raw)
        self.jobs = split_jobs(self.text)

    def test_it_restores_and_saves_no_cache(self):
        self.assertNotIn("actions/cache", self.text)
        self.assertNotIn("cache:", self.text)

    def test_no_installer_is_piped_into_a_shell_and_bun_comes_from_the_pinned_script(self):
        self.assertNotRegex(self.text, r"curl[^\n]*\|\s*(ba)?sh")
        self.assertEqual(self.text.count("install-bun.sh"), 2)

    def test_the_toolchain_is_the_one_of_the_repository_not_stable(self):
        self.assertNotIn("stable", self.text)
        self.assertNotRegex(self.text, r"rustup (toolchain install|default) \S")
        self.assertEqual(len(re.findall(r"run: (\|\n\s+)?rustup toolchain install\b", self.text)), 3)

    def test_the_tools_that_are_built_have_a_version(self):
        for line in re.findall(r"^\s+run: (cargo install .*)$", self.text, re.M):
            self.assertRegex(line, r"--version \d+\.\d+\.\d+", line)
            self.assertIn("--locked", line)

    def test_the_audit_is_the_downloaded_cargo_audit_with_the_shared_configuration(self):
        self.assertIn("install-cargo-audit.sh", self.text)
        self.assertNotIn("cargo install cargo-audit", self.text)
        self.assertIn("run: cargo-audit audit --deny warnings", self.text)
        self.assertNotIn("--ignore", self.text)

    def test_every_checkout_drops_the_token(self):
        for name, body in self.jobs.items():
            for block in re.findall(r"uses: actions/checkout@\S+ # v[\d.]+\n((?:        .*\n)+)", body):
                self.assertIn("persist-credentials: false", block, name)

    def test_the_signing_library_is_installed_from_pinned_hashes_in_a_venv(self):
        req = (ROOT / ".github/scripts/release/sign-requirements.txt").read_text(encoding="utf-8")
        pins = re.findall(r"^([a-z]+)==([\d.]+) \\$", req, re.M)
        self.assertEqual(sorted(n for n, _ in pins), ["cffi", "cryptography", "pycparser"])
        for name in ("cffi", "cryptography", "pycparser"):
            block = re.search(rf"^{name}==.*?(?=^\S|\Z)", req, re.M | re.S).group(0)
            self.assertGreaterEqual(len(re.findall(r"--hash=sha256:[0-9a-f]{64}", block)), 1, name)
        install = 'python3 -m venv "$RUNNER_TEMP/signvenv"'
        for job in ("sign-dry-run", "release"):
            body = split_jobs(self.text)[job]
            self.assertIn(install, body, job)
            self.assertIn("-m pip install --quiet --require-hashes --only-binary=:all: -r", body, job)
            self.assertIn("sign-requirements.txt", body, job)
        self.assertEqual(len(re.findall(r"\bpip install\b", self.text)), 2)
        self.assertNotIn("pip install --quiet 'cryptography", self.text)


class WhoCanPublishAndSign(unittest.TestCase):
    def setUp(self):
        self.text = strip_comments(RELEASE.read_text(encoding="utf-8"))
        self.jobs = split_jobs(self.text)

    def test_the_write_token_is_held_only_by_the_job_that_publishes(self):
        top = self.text.split("\njobs:\n", 1)[0]
        self.assertTrue(top.endswith("permissions:\n  contents: read"), top[-60:])
        writers = [name for name, body in self.jobs.items() if re.search(r"contents:\s*write", body)]
        self.assertEqual(writers, ["release"])
        self.assertIn("    permissions:\n      contents: write\n", self.jobs["release"])

    def test_the_signing_key_is_read_by_the_release_job_and_by_no_other(self):
        readers = [name for name, body in self.jobs.items() if "secrets.GLYNDOR_RELEASE_ED25519_KEY" in body]
        self.assertEqual(readers, ["release"])
        self.assertEqual(self.text.count("secrets."), 1)

    def test_a_dry_run_signs_with_a_key_made_in_the_job_and_says_so(self):
        body = self.jobs["sign-dry-run"]
        self.assertIn("    if: ${{ inputs.dry_run }}\n", body)
        self.assertIn("os.urandom(32)", body)
        self.assertIn("::add-mask::", body)
        self.assertIn("throwaway key", body)
        self.assertIn("The release key is not read", body)
        self.assertNotIn("secrets.", body)
        self.assertNotIn("gh release", body)

    def test_the_release_job_is_skipped_by_a_dry_run_and_the_others_never_publish(self):
        self.assertRegex(self.text, r"dry_run:\n\s+description: .*\n\s+type: boolean\n\s+default: false")
        self.assertIn("    if: ${{ !inputs.dry_run }}\n", self.jobs["release"])
        for name, body in self.jobs.items():
            if name != "release":
                self.assertNotIn("gh release", body, name)

    def test_the_build_jobs_sign_nothing(self):
        for name in ("prepare", "ci-server", "ci-ui", "build"):
            self.assertNotIn("sign.py", self.jobs[name], name)
            self.assertNotIn("sign-artifacts", self.jobs[name], name)
            self.assertNotIn(".sig", self.jobs[name], name)

    def test_a_run_started_by_hand_must_be_at_the_commit_of_the_tag(self):
        step = run_block(self.jobs["release"], "Check that this commit is the one the tag points at")
        self.assertIn('git fetch --no-tags --depth=1 origin "refs/tags/${TAG}:refs/tags/${TAG}"', step)
        self.assertIn('git rev-parse "refs/tags/${TAG}^{commit}"', step)
        self.assertIn('[ "$TAG_SHA" != "$RUN_SHA" ]', step)
        # it runs before the key is read
        body = self.jobs["release"]
        self.assertLess(body.index("Check that this commit"), body.index("secrets.GLYNDOR_RELEASE_ED25519_KEY"))

    def test_the_artifacts_are_downloaded_by_name_outside_the_checkout_one_folder_each(self):
        self.assertNotIn("merge-multiple", self.text)
        for job in ("sign-dry-run", "release"):
            body = self.jobs[job]
            names = re.findall(r"uses: actions/download-artifact@\S+ # v[\d.]+\n        with:\n          name: (\S+)\n          path: (.+)\n", body)
            self.assertEqual(
                names,
                [
                    ("dashboard-x86_64", "${{ runner.temp }}/artifacts/x86_64"),
                    ("dashboard-arm64", "${{ runner.temp }}/artifacts/arm64"),
                ],
                job,
            )
            self.assertEqual(len(re.findall(r"download-artifact@", body)), 2, job)

    def test_the_signer_runs_isolated_on_that_directory_with_the_interpreter_of_the_venv(self):
        for job in ("sign-dry-run", "release"):
            body = self.jobs[job]
            self.assertIn("SIGN_PYTHON: ${{ runner.temp }}/signvenv/bin/python", body, job)
            self.assertIn('run: bash "$GITHUB_WORKSPACE/.github/scripts/release/sign-artifacts.sh" "$RUNNER_TEMP/artifacts"', body, job)
        script = SIGN_ARTIFACTS.read_text(encoding="utf-8")
        self.assertIn('"$python" -I "$GITHUB_WORKSPACE/.github/scripts/sign.py" "$file"', script)

    def test_the_release_is_created_from_the_signed_directory(self):
        body = self.jobs["release"]
        self.assertIn("working-directory: ${{ runner.temp }}/artifacts", body)
        self.assertEqual(len(re.findall(r"^\s+(?:x86_64|arm64)/helmly-dashboard-\S+", body, re.M)), 12)

    def test_the_signing_job_does_not_sign_before_the_check(self):
        body = self.jobs["release"]
        self.assertLess(body.index("Check that this commit"), body.index("sign-artifacts.sh"))
        self.assertLess(body.index("sign-artifacts.sh"), body.index("gh release create"))


class TheTagIsValidatedFirst(unittest.TestCase):
    def setUp(self):
        self.text = strip_comments(RELEASE.read_text(encoding="utf-8"))
        self.jobs = split_jobs(self.text)

    def test_every_other_job_depends_on_the_validation_directly_or_through_another(self):
        needs = {}
        for name, body in self.jobs.items():
            m = re.search(r"^    needs: \[([^\]]*)\]$", body, re.M)
            needs[name] = {x.strip() for x in m.group(1).split(",")} if m else set()

        def reaches_tag(name, seen=()):
            return "tag" in needs[name] or any(reaches_tag(n, seen + (name,)) for n in needs[name] if n not in seen)

        for name in self.jobs:
            if name != "tag":
                self.assertTrue(reaches_tag(name), f"{name} can run before the tag is validated")
        self.assertEqual(needs["tag"], set())

    def test_the_tag_reaches_the_other_jobs_only_as_the_output_of_the_validation(self):
        for name, body in self.jobs.items():
            if name == "tag":
                continue
            self.assertNotIn("github.ref_name", body, name)
            self.assertNotIn("inputs.tag", body, name)
            self.assertNotIn("github.event.inputs", body, name)

    def test_the_validation_is_the_release_tag_shape_and_fails_closed(self):
        script = run_block(self.jobs["tag"], TAG_STEP, "      - id: tag")
        self.assertIn(r'[[ "$TAG" =~ ^dashboard@[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]', script)
        self.assertIn("exit 1", script)
        self.assertTrue(script.startswith("set -euo pipefail"))

    def cases(self, tag: str) -> int:
        script = run_block(self.jobs["tag"], TAG_STEP, "      - id: tag")
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp) / "out"
            out.touch()
            env = {**os.environ, "TAG": tag, "GITHUB_OUTPUT": str(out)}
            code = subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode
            if code == 0:
                self.assertEqual(out.read_text(), f"tag={tag}\n")
        return code

    def test_the_validation_accepts_a_release_tag_and_refuses_everything_else(self):
        for good in ("dashboard@1.2.3", "dashboard@10.20.30", "dashboard@1.2.3-rc.1"):
            self.assertEqual(self.cases(good), 0, good)
        for bad in (
            "",
            "dashboard@",
            "dashboard@1.2",
            "dashboard@1.2.3-",
            'dashboard@1.2.3"; touch x; "',
            "dashboard@1.2.3/../x",
            "dashboard@1.2.3\nx",
            "dashboard@1.2.3\n",
            "dashboard@1.2.3 ",
            "agent@1.2.3",
            "v1.2.3",
            "dashboard@1.2.3$(id)",
            "dashboard@1.2.3`id`",
            "dashboard@1.2.3&x",
        ):
            self.assertEqual(self.cases(bad), 1, repr(bad))


class SetVersion(unittest.TestCase):
    """The script that writes the tag's version into Cargo.toml: it builds no program text out
    of the tag, and refuses what is not a release tag before it reads or writes anything."""

    TOML = '[package]\nname = "x"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\nserde = { version = "1" }\n'

    def run_script(self, tag: "str | None", text: str = None):
        text = self.TOML if text is None else text
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "Cargo.toml"
            path.write_text(text)
            env = {k: v for k, v in os.environ.items() if k != "TAG"}
            if tag is not None:
                env["TAG"] = tag
            done = subprocess.run(["python3", str(SET_VERSION), str(path)], env=env, capture_output=True, text=True)
            return done.returncode, path.read_text(), done.stderr

    def test_a_valid_tag_sets_the_package_version_and_nothing_else(self):
        code, out, _ = self.run_script("dashboard@2.3.4")
        self.assertEqual(code, 0)
        self.assertEqual(out, self.TOML.replace('version = "0.1.0"', 'version = "2.3.4"'))

    def test_a_prerelease_tag_is_kept_whole(self):
        code, out, _ = self.run_script("dashboard@2.3.4-rc.1")
        self.assertEqual(code, 0)
        self.assertIn('version = "2.3.4-rc.1"', out)

    def test_a_tag_with_quotes_slash_newline_or_nothing_changes_no_file(self):
        for bad in ('dashboard@1.2.3"', "dashboard@1.2.3/x", "dashboard@1.2.3\n", "dashboard@1.2.3\nversion = \"9\"", "", "dashboard@", "1.2.3"):
            code, out, err = self.run_script(bad)
            self.assertEqual(code, 1, repr(bad))
            self.assertEqual(out, self.TOML, repr(bad))
            self.assertIn("::error::", err)

    def test_a_missing_tag_changes_no_file(self):
        code, out, _ = self.run_script(None)
        self.assertEqual((code, out), (1, self.TOML))

    def test_a_manifest_without_a_version_line_is_an_error(self):
        code, out, err = self.run_script("dashboard@1.2.3", '[package]\nname = "x"\n')
        self.assertEqual(code, 1)
        self.assertIn("has no version line", err)

    def test_the_arguments_are_exactly_one_path(self):
        env = {**os.environ, "TAG": "dashboard@1.2.3"}
        self.assertEqual(subprocess.run(["python3", str(SET_VERSION)], env=env, capture_output=True).returncode, 2)

    def test_the_script_runs_no_command_and_builds_no_program(self):
        text = SET_VERSION.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "eval(", "exec(", "shell=True", "sed"):
            self.assertNotIn(forbidden, text)

    def test_the_workflow_calls_it_with_the_validated_output_only(self):
        body = split_jobs(strip_comments(RELEASE.read_text(encoding="utf-8")))["build"]
        self.assertIn("          TAG: ${{ needs.tag.outputs.tag }}\n", body)
        self.assertIn('run: python3 "$GITHUB_WORKSPACE/.github/scripts/release/set_version.py" dashboard/server/Cargo.toml', body)
        self.assertNotIn("sed -i", body)


class SigningScripts(unittest.TestCase):
    def test_the_key_is_never_an_argument(self):
        text = SIGN.read_text(encoding="utf-8")
        self.assertIn('os.environ.get("GLYNDOR_RELEASE_ED25519_KEY"', text)
        for line in strip_comments(RELEASE.read_text(encoding="utf-8")).splitlines():
            if "sign.py" in line:
                self.fail(f"the workflow calls sign.py directly: {line}")
        self.assertIn('sign.py" "$file"', SIGN_ARTIFACTS.read_text(encoding="utf-8"))

    def run_sign(self, args, key):
        env = {k: v for k, v in os.environ.items() if k != "GLYNDOR_RELEASE_ED25519_KEY"}
        if key is not None:
            env["GLYNDOR_RELEASE_ED25519_KEY"] = key
        return subprocess.run(["python3", str(SIGN), *args], env=env, capture_output=True, text=True)

    def test_without_the_key_in_the_environment_nothing_is_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "a"
            f.write_text("x")
            self.assertEqual(self.run_sign([str(f)], None).returncode, 1)
            self.assertFalse((pathlib.Path(tmp) / "a.sig").exists())

    def test_a_key_given_as_an_argument_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "a"
            f.write_text("x")
            done = self.run_sign(["AAAA", str(f), str(f) + ".sig", "extra"], "AAAA")
            self.assertEqual(done.returncode, 1)

    def test_signing_with_a_key_of_the_environment_writes_a_64_byte_signature(self):
        try:
            import base64
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            self.skipTest("the cryptography module is not installed here")
        seed = os.urandom(32)
        key = base64.b64encode(seed).decode()
        with tempfile.TemporaryDirectory() as tmp:
            f = pathlib.Path(tmp) / "a"
            f.write_bytes(b"payload")
            done = self.run_sign([str(f)], key)
            self.assertEqual(done.returncode, 0, done.stderr)
            sig = (pathlib.Path(tmp) / "a.sig").read_bytes()
            self.assertEqual(len(sig), 64)
            Ed25519PrivateKey.from_private_bytes(seed).public_key().verify(sig, b"payload")

    EXPECTED = {
        arch: sorted(
            [
                f"helmly-dashboard-backend-linux-{arch}",
                f"helmly-dashboard-frontend-assets-linux-{arch}.tar.gz",
                f"helmly-dashboard-frontend-linux-{arch}",
            ]
        )
        for arch in ("x86_64", "arm64")
    }
    STUB = (
        "import importlib.util, json, sys\n"
        "spec = importlib.util.find_spec('cryptography')\n"
        "open(sys.argv[1] + '.sig', 'w').write(json.dumps({'path': sys.path, 'cryptography': spec.origin if spec else None}))\n"
    )

    def build(self, tmp, extra=None, links=None, drop=None, folders=None):
        """A fake workspace with a stand-in sign.py and an artifacts directory, optionally with
        something wrong in it."""
        tmp = pathlib.Path(tmp)
        work = tmp / "work"
        (work / ".github/scripts").mkdir(parents=True)
        (work / ".github/scripts/sign.py").write_text(self.STUB)
        data = tmp / "artifacts"
        for arch, names in self.EXPECTED.items():
            (data / arch).mkdir(parents=True)
            for name in names:
                if name != drop:
                    (data / arch / name).write_text("x")
        for rel, text in (extra or {}).items():
            path = data / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        for rel, target in (links or {}).items():
            path = data / rel
            if path.exists() or path.is_symlink():
                path.unlink()
            path.symlink_to(target)
        for folder in folders or []:
            (data / folder).mkdir(parents=True, exist_ok=True)
        return work, data

    def sign(self, tmp, work, data, key="AAAA"):
        env = {k: v for k, v in os.environ.items() if k != "GLYNDOR_RELEASE_ED25519_KEY"}
        env.update({"GITHUB_WORKSPACE": str(work)})
        if key is not None:
            env["GLYNDOR_RELEASE_ED25519_KEY"] = key
        done = subprocess.run(["bash", str(SIGN_ARTIFACTS), str(data)], cwd=tmp, env=env, capture_output=True, text=True)
        signed = sorted(p.relative_to(data).as_posix() for p in data.rglob("*.sig"))
        return done, signed

    def test_the_six_expected_files_are_signed_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            done, signed = self.sign(tmp, work, data)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(len(signed), 6)

    def test_a_file_with_a_name_more_is_refused_before_anything_is_signed(self):
        for extra in ("x86_64/helmly-dashboard-extra", "arm64/notes.txt", "x86_64/.hidden", "stray"):
            with tempfile.TemporaryDirectory() as tmp:
                work, data = self.build(tmp, extra={extra: "x"})
                done, signed = self.sign(tmp, work, data)
                self.assertEqual(done.returncode, 1, extra)
                self.assertEqual(signed, [], extra)

    def test_a_missing_artifact_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp, drop="helmly-dashboard-backend-linux-arm64")
            done, signed = self.sign(tmp, work, data)
            self.assertEqual((done.returncode, signed), (1, []))

    def test_an_artifact_that_is_a_symbolic_link_is_refused_before_anything_is_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "elsewhere"
            target.write_text("x")
            work, data = self.build(tmp, links={"x86_64/helmly-dashboard-backend-linux-x86_64": str(target)})
            done, signed = self.sign(tmp, work, data)
            self.assertEqual((done.returncode, signed), (1, []))
            self.assertIn("not a regular file", done.stderr + done.stdout)

    def test_a_folder_that_is_a_link_or_a_folder_in_place_of_a_file_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            other = pathlib.Path(tmp) / "other"
            other.mkdir()
            shutil.rmtree(data / "arm64")
            (data / "arm64").symlink_to(other)
            self.assertEqual(self.sign(tmp, work, data)[0].returncode, 1)
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            (data / "x86_64/helmly-dashboard-backend-linux-x86_64").unlink()
            (data / "x86_64/helmly-dashboard-backend-linux-x86_64").mkdir()
            self.assertEqual(self.sign(tmp, work, data)[0].returncode, 1)

    def test_a_module_named_like_the_signing_library_in_the_data_is_never_imported(self):
        import json
        planted = "raise SystemExit('the planted module was imported')\n"
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp, extra={})
            (data / "cryptography.py").write_text(planted)
            done, signed = self.sign(tmp, work, data)
            # the directory holds a stray file, so the signer is not even reached
            self.assertEqual((done.returncode, signed), (1, []))
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            (pathlib.Path(tmp) / "cryptography.py").write_text(planted)  # the working directory
            (work / "cryptography.py").write_text(planted)  # the workspace root
            done, signed = self.sign(tmp, work, data)
            self.assertEqual(done.returncode, 0, done.stderr)
            seen = json.loads((data / f"x86_64/{self.EXPECTED['x86_64'][0]}.sig").read_text())
            for entry in seen["path"]:
                self.assertNotIn(tmp, entry, f"{entry} is on the import path")
                self.assertNotEqual(entry, "", "the working directory is on the import path")
            self.assertTrue(seen["cryptography"] is None or tmp not in seen["cryptography"])

    def test_sign_artifacts_needs_the_key_in_the_environment(self):
        env = {k: v for k, v in os.environ.items() if k != "GLYNDOR_RELEASE_ED25519_KEY"}
        env["GITHUB_WORKSPACE"] = str(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            done = subprocess.run(["bash", str(SIGN_ARTIFACTS), str(data)], cwd=tmp, env={**env, "GITHUB_WORKSPACE": str(work)}, capture_output=True, text=True)
            self.assertEqual(done.returncode, 1)
            self.assertEqual(sorted(data.rglob("*.sig")), [])

    def test_a_relative_directory_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work, data = self.build(tmp)
            env = {**os.environ, "GLYNDOR_RELEASE_ED25519_KEY": "AAAA", "GITHUB_WORKSPACE": str(work)}
            done = subprocess.run(["bash", str(SIGN_ARTIFACTS), "artifacts"], cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
