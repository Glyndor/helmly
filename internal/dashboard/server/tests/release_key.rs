//! The release verification key: one key, in every place that checks a release, and the
//! shell verifiers refuse what they must.
//!
//! The key is the file `internal/dashboard/release-verify-key.b64`. The server includes that
//! file at compile time; `install.sh`, `setup-dashboard.sh` and `update-dashboard.sh` are
//! distributed on their own and carry the same text, which these tests compare. The verifier
//! of each script is then run on signatures made here: good, absent, empty, altered, and from
//! another key.

use ed25519_dalek::{Signer, SigningKey};
use rand::Rng;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn dashboard_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("..")
}

fn repo_root() -> PathBuf {
    dashboard_dir().join("../..")
}

fn read(path: &Path) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

fn key_file() -> String {
    read(&dashboard_dir().join("release-verify-key.b64"))
        .trim()
        .to_string()
}

/// The text between `start` and the next `"` after it.
fn quoted_after(text: &str, start: &str) -> String {
    let at = text
        .find(start)
        .unwrap_or_else(|| panic!("`{start}` not found"))
        + start.len();
    let rest = &text[at..];
    rest[..rest.find('"').expect("closing quote")].to_string()
}

fn install_sh() -> String {
    read(&repo_root().join("install.sh"))
}

fn setup_sh() -> String {
    read(&dashboard_dir().join("setup-dashboard.sh"))
}

fn update_sh() -> String {
    read(&dashboard_dir().join("update-dashboard.sh"))
}

#[test]
fn the_key_file_is_one_unpadded_base64_ed25519_key() {
    let key = key_file();
    assert_eq!(key.len(), 43, "32 bytes, base64 without padding");
    assert!(!key.contains('='));
    assert!(key
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '+' || c == '/'));
}

