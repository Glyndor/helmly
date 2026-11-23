//! Data that an installation already holds must keep opening: vectors made by the code that
//! shipped with `aes-gcm` 0.10.3 (the key is the bytes 0 to 31, the nonces are fixed), and the
//! published test case 16 of the GCM specification as an independent check of the algorithm.
//! A change of the crate may change the API; it may not change one byte of these.
use super::kek::{decrypt_dek, decrypt_with_dek, encrypt_with_dek};
use aes_gcm::{
    aead::{Aead, KeyInit, Payload},
    Aes256Gcm, Key, Nonce,
};

struct Vector {
    name: &'static str,
    nonce: &'static str,
    aad: &'static str,
    plain: &'static str,
    sealed: &'static str,
}

/// nonce, additional data, plaintext and the ciphertext with its 16-byte tag, as hex.
const VECTORS: &[Vector] = &[
    Vector {
        name: "empty",
        nonce: "101112131415161718191a1b",
        aad: "",
        plain: "",
        sealed: "018f5e9d66e6fbec03896ee4b1e8d030",
    },
    Vector {
        name: "a data key",
        nonce: "a0a1a2a3a4a5a6a7a8a9aaab",
        aad: "",
        plain: "404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f",
        sealed: "a6593e6e018e44f82a2ccd984b378e9120fd0b43c6e2143bc4577cdd23f62b5e33c1bcb4fb7410add3e85e156ba08e54",
    },
    Vector {
        name: "text",
        nonce: "101112131415161718191a1b",
        aad: "",
        plain: "68656c6c6f2068656c6d6c7920776f726c64",
        sealed: "159bf47a26e952d6a61864642f0e0621bb3426a37b8c62b18d500253d1244c1fbe66",
    },
    Vector {
        name: "a hundred bytes",
        nonce: "a0a1a2a3a4a5a6a7a8a9aaab",
        aad: "",
        plain: "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f404142434445464748494a4b4c4d4e4f505152535455565758595a5b5c5d5e5f60616263",
        sealed: "e6197e2e41ce04b86a6c8dd80b77ced160bd4b0386a2547b84173c9d63b66b1ef25765dc8b07751a77b52ee32557add6772a747b56e52c4979673165984dbb8ff4fdc72c74e0a2a7bcabb553c38382f59d5af9f79194cf2db62c64c1edc713cbefe549c8b4189847eeee7ddf796aaa7cac6e0839",
    },
    Vector {
        name: "with additional data",
        nonce: "101112131415161718191a1b",
        aad: "68656c6d6c793a64656b3a7631",
        plain: "00070e151c232a31383f464d545b626970777e858c939aa1a8afb6bdc4cbd2d9e0e7eef5fc030a11",
        sealed: "7df9960355ea1082f24a4e505b220b3aa727308b9751cd104f5654da9a9f8602b00eb9f609a564feb982c547ab6da870e9f2bf61e2c32186",
    },
];

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).expect("hex"))
        .collect()
}

fn fixed_bytes() -> [u8; 32] {
    core::array::from_fn(|i| i as u8)
}

fn seal(key: &[u8; 32], nonce: &[u8], aad: &[u8], plain: &[u8]) -> Vec<u8> {
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    cipher
        .encrypt(Nonce::from_slice(nonce), Payload { msg: plain, aad })
        .expect("seal")
}

fn open(key: &[u8; 32], nonce: &[u8], aad: &[u8], sealed: &[u8]) -> Option<Vec<u8>> {
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    cipher
        .decrypt(Nonce::from_slice(nonce), Payload { msg: sealed, aad })
        .ok()
}

/// The layout the application stores: the nonce, then the ciphertext and the tag.
fn stored(v: &Vector) -> Vec<u8> {
    [unhex(v.nonce), unhex(v.sealed)].concat()
}

#[test]
fn what_an_existing_installation_stored_still_decrypts() {
    let key = fixed_bytes();
    for v in VECTORS.iter().filter(|v| v.aad.is_empty()) {
        let plain =
            decrypt_with_dek(&stored(v), &key).unwrap_or_else(|e| panic!("{}: {e}", v.name));
        assert_eq!(plain, unhex(v.plain), "{}", v.name);
    }
}

