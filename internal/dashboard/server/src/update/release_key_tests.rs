//! Tests of the release signature check and of the key it embeds: a good signature is
//! accepted; an absent, truncated, altered or foreign one is refused; and the embedded key is
//! the unpadded base64 key of `internal/dashboard/release-verify-key.b64`.

use super::*;

use ed25519_dalek::{Signer, SigningKey};
use rand::Rng;

fn random_key() -> SigningKey {
    let mut seed = [0u8; 32];
    rand::rng().fill_bytes(&mut seed);
    SigningKey::from_bytes(&seed)
}

#[test]
fn the_embedded_key_is_the_file_and_is_32_bytes() {
    let key = load_release_verify_key().expect("embedded key decodes");
    assert_eq!(key.len(), 32);
    assert!(VerifyingKey::from_bytes(&key).is_ok());
    assert!(
        !RELEASE_VERIFY_KEY_FILE.trim().contains('='),
        "stored without padding"
    );
}

#[test]
fn a_padded_or_short_key_is_refused() {
    assert!(decode_verify_key("HFv7vg5FCY7YyKUDbJhaQSfB9SboJGSblJtFbLmLHzM=").is_err());
    assert!(decode_verify_key("AAAA").is_err());
    assert!(decode_verify_key("").is_err());
}

#[test]
fn a_good_signature_verifies() {
    let key = random_key();
    let data = b"release payload";
    let sig = key.sign(data).to_bytes();
    assert!(verify_with_key(&key.verifying_key().to_bytes(), data, &sig).is_ok());
}

#[test]
fn an_absent_signature_is_refused() {
    let key = random_key();
    assert!(verify_with_key(&key.verifying_key().to_bytes(), b"payload", &[]).is_err());
}

#[test]
fn a_truncated_or_padded_signature_is_refused() {
    let key = random_key();
    let sig = key.sign(b"payload").to_bytes();
    let verifying = key.verifying_key().to_bytes();
    assert!(verify_with_key(&verifying, b"payload", &sig[..63]).is_err());
    let mut long = sig.to_vec();
    long.push(0);
    assert!(verify_with_key(&verifying, b"payload", &long).is_err());
}

#[test]
fn an_altered_payload_is_refused() {
    let key = random_key();
    let sig = key.sign(b"payload").to_bytes();
    assert!(verify_with_key(&key.verifying_key().to_bytes(), b"payloaD", &sig).is_err());
}

#[test]
fn an_altered_signature_is_refused() {
    let key = random_key();
    let mut sig = key.sign(b"payload").to_bytes();
    sig[10] ^= 0x01;
    assert!(verify_with_key(&key.verifying_key().to_bytes(), b"payload", &sig).is_err());
}

#[test]
fn a_signature_of_another_key_is_refused() {
    let signer = random_key();
    let other = random_key();
    let sig = signer.sign(b"payload").to_bytes();
    assert!(verify_with_key(&other.verifying_key().to_bytes(), b"payload", &sig).is_err());
}

#[test]
fn the_embedded_key_refuses_a_signature_of_an_unrelated_key() {
    let sig = random_key().sign(b"payload").to_bytes();
    assert!(verify_signature(b"payload", &sig).is_err());
    assert!(verify_signature(b"payload", &[]).is_err());
}