#[test]
fn the_four_places_that_verify_a_release_carry_the_key_of_the_file() {
    let key = key_file();
    let install = {
        let t = install_sh();
        let at = t
            .find("HELMLY_RELEASE_PUBKEY_B64=\"${HELMLY_RELEASE_PUBKEY_B64:-")
            .expect("install.sh key");
        let rest = &t[at + "HELMLY_RELEASE_PUBKEY_B64=\"${HELMLY_RELEASE_PUBKEY_B64:-".len()..];
        rest[..rest.find('}').unwrap()].to_string()
    };
    let setup = quoted_after(&setup_sh(), "RELEASE_VERIFY_KEY_B64=\"");
    let update = quoted_after(&update_sh(), "pub_b64 = \"");
    assert_eq!(install, key, "install.sh");
    assert_eq!(setup, key, "setup-dashboard.sh");
    assert_eq!(update, key, "update-dashboard.sh");
    let server = read(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src/update.rs"));
    assert!(
        server.contains("include_str!(\"../../release-verify-key.b64\")"),
        "update.rs reads the file"
    );
    assert!(
        !server.contains("RELEASE_VERIFY_KEY_B64"),
        "no second copy of the key in update.rs"
    );
}

#[test]
fn the_second_install_slot_is_empty_so_only_one_key_is_trusted() {
    assert!(install_sh().contains("HELMLY_RELEASE_PUBKEY2_B64=\"${HELMLY_RELEASE_PUBKEY2_B64:-}\""));
}

// ---- the shell verifiers, run for real -----------------------------------------------

fn python() -> String {
    std::env::var("HELMLY_TEST_PYTHON").unwrap_or_else(|_| "python3".to_string())
}

/// True when the interpreter can run the verifiers. A missing `cryptography` skips these
/// tests unless REQUIRE_PY_CRYPTO is set, which CI sets, so that a runner without it fails
/// instead of passing without having looked.
fn python_can_verify() -> bool {
    let ok = Command::new(python())
        .args([
            "-c",
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey",
        ])
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !ok {
        assert!(
            std::env::var("REQUIRE_PY_CRYPTO").is_err(),
            "REQUIRE_PY_CRYPTO is set and {} cannot import cryptography",
            python()
        );
        eprintln!("skipped: {} has no cryptography module", python());
    }
    ok
}

/// The Python program of the heredoc that uses Ed25519PublicKey in a script.
fn verifier_program(script: &str) -> String {
    let mut found = None;
    let mut lines = script.lines();
    while let Some(line) = lines.next() {
        if line.contains("python3 -") && line.contains("<<'PYEOF'") {
            let body: Vec<&str> = lines.by_ref().take_while(|l| l.trim() != "PYEOF").collect();
            let text = body.join("\n");
            if text.contains("Ed25519PublicKey") && text.contains("verify") {
                found = Some(text);
                break;
            }
        }
    }
    found.expect("a python verifier heredoc")
}

struct Case {
    dir: PathBuf,
    data: PathBuf,
    sig: PathBuf,
}

fn b64(bytes: &[u8]) -> String {
    use base64ct::{Base64Unpadded, Encoding};
    Base64Unpadded::encode_string(bytes)
}

fn case(data: &[u8], sig: Option<&[u8]>) -> Case {
    let dir = std::env::temp_dir().join(format!("helmly-release-key-{}", uuid::Uuid::now_v7()));
    std::fs::create_dir_all(&dir).unwrap();
    let data_path = dir.join("payload");
    let sig_path = dir.join("payload.sig");
    std::fs::write(&data_path, data).unwrap();
    if let Some(sig) = sig {
        std::fs::write(&sig_path, sig).unwrap();
    }
    Case {
        dir,
        data: data_path,
        sig: sig_path,
    }
}

fn run(program: &str, args: &[&str]) -> bool {
    let mut child = Command::new(python())
        .arg("-")
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn python");
    child
        .stdin
        .take()
        .unwrap()
        .write_all(program.as_bytes())
        .unwrap();
    child.wait().unwrap().success()
}

fn key() -> SigningKey {
    let mut seed = [0u8; 32];
    rand::rng().fill_bytes(&mut seed);
    SigningKey::from_bytes(&seed)
}

/// One verifier, as a function of (key as base64, payload, signature file) -> accepted.
type Verify = Box<dyn Fn(&str, &Case) -> bool>;

fn verifiers() -> Vec<(&'static str, Verify)> {
    let setup = verifier_program(&setup_sh());
    let update = verifier_program(&update_sh());
    let install = verifier_program(&install_sh());
    let embedded_key = key_file();
    let update_with_key = move |key: &str| {
        update.replace(
            &format!("pub_b64 = \"{embedded_key}\""),
            &format!("pub_b64 = \"{key}\""),
        )
    };
    vec![
        (
            "setup-dashboard.sh",
            Box::new(move |k, c| {
                run(
                    &setup,
                    &[k, c.data.to_str().unwrap(), c.sig.to_str().unwrap()],
                )
            }),
        ),
        (
            "update-dashboard.sh",
            Box::new(move |k, c| {
                run(
                    &update_with_key(k),
                    &[c.data.to_str().unwrap(), c.sig.to_str().unwrap()],
                )
            }),
        ),
        (
            "install.sh",
            Box::new(move |k, c| {
                run(
                    &install,
                    &[c.sig.to_str().unwrap(), c.data.to_str().unwrap(), k],
                )
            }),
        ),
    ]
}

#[test]
fn each_shell_verifier_accepts_a_good_signature() {
    if !python_can_verify() {
        return;
    }
    for (name, verify) in verifiers() {
        let signer = key();
        let sig = signer.sign(b"payload").to_bytes();
        let c = case(b"payload", Some(&sig));
        assert!(
            verify(&b64(&signer.verifying_key().to_bytes()), &c),
            "{name} rejected a good signature"
        );
        std::fs::remove_dir_all(&c.dir).ok();
    }
}

#[test]
fn each_shell_verifier_refuses_an_absent_empty_altered_or_foreign_signature() {
    if !python_can_verify() {
        return;
    }
    for (name, verify) in verifiers() {
        let signer = key();
        let pub_b64 = b64(&signer.verifying_key().to_bytes());
        let good = signer.sign(b"payload").to_bytes();

        let absent = case(b"payload", None);
        assert!(
            !verify(&pub_b64, &absent),
            "{name} accepted an absent signature"
        );

        let empty = case(b"payload", Some(&[]));
        assert!(
            !verify(&pub_b64, &empty),
            "{name} accepted an empty signature"
        );

        let altered_payload = case(b"payloaD", Some(&good));
        assert!(
            !verify(&pub_b64, &altered_payload),
            "{name} accepted an altered payload"
        );

        let mut bent = good;
        bent[7] ^= 0x01;
        let altered_sig = case(b"payload", Some(&bent));
        assert!(
            !verify(&pub_b64, &altered_sig),
            "{name} accepted an altered signature"
        );

        let foreign = key().sign(b"payload").to_bytes();
        let other_key = case(b"payload", Some(&foreign));
        assert!(
            !verify(&pub_b64, &other_key),
            "{name} accepted a signature of another key"
        );

        for c in [absent, empty, altered_payload, altered_sig, other_key] {
            std::fs::remove_dir_all(&c.dir).ok();
        }
    }
}

#[test]
fn the_keys_baked_into_the_scripts_refuse_a_signature_of_an_unrelated_key() {
    if !python_can_verify() {
        return;
    }
    let embedded = key_file();
    let foreign = key().sign(b"payload").to_bytes();
    let c = case(b"payload", Some(&foreign));
    let setup = verifier_program(&setup_sh());
    assert!(!run(
        &setup,
        &[&embedded, c.data.to_str().unwrap(), c.sig.to_str().unwrap()]
    ));
    let update = verifier_program(&update_sh());
    assert!(!run(
        &update,
        &[c.data.to_str().unwrap(), c.sig.to_str().unwrap()]
    ));
    let install = verifier_program(&install_sh());
    assert!(!run(
        &install,
        &[c.sig.to_str().unwrap(), c.data.to_str().unwrap(), &embedded]
    ));
    std::fs::remove_dir_all(&c.dir).ok();
}
