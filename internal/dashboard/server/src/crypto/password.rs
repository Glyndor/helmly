use anyhow::Result;
use argon2::{
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use rand::Rng;
use zeroize::Zeroizing;

pub fn hash(password: &str) -> Result<String> {
    // A new 16-byte salt for every hash, from the same generator as the keys of this module. It is
    // not taken from `argon2::password_hash::rand_core`, a feature that this crate never asks for
    // and that only builds when another crate of the stack happens to turn it on.
    let mut raw = [0u8; 16];
    rand::rng().fill_bytes(&mut raw);
    let salt = SaltString::encode_b64(&raw).map_err(|e| anyhow::anyhow!("argon2 salt: {e}"))?;
    Argon2::default()
        .hash_password(password.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|e| anyhow::anyhow!("argon2 hash: {e}"))
}

pub fn verify(password: &str, hash: &str) -> Result<bool> {
    let parsed = PasswordHash::new(hash).map_err(|e| anyhow::anyhow!("parse hash: {e}"))?;
    Ok(Argon2::default()
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

static DUMMY_HASH: &str =
    "$argon2id$v=19$m=19456,t=2,p=1$c29tZXNhbHRzb21lc2FsdA$VtJfVEBMQG4mOXlm5M5Fl8bAHYE5L7Hp/fSKl3y6z2E";

pub fn verify_dummy(password: &str) {
    let _ = verify(password, DUMMY_HASH);
}

pub fn zeroize_str(s: &mut String) {
    let _ = Zeroizing::new(std::mem::take(s));
}

#[cfg(test)]
mod tests {
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
    fn a_new_hash_has_the_settings_and_the_lengths_it_always_had() {
        let h = hash("correct horse").expect("hash");
        let parts: Vec<&str> = h.split('$').collect();
        assert_eq!(parts.len(), 6, "{h}");
        assert_eq!(&parts[..4], &["", "argon2id", "v=19", "m=19456,t=2,p=1"]);
        assert_eq!(parts[4].len(), 22, "a 16-byte salt in 22 characters");
        assert_eq!(parts[5].len(), 43, "a 32-byte hash in 43 characters");
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
}
