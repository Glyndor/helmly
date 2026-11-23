//! A password that an existing installation stored must keep verifying: hashes made by the code
//! that shipped with `argon2` 0.5.3 (Argon2id, version 19, 19 MiB, 2 passes, 1 lane, a 16-byte
//! salt and a 32-byte hash, with salts that are fixed here so that the bytes can be compared).
//! A change of the crate may change the API; it may not change one character of these, nor the
//! parameters that new hashes are made with.
use super::password::{hash, verify, verify_dummy};
use argon2::{
    password_hash::{PasswordHasher, SaltString},
    Argon2,
};

struct Vector {
    name: &'static str,
    password: &'static str,
    salt: &'static [u8],
    phc: &'static str,
}

const FIRST_SALT: &[u8] = b"helmly-fixed-salt";
const SECOND_SALT: &[u8] = b"another-salt-16b";

const VECTORS: &[Vector] = &[
    Vector {
        name: "empty",
        password: "",
        salt: FIRST_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$aGVsbWx5LWZpeGVkLXNhbHQ$hryg7klABf6HgpgRh/b56Ba3NqkUsWNXwDC0JjUkgJM",
    },
    Vector {
        name: "short",
        password: "correct horse",
        salt: FIRST_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$aGVsbWx5LWZpeGVkLXNhbHQ$2TKqNEHbXi4nBj5vTo71UzhVtXmBxkODNW/rOLJP1JM",
    },
    Vector {
        name: "the same text with another salt",
        password: "correct horse",
        salt: SECOND_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$YW5vdGhlci1zYWx0LTE2Yg$DBqbIqvGxrSjSf5jEFA/G9Nvo4TBRY/zhZ5VUWsVAGU",
    },
    Vector {
        name: "a hundred letters",
        password: concat!("aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa", "aaaaaaaaaa"),
        salt: FIRST_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$aGVsbWx5LWZpeGVkLXNhbHQ$9c2lfP6VIdZEQC79k0rDQdkYn1mjkCVp6hcHxLKnENk",
    },
    Vector {
        name: "non-ASCII text",
        password: "pässwörd-日本語-✓",
        salt: FIRST_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$aGVsbWx5LWZpeGVkLXNhbHQ$ZUwoNxkirG/PdI9yOmdtEsPxNEJ7a/0e6HzCyRnr7oM",
    },
    Vector {
        name: "punctuation and quotes",
        password: r#"Tr0ub4dor&3 \"quoted\" $x"#,
        salt: FIRST_SALT,
        phc: "$argon2id$v=19$m=19456,t=2,p=1$aGVsbWx5LWZpeGVkLXNhbHQ$sMEF5jb7pEwv8K9Q23H8NRvC4lrjgWlaHRNGfDwgWTc",
    },
];

/// The hash of a text with a given salt, with the default settings of the crate, as the code did.
fn with_salt(text: &str, salt: &[u8]) -> String {
    let salt = SaltString::encode_b64(salt).expect("a salt");
    Argon2::default()
        .hash_password(text.as_bytes(), &salt)
        .expect("hash")
        .to_string()
}

#[test]
fn what_an_existing_installation_stored_still_verifies() {
    for v in VECTORS {
        assert!(matches!(verify(v.password, v.phc), Ok(true)), "{}", v.name);
    }
}

#[test]
fn another_password_does_not_verify_against_a_stored_hash() {
    for v in VECTORS {
        for other in [
            "wrong",
            "correct hors",
            "correct horse ",
            &format!("{}x", v.password),
        ] {
            if other == v.password {
                continue;
            }
            assert!(
                matches!(verify(other, v.phc), Ok(false)),
                "{}: {other:?}",
                v.name
            );
        }
    }
}

#[test]
fn the_same_text_and_salt_give_the_same_characters() {
    for v in VECTORS {
        assert_eq!(with_salt(v.password, v.salt), v.phc, "{}", v.name);
    }
}

