//! Information timestamp compatibility with CPython 3.14.2 datetime.fromisoformat.
//! Safe translation of the reference parsing order; see third-party/PYTHON-LICENSE.txt.
use crate::information::MetadataError;
use chrono::{DateTime, Datelike, Duration, NaiveDate, Utc, Weekday};
use std::sync::LazyLock;

fn error(kind: &'static str, message: impl Into<String>) -> MetadataError {
    MetadataError {
        kind,
        message: message.into(),
    }
}
static NONPRINTING: LazyLock<Vec<(u32, u32)>> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../assets/python-nonprinting.json"))
        .expect("frozen Unicode ranges")
});
fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut result = String::new();
    result.push(quote);
    for c in text.chars() {
        match c {
            '\\' => result.push_str("\\\\"),
            '\t' => result.push_str("\\t"),
            '\r' => result.push_str("\\r"),
            '\n' => result.push_str("\\n"),
            c if c == quote => {
                result.push('\\');
                result.push(c);
            }
            c => {
                let code = u32::from(c);
                let i = NONPRINTING.partition_point(|(start, _)| *start <= code);
                if c != ' ' && i > 0 && code <= NONPRINTING[i - 1].1 {
                    if code <= 255 {
                        result.push_str(&format!("\\x{code:02x}"));
                    } else if code <= 65535 {
                        result.push_str(&format!("\\u{code:04x}"));
                    } else {
                        result.push_str(&format!("\\U{code:08x}"));
                    }
                } else {
                    result.push(c);
                }
            }
        }
    }
    result.push(quote);
    result
}
fn byte(text: &[u8], i: usize) -> u8 {
    text.get(i).copied().unwrap_or(0)
}
fn digits(text: &[u8], pos: &mut usize, width: usize) -> Option<u32> {
    let mut value = 0;
    for _ in 0..width {
        let b = *text.get(*pos)?;
        if !b.is_ascii_digit() {
            return None;
        }
        value = value * 10 + u32::from(b - b'0');
        *pos += 1;
    }
    Some(value)
}
fn separator(text: &[u8]) -> Option<usize> {
    let len = text.len();
    if len < 7 {
        return None;
    }
    if len == 7 {
        return Some(7);
    }
    Some(if text[4] == b'-' {
        if text[5] == b'W' {
            if len > 8 && text[8] == b'-' {
                if len == 9 {
                    return None;
                }
                if len > 10 && text[10].is_ascii_digit() {
                    8
                } else {
                    10
                }
            } else {
                8
            }
        } else {
            10
        }
    } else if text[4] == b'W' {
        let mut i = 7;
        while i < len && text[i].is_ascii_digit() {
            i += 1;
        }
        if i < 9 {
            i
        } else if i % 2 == 0 {
            7
        } else {
            8
        }
    } else {
        8
    })
}
fn date(text: &[u8], end: usize) -> Option<(i32, u32, u32)> {
    let mut pos = 0;
    let year = digits(text, &mut pos, 4)? as i32;
    let dashed = byte(text, pos) == b'-';
    pos += usize::from(dashed);
    if byte(text, pos) == b'W' {
        pos += 1;
        let week = digits(text, &mut pos, 2)?;
        let day = if pos < end {
            if dashed {
                if byte(text, pos) != b'-' {
                    return None;
                }
                pos += 1;
            }
            digits(text, &mut pos, 1)?
        } else {
            1
        };
        if !(1..=9999).contains(&year) || !(1..=7).contains(&day) {
            return None;
        }
        let weekday = [
            Weekday::Mon,
            Weekday::Tue,
            Weekday::Wed,
            Weekday::Thu,
            Weekday::Fri,
            Weekday::Sat,
            Weekday::Sun,
        ][day as usize - 1];
        let value = NaiveDate::from_isoywd_opt(year, week, weekday)?;
        Some((value.year(), value.month(), value.day()))
    } else {
        let month = digits(text, &mut pos, 2)?;
        if dashed {
            if byte(text, pos) != b'-' {
                return None;
            }
            pos += 1;
        }
        Some((year, month, digits(text, &mut pos, 2)?))
    }
}
// The reference parser uses an explicit timezone boundary and a trailing NUL.
// Preserve its return-code distinction, including a decimal mark just before TZ.
fn clock(text: &[u8], end: usize) -> Option<([u32; 4], bool)> {
    let mut parts = [0; 4];
    let mut pos = 0;
    let mut separated = true;
    for (i, part) in parts[..3].iter_mut().enumerate() {
        *part = digits(text, &mut pos, 2)?;
        let c = byte(text, pos);
        pos += 1;
        if i == 0 {
            separated = c == b':';
        }
        if pos >= end {
            return Some((parts, c != 0));
        }
        if separated && c == b':' {
            if i == 2 {
                return None;
            }
            continue;
        }
        if c == b'.' || c == b',' {
            if i < 2 {
                return None;
            }
            break;
        }
        if !separated {
            pos -= 1;
        } else {
            return None;
        }
    }
    let width = end.checked_sub(pos)?.min(6);
    if width == 0 {
        return None;
    }
    parts[3] = digits(text, &mut pos, width)? * 10_u32.pow(6 - width as u32);
    while byte(text, pos).is_ascii_digit() {
        pos += 1;
    }
    Some((parts, byte(text, pos) != 0))
}
fn timedelta_repr(micros: i64) -> String {
    let days = micros.div_euclid(86_400_000_000);
    let rest = micros.rem_euclid(86_400_000_000);
    let seconds = rest / 1_000_000;
    let fraction = rest % 1_000_000;
    let mut fields = Vec::new();
    if days != 0 {
        fields.push(format!("days={days}"));
    }
    if seconds != 0 {
        fields.push(format!("seconds={seconds}"));
    }
    if fraction != 0 {
        fields.push(format!("microseconds={fraction}"));
    }
    format!(
        "datetime.timedelta({})",
        if fields.is_empty() {
            "0".into()
        } else {
            fields.join(", ")
        }
    )
}
fn month_days(year: i32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 => {
            if year % 4 == 0 && (year % 100 != 0 || year % 400 == 0) {
                29
            } else {
                28
            }
        }
        _ => 0,
    }
}
pub(crate) fn information_stamp(original: &str) -> Result<DateTime<Utc>, MetadataError> {
    let text = original.replace('Z', "+00:00");
    let bytes = text.as_bytes();
    let invalid = || {
        error(
            "ValueError",
            format!("Invalid isoformat string: {}", repr(&text)),
        )
    };
    let split = separator(bytes).ok_or_else(invalid)?;
    let (mut year, mut month, mut day) = date(bytes, split).ok_or_else(invalid)?;
    let mut time = [0; 4];
    let mut offset = None;
    if bytes.len() > split {
        let separator = text
            .get(split..)
            .and_then(|s| s.chars().next())
            .ok_or_else(invalid)?;
        let body = &bytes[split + separator.len_utf8()..];
        let tz = body.iter().position(|c| matches!(c, b'+' | b'-' | b'Z'));
        let (parts, trailing) = clock(body, tz.unwrap_or(body.len())).ok_or_else(invalid)?;
        time = parts;
        if let Some(pos) = tz {
            let sign = if body[pos] == b'-' { -1 } else { 1 };
            let zone = &body[pos + 1..];
            let (parts, trailing) = clock(zone, zone.len()).ok_or_else(invalid)?;
            if trailing {
                return Err(invalid());
            }
            let seconds =
                i64::from(parts[0]) * 3600 + i64::from(parts[1]) * 60 + i64::from(parts[2]);
            // CPython treats a zero whole-second offset as UTC, even with a fraction.
            let value = if seconds == 0 {
                0
            } else {
                sign * (seconds * 1_000_000 + i64::from(parts[3]))
            };
            if value.abs() >= 86_400_000_000 {
                return Err(error(
                    "ValueError",
                    format!(
                        "offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not {}",
                        timedelta_repr(value)
                    ),
                ));
            }
            offset = Some(value);
        } else if trailing {
            return Err(invalid());
        }
    }
    if time[0] == 24 && month <= 12 && day <= month_days(year, month) {
        if time[1..].iter().any(|v| *v != 0) {
            return Err(error(
                "ValueError",
                "minute, second, and microsecond must be 0 when hour is 24",
            ));
        }
        time[0] = 0;
        day += 1;
        if day > month_days(year, month) {
            day = 1;
            month += 1;
            if month > 12 {
                month = 1;
                year += 1;
            }
        }
    }
    if !(1..=9999).contains(&year) {
        return Err(error(
            "ValueError",
            format!("year must be in 1..9999, not {year}"),
        ));
    }
    if !(1..=12).contains(&month) {
        return Err(error(
            "ValueError",
            format!("month must be in 1..12, not {month}"),
        ));
    }
    let dim = month_days(year, month);
    if !(1..=dim).contains(&day) {
        return Err(error(
            "ValueError",
            format!("day {day} must be in range 1..{dim} for month {month} in year {year}"),
        ));
    }
    for (value, name, maximum) in [
        (time[0], "hour", 23),
        (time[1], "minute", 59),
        (time[2], "second", 59),
    ] {
        if value > maximum {
            return Err(error(
                "ValueError",
                format!("{name} must be in 0..{maximum}, not {value}"),
            ));
        }
    }
    let offset = offset
        .ok_or_else(|| error("ProviderError", "Information timestamps require a timezone"))?;
    let local = NaiveDate::from_ymd_opt(year, month, day)
        .and_then(|date| date.and_hms_micro_opt(time[0], time[1], time[2], time[3]))
        .ok_or_else(invalid)?;
    let utc = local
        .checked_sub_signed(Duration::microseconds(offset))
        .ok_or_else(|| error("OverflowError", "date value out of range"))?;
    if !(1..=9999).contains(&utc.year()) {
        return Err(error("OverflowError", "date value out of range"));
    }
    Ok(utc.and_utc())
}
