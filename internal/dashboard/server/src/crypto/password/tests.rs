use super::*;
use uuid::Uuid;

/// A password minted for the test, so no secret-looking literal sits in the source.
fn fixture_password() -> String {
    format!("{}-Aa1!", Uuid::now_v7().simple())
}

#[test]
fn hash_and_verify_correct() {
    let pw = fixture_password();
    let h = hash(&pw).expect("hash");
    assert!(
        verify(&pw, &h).expect("verify"),
        "correct password must verify"
    );
}

#[test]
fn wrong_password_rejected() {
    let h = hash(&fixture_password()).expect("hash");
    assert!(
        !verify(&fixture_password(), &h).expect("verify"),
        "wrong password must not verify"
    );
}

#[test]
fn hashes_differ_for_same_input() {
    // Different salts → different hashes
    let pw = fixture_password();
    let h1 = hash(&pw).expect("h1");
    let h2 = hash(&pw).expect("h2");
    assert_ne!(h1, h2, "each hash must use a fresh salt");
}

#[test]
fn dummy_verify_does_not_panic() {
    // verify_dummy must never panic regardless of input
    verify_dummy(&fixture_password());
    verify_dummy(&fixture_password()[..0]); // the empty password
    verify_dummy(&"a".repeat(1000));
}

#[test]
fn invalid_hash_string_returns_err() {
    assert!(verify(&fixture_password(), "not-a-valid-hash").is_err());
}