#[test]
fn a_new_hash_has_the_settings_of_the_old_ones() {
    let made = hash("correct horse").expect("hash");
    let parts: Vec<&str> = made.split('$').collect();
    assert_eq!(parts.len(), 6, "{made}");
    assert_eq!(&parts[..4], &["", "argon2id", "v=19", "m=19456,t=2,p=1"]);
    assert_eq!(parts[4].len(), 22, "a 16-byte salt in 22 characters");
    assert_eq!(parts[5].len(), 43, "a 32-byte hash in 43 characters");
    assert!(matches!(verify("correct horse", &made), Ok(true)));
    assert_ne!(
        made,
        hash("correct horse").expect("hash"),
        "a new salt each time"
    );
}

#[test]
fn the_dummy_hash_parses_and_never_accepts_a_guess() {
    for guess in ["", "wrong", "correct horse", "somesaltsomesalt"] {
        assert!(
            matches!(verify(guess, super::password::DUMMY_HASH), Ok(false)),
            "{guess:?}"
        );
        verify_dummy(guess);
    }
}

#[test]
fn a_truncated_or_altered_hash_is_never_accepted() {
    let good = VECTORS[1].phc;
    for cut in 1..good.len() {
        let bad = &good[..cut];
        assert!(
            !matches!(verify("correct horse", bad), Ok(true)),
            "cut at {cut}: {bad}"
        );
    }
    for position in [good.len() - 1, good.len() - 20, good.len() - 44, 20, 5] {
        let mut bytes = good.as_bytes().to_vec();
        bytes[position] = if bytes[position] == b'A' { b'B' } else { b'A' };
        let bad = String::from_utf8(bytes).expect("ascii");
        assert!(
            !matches!(verify("correct horse", &bad), Ok(true)),
            "byte {position}: {bad}"
        );
    }
}

#[test]
fn settings_below_the_minimum_or_that_are_not_settings_are_never_accepted() {
    let salt_and_hash = "aGVsbWx5LWZpeGVkLXNhbHQ$2TKqNEHbXi4nBj5vTo71UzhVtXmBxkODNW/rOLJP1JM";
    for head in [
        "$argon2id$v=19$m=7,t=2,p=1", // less memory than eight blocks per lane
        "$argon2id$v=19$m=19456,t=0,p=1", // no pass
        "$argon2id$v=19$m=19456,t=2,p=0", // no lane
        "$argon2id$v=19$m=0,t=0,p=0", // nothing
        "$argon2id$v=19$m=19456,t=2,p=16777216", // more lanes than there are
        "$argon2id$v=19$m=19456,t=4294967296,p=1", // a number that is not a u32
        "$argon2id$v=99$m=19456,t=2,p=1", // a version that does not exist
        "$argon2z$v=19$m=19456,t=2,p=1", // an algorithm that does not exist
        "$argon3id$v=19$m=19456,t=2,p=1",
        "$scrypt$v=19$m=19456,t=2,p=1",
    ] {
        let bad = format!("{head}${salt_and_hash}");
        assert!(!matches!(verify("correct horse", &bad), Ok(true)), "{head}");
    }
    for bad in [
        "",
        "$",
        "$argon2id$",
        "$argon2id$v=19$m=19456,t=2,p=1$",
        "$argon2id$v=19$m=19456,t=2,p=1$$",
        "not a hash",
        "$argon2id$v=19$m=19456,t=2,p=1$short$2TKqNEHbXi4nBj5vTo71UzhVtXmBxkODNW/rOLJP1JM",
    ] {
        assert!(!matches!(verify("correct horse", bad), Ok(true)), "{bad:?}");
    }
}

#[test]
fn a_setting_that_a_hash_leaves_out_takes_the_default_it_always_had() {
    let salt_and_hash = "aGVsbWx5LWZpeGVkLXNhbHQ$2TKqNEHbXi4nBj5vTo71UzhVtXmBxkODNW/rOLJP1JM";
    for head in [
        "$argon2id$v=19$m=19456,t=2",
        "$argon2id$v=19$m=19456,p=1",
        "$argon2id$v=19$t=2,p=1",
        "$argon2id$m=19456,t=2,p=1",
    ] {
        let stored = format!("{head}${salt_and_hash}");
        assert!(
            matches!(verify("correct horse", &stored), Ok(true)),
            "{head}"
        );
        assert!(matches!(verify("wrong", &stored), Ok(false)), "{head}");
    }
}
