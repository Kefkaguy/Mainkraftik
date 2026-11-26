//! Compatibility port of Python 3.14 RobotFileParser's '*' query semantics.
//! PSF/Bastian Kleineidam notices retained in third-party/PYTHON-LICENSE.txt.
use percent_encoding::percent_decode_str;
use regex::Regex;
use std::sync::LazyLock;
static LINES: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\r\n|[\n\r\x0b\x0c\x1c-\x1e\x{85}\x{2028}\x{2029}]").expect("constant regex")
});
#[derive(Default)]
struct Entry {
    agents: Vec<String>,
    rules: Vec<(String, bool)>,
}
#[derive(Default)]
pub struct Robots {
    entries: Vec<Entry>,
    default: Option<Entry>,
}
fn space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}
fn encode(s: &str) -> String {
    let bytes = percent_decode_str(s).collect::<Vec<_>>();
    let mut out = String::new();
    for b in bytes {
        if b.is_ascii_alphanumeric() || b"/_.-~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}
fn normalize(s: &str) -> String {
    let (path, query) = s.split_once('?').map_or((s, None), |(a, b)| (a, Some(b)));
    let mut out = encode(path);
    if let Some(query) = query {
        out.push('?');
        let mut start = 0;
        for (i, c) in query.char_indices() {
            if c == '=' || c == '&' {
                out.push_str(&encode(&query[start..i]));
                out.push(c);
                start = i + 1;
            }
        }
        out.push_str(&encode(&query[start..]));
    }
    out
}
impl Robots {
    fn add(&mut self, entry: Entry) {
        if entry.agents.iter().any(|s| s == "*") {
            if self.default.is_none() {
                self.default = Some(entry);
            }
        } else {
            self.entries.push(entry);
        }
    }
    pub fn parse(text: &str) -> Self {
        let mut result = Self::default();
        let mut state = 0;
        let mut entry = Entry::default();
        for raw in LINES.split(text) {
            if raw.is_empty() {
                if state == 2 {
                    result.add(std::mem::take(&mut entry));
                }
                if state != 0 {
                    entry = Entry::default();
                    state = 0;
                }
            }
            let line = raw.split('#').next().unwrap_or("").trim_matches(space);
            let Some((key, value)) = line.split_once(':') else {
                continue;
            };
            let value = value.trim_matches(space);
            match key.trim_matches(space).to_lowercase().as_str() {
                "user-agent" => {
                    if state == 2 {
                        result.add(std::mem::take(&mut entry));
                    }
                    entry.agents.push(value.into());
                    state = 1;
                }
                "allow" | "disallow" if state != 0 => {
                    let allowed =
                        key.trim_matches(space).eq_ignore_ascii_case("allow") || value.is_empty();
                    entry.rules.push((normalize(value), allowed));
                    state = 2;
                }
                "crawl-delay" | "request-rate" if state != 0 => state = 2,
                _ => {}
            }
        }
        if state == 2 {
            result.add(entry);
        }
        result
    }
    pub fn can_fetch(&self, url: &str) -> bool {
        let tail = url.split_once("://").map_or(url, |(_, s)| s);
        let at = tail.find(['/', '?', '#']).unwrap_or(tail.len());
        let path = normalize(&tail[at..]);
        let path = if path.is_empty() { "/" } else { &path };
        let entry = self
            .entries
            .iter()
            .find(|e| e.agents.iter().any(|s| s.is_empty() || s == "*"))
            .or(self.default.as_ref());
        entry
            .and_then(|e| {
                e.rules
                    .iter()
                    .find(|(prefix, _)| prefix == "*" || path.starts_with(prefix))
            })
            .is_none_or(|(_, allowed)| *allowed)
    }
}
