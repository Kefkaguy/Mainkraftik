//! CPython 64-bit SipHash13/string-set iteration for the existing discovery Counter.
//! Algorithm references: CPython v3.12.3 Objects/setobject.c, Python/pyhash.c,
//! Python/bootstrap_hash.c. PSF-2.0 license is retained with the reference receipt.
//! No deletion or generic Python objects; news symbols are strings.
use rand::RngCore;
use siphasher::sip::SipHasher13;
use std::{collections::BTreeMap, hash::Hasher, sync::OnceLock};
pub const MAX_SYMBOLS: usize = 100_000;
pub const MAX_SYMBOL_BYTES: usize = 8 * 1024 * 1024;
pub type SetResult<T> = Result<T, &'static str>;
pub struct Seed {
    keys: [u64; 2],
}
impl Seed {
    pub fn from_integer(mut value: u32) -> Self {
        let mut bytes = [0_u8; 16];
        if value != 0 {
            for byte in &mut bytes {
                value = value.wrapping_mul(214013).wrapping_add(2531011);
                *byte = (value >> 16) as u8;
            }
        }
        Self::from_bytes(bytes)
    }
    fn from_bytes(bytes: [u8; 16]) -> Self {
        Self {
            keys: [
                u64::from_ne_bytes(bytes[..8].try_into().expect("eight bytes")),
                u64::from_ne_bytes(bytes[8..].try_into().expect("eight bytes")),
            ],
        }
    }
}
pub fn process_seed() -> SetResult<&'static Seed> {
    static SEED: OnceLock<SetResult<Seed>> = OnceLock::new();
    SEED.get_or_init(|| match std::env::var("PYTHONHASHSEED") {
        Ok(text) if text != "random" => {
            let value = text
                .parse::<u32>()
                .map_err(|_| "Invalid PYTHONHASHSEED compatibility setting")?;
            Ok(Seed::from_integer(value))
        }
        Err(std::env::VarError::NotUnicode(_)) => {
            Err("Invalid PYTHONHASHSEED compatibility setting")
        }
        _ => {
            let mut bytes = [0; 16];
            rand::rngs::OsRng
                .try_fill_bytes(&mut bytes)
                .map_err(|_| "Hash randomization unavailable")?;
            Ok(Seed::from_bytes(bytes))
        }
    })
    .as_ref()
    .map_err(|error| *error)
}
pub fn hash_string(value: &str, seed: &Seed) -> i64 {
    if value.is_empty() {
        return 0;
    }
    let max = value.chars().map(u32::from).max().unwrap_or(0);
    let mut bytes = Vec::new();
    for ch in value.chars().map(u32::from) {
        if max <= 255 {
            bytes.push(ch as u8);
        } else if max <= 65535 {
            bytes.extend_from_slice(&(ch as u16).to_ne_bytes());
        } else {
            bytes.extend_from_slice(&ch.to_ne_bytes());
        }
    }
    let mut hash = SipHasher13::new_with_keys(seed.keys[0], seed.keys[1]);
    hash.write(&bytes);
    match hash.finish() as i64 {
        -1 => -2,
        value => value,
    }
}
fn place(table: &mut [Option<(String, i64)>], entry: (String, i64)) -> bool {
    let mask = table.len() - 1;
    let mut perturb = entry.1 as u64;
    let mut index = perturb as usize & mask;
    loop {
        let linear = if index + 9 <= mask { 9 } else { 0 };
        for offset in 0..=linear {
            match &table[index + offset] {
                Some(old) if old.1 == entry.1 && old.0 == entry.0 => return false,
                None => {
                    table[index + offset] = Some(entry);
                    return true;
                }
                _ => {}
            }
        }
        perturb >>= 5;
        index = index
            .wrapping_mul(5)
            .wrapping_add(1)
            .wrapping_add(perturb as usize)
            & mask;
    }
}
pub fn ordered(values: &[String], seed: &Seed) -> SetResult<Vec<String>> {
    if usize::BITS != 64 || !cfg!(target_endian = "little") {
        return Err("Unvalidated Python set platform");
    }
    let bytes = values
        .iter()
        .try_fold(0_usize, |n, s| n.checked_add(s.len()));
    if values.len() > MAX_SYMBOLS || bytes.is_none_or(|n| n > MAX_SYMBOL_BYTES) {
        return Err("News symbol set exceeds resource boundary");
    }
    let mut table = vec![None; 8];
    let mut used = 0_usize;
    for value in values {
        if place(&mut table, (value.clone(), hash_string(value, seed))) {
            used += 1;
            if used * 5 >= (table.len() - 1) * 3 {
                let minimum = used * if used > 50_000 { 2 } else { 4 };
                let mut size = 8;
                while size <= minimum {
                    size *= 2;
                }
                let previous = std::mem::replace(&mut table, vec![None; size]);
                for entry in previous.into_iter().flatten() {
                    if !place(&mut table, entry) {
                        return Err("Python set reconstruction failed");
                    }
                }
            }
        }
    }
    Ok(table.into_iter().flatten().map(|entry| entry.0).collect())
}
pub fn ranked<'a>(
    articles: impl IntoIterator<Item = &'a [String]>,
    mut valid: impl FnMut(&str) -> bool,
    seed: &Seed,
) -> SetResult<Vec<(String, usize)>> {
    let mut counts = Vec::<(String, usize)>::new();
    let mut indices = BTreeMap::<String, usize>::new();
    let mut total_symbols = 0_usize;
    let mut total_bytes = 0_usize;
    for article in articles {
        total_symbols = total_symbols
            .checked_add(article.len())
            .ok_or("News symbol count exceeds resource boundary")?;
        total_bytes = article
            .iter()
            .try_fold(total_bytes, |n, s| n.checked_add(s.len()))
            .ok_or("News symbol bytes exceed resource boundary")?;
        if total_symbols > MAX_SYMBOLS || total_bytes > MAX_SYMBOL_BYTES {
            return Err("News symbol collection exceeds resource boundary");
        }
        for value in ordered(article, seed)? {
            if !valid(&value) {
                continue;
            }
            if let Some(&index) = indices.get(&value) {
                counts[index].1 += 1;
            } else {
                indices.insert(value.clone(), counts.len());
                counts.push((value, 1));
            }
        }
    }
    counts.sort_by(|a, b| b.1.cmp(&a.1));
    counts.truncate(60);
    Ok(counts)
}
