use anyhow::Result;
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2,
};
use zeroize::Zeroizing;

pub fn hash(password: &str) -> Result<String> {
    let salt = SaltString::generate(&mut OsRng);
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

pub(super) static DUMMY_HASH: &str =
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
