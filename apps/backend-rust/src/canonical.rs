//! Python canonical JSON identity compatibility, including ASCII escapes and f64 repr.
use crate::{Result, invalid};
use serde_json::Value;
use sha2::{Digest, Sha256};

fn float_repr(value: f64) -> Result<String> {
    if !value.is_finite() {
        return Err(invalid("non-finite canonical number"));
    }
    if value == 0.0 {
        return Ok(if value.is_sign_negative() {
            "-0.0"
        } else {
            "0.0"
        }
        .into());
    }
    let mut buffer = ryu::Buffer::new();
    let raw = buffer.format_finite(value);
    let (sign, raw) = raw.strip_prefix('-').map_or(("", raw), |s| ("-", s));
    let (mantissa, exponent) = match raw.split_once('e') {
        Some((m, e)) => (m, e.parse::<i32>().map_err(|_| invalid("float exponent"))?),
        None => (raw, 0),
    };
    let decimal = mantissa.find('.').unwrap_or(mantissa.len()) as i32;
    let all: String = mantissa.chars().filter(|c| *c != '.').collect();
    let leading = all.bytes().take_while(|b| *b == b'0').count();
    let digits = all[leading..].trim_end_matches('0');
    let order = exponent + decimal - leading as i32 - 1;
    let first = digits
        .chars()
        .next()
        .ok_or_else(|| invalid("empty float digits"))?;
    if !(-4..16).contains(&order) {
        let fraction = if digits.len() > 1 {
            format!(".{}", &digits[1..])
        } else {
            String::new()
        };
        return Ok(format!(
            "{sign}{first}{fraction}e{}{:02}",
            if order < 0 { '-' } else { '+' },
            order.abs()
        ));
    }
    let point = order + 1;
    let fixed = if point <= 0 {
        format!("0.{}{}", "0".repeat((-point) as usize), digits)
    } else if point as usize >= digits.len() {
        format!("{}{}.0", digits, "0".repeat(point as usize - digits.len()))
    } else {
        format!(
            "{}.{}",
            &digits[..point as usize],
            &digits[point as usize..]
        )
    };
    Ok(format!("{sign}{fixed}"))
}

fn string(value: &str, out: &mut String) {
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ch if ch < '\u{20}' || !ch.is_ascii() => {
                let mut units = [0u16; 2];
                for unit in ch.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            _ => out.push(ch),
        }
    }
    out.push('"');
}

fn append(value: &Value, out: &mut String) -> Result<()> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if n.is_f64() {
                out.push_str(&float_repr(n.as_f64().ok_or_else(|| invalid("number"))?)?);
            } else {
                out.push_str(&n.to_string());
            }
        }
        Value::String(s) => string(s, out),
        Value::Array(rows) => {
            out.push('[');
            for (i, row) in rows.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                append(row, out)?;
            }
            out.push(']');
        }
        Value::Object(obj) => {
            out.push('{');
            let mut keys: Vec<_> = obj.keys().collect();
            keys.sort();
            for (i, key) in keys.into_iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                string(key, out);
                out.push(':');
                append(&obj[key], out)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

pub fn canonical(value: &Value) -> Result<String> {
    let mut out = String::new();
    append(value, &mut out)?;
    Ok(out)
}
pub fn digest(value: &Value) -> Result<String> {
    Ok(hex::encode(Sha256::digest(canonical(value)?.as_bytes())))
}

/// The same Python JSON bytes, with a caller-supplied allocation ceiling.
/// Spaced mode matches json.dumps(sort_keys=True)'s default separators.
pub fn bounded(value: &Value, limit: usize, spaced: bool) -> Result<Vec<u8>> {
    fn push(out: &mut Vec<u8>, text: &[u8], limit: usize) -> Result<()> {
        if text.len() > limit.saturating_sub(out.len()) {
            return Err(invalid("Research evidence exceeds configured byte limit"));
        }
        out.extend_from_slice(text);
        Ok(())
    }
    fn quoted(text: &str, out: &mut Vec<u8>, limit: usize) -> Result<()> {
        push(out, b"\"", limit)?;
        for c in text.chars() {
            match c {
                '"' => push(out, b"\\\"", limit)?,
                '\\' => push(out, b"\\\\", limit)?,
                '\n' => push(out, b"\\n", limit)?,
                '\r' => push(out, b"\\r", limit)?,
                '\t' => push(out, b"\\t", limit)?,
                '\u{8}' => push(out, b"\\b", limit)?,
                '\u{c}' => push(out, b"\\f", limit)?,
                c if c < '\u{20}' || !c.is_ascii() => {
                    let mut units = [0; 2];
                    for unit in c.encode_utf16(&mut units) {
                        push(out, format!("\\u{unit:04x}").as_bytes(), limit)?;
                    }
                }
                c => push(out, &[c as u8], limit)?,
            }
        }
        push(out, b"\"", limit)
    }
    fn encode(
        value: &Value,
        out: &mut Vec<u8>,
        limit: usize,
        spaced: bool,
        depth: usize,
    ) -> Result<()> {
        if depth >= 128 {
            return Err(invalid("Research evidence exceeds JSON nesting limit"));
        }
        let comma = if spaced { b", ".as_slice() } else { b"," };
        let colon = if spaced { b": ".as_slice() } else { b":" };
        match value {
            Value::String(s) => quoted(s, out, limit),
            Value::Array(items) => {
                if items.len() > limit {
                    return Err(invalid("Research evidence exceeds configured byte limit"));
                }
                push(out, b"[", limit)?;
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        push(out, comma, limit)?;
                    }
                    encode(item, out, limit, spaced, depth + 1)?;
                }
                push(out, b"]", limit)
            }
            Value::Object(items) => {
                if items.len() > limit / 4 {
                    return Err(invalid("Research evidence exceeds configured byte limit"));
                }
                let mut keys: Vec<_> = items.keys().collect();
                keys.sort_unstable();
                push(out, b"{", limit)?;
                for (i, key) in keys.into_iter().enumerate() {
                    if i > 0 {
                        push(out, comma, limit)?;
                    }
                    quoted(key, out, limit)?;
                    push(out, colon, limit)?;
                    encode(&items[key], out, limit, spaced, depth + 1)?;
                }
                push(out, b"}", limit)
            }
            _ => push(out, canonical(value)?.as_bytes(), limit),
        }
    }
    let mut out = Vec::new();
    encode(value, &mut out, limit, spaced, 0)?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_exponents_and_unicode() {
        let v: Value =
            serde_json::from_str(r#"[1e-7,1e-5,1e-4,1e16,1e15,-0.0,"λ😀"]"#).expect("fixture");
        assert_eq!(
            canonical(&v).expect("canonical"),
            r#"[1e-07,1e-05,0.0001,1e+16,1000000000000000.0,-0.0,"\u03bb\ud83d\ude00"]"#
        );
    }
}