#[test]
fn a_stored_data_key_still_unwraps() {
    let kek = fixed_bytes();
    let wrapped = stored(&VECTORS[1]);
    assert_eq!(
        &*decrypt_dek(&wrapped, &kek).expect("unwrap"),
        unhex(VECTORS[1].plain).as_slice()
    );
}

#[test]
fn the_same_key_nonce_and_data_give_the_same_bytes() {
    let key = fixed_bytes();
    for v in VECTORS {
        let sealed = seal(&key, &unhex(v.nonce), &unhex(v.aad), &unhex(v.plain));
        assert_eq!(sealed, unhex(v.sealed), "{}", v.name);
    }
}

#[test]
fn additional_data_is_part_of_what_is_checked() {
    let key = fixed_bytes();
    let v = &VECTORS[4];
    let (nonce, sealed) = (unhex(v.nonce), unhex(v.sealed));
    assert_eq!(
        open(&key, &nonce, &unhex(v.aad), &sealed),
        Some(unhex(v.plain))
    );
    assert_eq!(
        open(&key, &nonce, b"helmly:dek:v2", &sealed),
        None,
        "other additional data"
    );
    assert_eq!(open(&key, &nonce, b"", &sealed), None, "no additional data");
}

#[test]
fn the_published_test_case_16_of_the_gcm_specification() {
    let key: [u8; 32] = unhex("feffe9928665731c6d6a8f9467308308feffe9928665731c6d6a8f9467308308")
        .try_into()
        .expect("32");
    let nonce = unhex("cafebabefacedbaddecaf888");
    let plain = unhex("d9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a721c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39");
    let aad = unhex("feedfacedeadbeeffeedfacedeadbeefabaddad2");
    let want = unhex("522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f66276fc6ece0f4e1768cddf8853bb2d551b");
    assert_eq!(seal(&key, &nonce, &aad, &plain), want);
    assert_eq!(open(&key, &nonce, &aad, &want), Some(plain));
}

#[test]
fn an_altered_tag_ciphertext_or_nonce_is_refused() {
    let key = fixed_bytes();
    let v = &VECTORS[2];
    let good = stored(v);
    assert!(decrypt_with_dek(&good, &key).is_ok());
    for position in [
        0,
        5,
        11,
        12,
        good.len() / 2,
        good.len() - 17,
        good.len() - 16,
        good.len() - 1,
    ] {
        let mut bad = good.clone();
        bad[position] ^= 0x01;
        assert!(
            decrypt_with_dek(&bad, &key).is_err(),
            "byte {position} of {} altered",
            good.len()
        );
    }
    // a nonce that is not the one the data was sealed with
    let mut other_nonce = good.clone();
    other_nonce[..12].copy_from_slice(&unhex("a0a1a2a3a4a5a6a7a8a9aaab"));
    assert!(decrypt_with_dek(&other_nonce, &key).is_err());
    // a key that is not the one it was sealed with
    let mut other_key = key;
    other_key[31] ^= 0x01;
    assert!(decrypt_with_dek(&good, &other_key).is_err());
}

#[test]
fn truncated_data_is_refused() {
    let key = fixed_bytes();
    let good = stored(&VECTORS[2]);
    for len in [0, 1, 11, 12, 13, 12 + 15, good.len() - 1] {
        assert!(
            decrypt_with_dek(&good[..len], &key).is_err(),
            "{len} bytes of {}",
            good.len()
        );
    }
    assert!(decrypt_with_dek(&[], &key).is_err());
}

#[test]
fn what_the_application_writes_opens_with_the_library_alone() {
    let key = fixed_bytes();
    for plain in [
        &b""[..],
        b"x",
        b"a longer text than one block of sixteen bytes",
    ] {
        let stored = encrypt_with_dek(plain, &key).expect("encrypt");
        assert_eq!(
            stored.len(),
            12 + plain.len() + 16,
            "the nonce, the data and the tag"
        );
        let (nonce, sealed) = stored.split_at(12);
        assert_eq!(open(&key, nonce, b"", sealed).as_deref(), Some(plain));
    }
}
