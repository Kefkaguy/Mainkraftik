//! Legacy extraction over HTML token events, not browser tree reconstruction.
//! Entity/whitespace behavior matches the frozen Python/BeautifulSoup reference.
use crate::{Result, invalid};
use html5gum::{
    Span, State, Tokenizer,
    emitters::callback::{CallbackEmitter, CallbackEvent},
};
use regex::Regex;
use serde_json::{Value, json};
use std::{collections::BTreeMap, sync::LazyLock};
static ENTITIES: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../assets/legacy-html-entities.json"))
        .expect("frozen entity table")
});
static ATTR_REF: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"&(#[0-9]+[;=]?|#[xX][0-9a-fA-F]+[;=]?|[a-zA-Z][a-zA-Z0-9]*[;=]?)")
        .expect("constant regex")
});
static TEXT_REF: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"&(#(?:[xX][0-9a-fA-F]+|[0-9]+);?|[a-zA-Z][-.a-zA-Z0-9]*;?)")
        .expect("constant regex")
});
fn space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}'..='\u{1f}')
}
fn decode(text: &str, attribute: bool) -> String {
    let regex = if attribute { &*ATTR_REF } else { &*TEXT_REF };
    regex
        .replace_all(text, |capture: &regex::Captures<'_>| {
            let full = &capture[1];
            let equal = attribute && full.ends_with('=');
            let name = full.trim_end_matches([';', '=']);
            if let Some(raw) = name.strip_prefix('#') {
                let (raw, radix) = raw
                    .strip_prefix('x')
                    .or_else(|| raw.strip_prefix('X'))
                    .map_or((raw, 10), |s| (s, 16));
                let n = u32::from_str_radix(raw, radix).unwrap_or(0x110000);
                let table = if attribute {
                    "numeric_attribute"
                } else {
                    "numeric_text"
                };
                if let Some(value) = ENTITIES[table][n.to_string()].as_str() {
                    return format!("{value}{}", if equal { "=" } else { "" });
                }
                if attribute
                    && n <= 0x10ffff
                    && ((0xfdd0..=0xfdef).contains(&n) || n & 0xffff >= 0xfffe)
                {
                    return if equal { "=" } else { "" }.into();
                }
                return format!(
                    "{}{}",
                    char::from_u32(n).unwrap_or('\u{fffd}'),
                    if equal { "=" } else { "" }
                );
            }
            if !attribute {
                return ENTITIES["text"][name]
                    .as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| format!("&{name}"));
            }
            if let Some(value) = ENTITIES["attribute"][full].as_str() {
                return value.to_owned();
            }
            format!("&{full}")
        })
        .into_owned()
}
#[derive(Default)]
struct Node {
    name: String,
    attrs: BTreeMap<String, String>,
    parent: usize,
    text: Option<String>,
    hidden: bool,
    preserve: bool,
    end: usize,
}
pub struct Document {
    nodes: Vec<Node>,
}
impl Document {
    pub fn parse(input: &str) -> Result<Self> {
        let mut nodes = vec![Node::default()];
        let mut stack = vec![0usize];
        let mut pending = Node::default();
        let mut attribute = String::new();
        let callback = |event: CallbackEvent<'_>, span: Span<usize>| -> Option<(String, bool)> {
            match event {
                CallbackEvent::OpenStartTag { name } => {
                    pending = Node {
                        name: String::from_utf8_lossy(name).to_lowercase(),
                        ..Node::default()
                    };
                    attribute.clear();
                }
                CallbackEvent::AttributeName { name } => {
                    attribute = String::from_utf8_lossy(name).to_lowercase();
                    pending.attrs.insert(attribute.clone(), String::new());
                }
                CallbackEvent::AttributeValue { value } => {
                    let raw = input
                        .get(span.start..span.end)
                        .unwrap_or_else(|| std::str::from_utf8(value).unwrap_or(""));
                    pending.attrs.insert(attribute.clone(), decode(raw, true));
                }
                CallbackEvent::CloseStartTag { self_closing } => {
                    pending.parent = *stack.last().expect("root");
                    let parent = &nodes[pending.parent];
                    pending.hidden = parent.hidden
                        || matches!(
                            parent.name.as_str(),
                            "script" | "style" | "template" | "rt" | "rp"
                        );
                    pending.preserve =
                        parent.preserve || matches!(pending.name.as_str(), "pre" | "textarea");
                    let name = pending.name.clone();
                    let index = nodes.len();
                    nodes.push(std::mem::take(&mut pending));
                    if !self_closing
                        && !matches!(
                            name.as_str(),
                            "area"
                                | "base"
                                | "br"
                                | "col"
                                | "embed"
                                | "hr"
                                | "img"
                                | "input"
                                | "keygen"
                                | "link"
                                | "menuitem"
                                | "meta"
                                | "param"
                                | "source"
                                | "track"
                                | "wbr"
                                | "basefont"
                                | "bgsound"
                                | "command"
                                | "frame"
                                | "image"
                                | "isindex"
                                | "nextid"
                                | "spacer"
                        )
                    {
                        stack.push(index);
                    } else {
                        nodes[index].end = nodes.len();
                    }
                    return Some((name, self_closing));
                }
                CallbackEvent::EndTag { name } => {
                    let name = String::from_utf8_lossy(name).to_lowercase();
                    if let Some(i) = stack.iter().rposition(|i| nodes[*i].name == name) {
                        let end = nodes.len();
                        for node in &stack[i..] {
                            nodes[*node].end = end;
                        }
                        stack.truncate(i.max(1));
                    }
                }
                CallbackEvent::String { value } => {
                    let raw = input
                        .get(span.start..span.end)
                        .unwrap_or_else(|| std::str::from_utf8(value).unwrap_or(""));
                    let mut text = decode(raw, false);
                    if text
                        .chars()
                        .all(|c| matches!(c, ' ' | '\t' | '\n' | '\r' | '\u{c}'))
                        && !nodes[*stack.last().expect("root")].preserve
                    {
                        text = if text.contains('\n') { "\n" } else { " " }.into();
                    }
                    nodes.push(Node {
                        parent: *stack.last().expect("root"),
                        text: Some(text),
                        hidden: {
                            let parent = &nodes[*stack.last().expect("root")];
                            parent.hidden
                                || matches!(
                                    parent.name.as_str(),
                                    "script" | "style" | "template" | "rt" | "rp"
                                )
                        },
                        ..Node::default()
                    });
                }
                CallbackEvent::Comment { .. } => {
                    if let Some(raw) = input
                        .get(span.start..span.end)
                        .and_then(|s| s.strip_prefix("<![CDATA["))
                        .and_then(|s| s.strip_suffix("]]>"))
                    {
                        nodes.push(Node {
                            parent: *stack.last().expect("root"),
                            text: Some(raw.into()),
                            ..Node::default()
                        });
                    }
                }
                _ => {}
            }
            None
        };
        let emitter = CallbackEmitter::new(callback);
        let mut tokenizer = Tokenizer::new_with_emitter(input, emitter);
        while let Some(token) = tokenizer.next() {
            let (name, closed) = token.map_err(|_| invalid("HTML token input unavailable"))?;
            if !closed {
                match name.as_str() {
                    "script" | "style" | "xmp" | "iframe" | "noembed" | "noframes" => {
                        tokenizer.set_state(State::RawText)
                    }
                    "textarea" | "title" => tokenizer.set_state(State::RcData),
                    "plaintext" => tokenizer.set_state(State::PlainText),
                    _ => {}
                }
            }
        }
        drop(tokenizer);
        let end = nodes.len();
        for node in stack {
            nodes[node].end = end;
        }
        Ok(Self { nodes })
    }
    pub fn elements(&self, name: &str) -> impl Iterator<Item = usize> + '_ {
        let name = name.to_owned();
        self.nodes
            .iter()
            .enumerate()
            .filter_map(move |(i, n)| (n.name == name).then_some(i))
    }
    pub fn attr(&self, index: usize, key: &str) -> Option<&str> {
        self.nodes[index].attrs.get(key).map(String::as_str)
    }
    pub fn text(&self, index: usize, strip: bool) -> String {
        let mut parts = Vec::new();
        for node in &self.nodes[index + 1..self.nodes[index].end] {
            if node.hidden {
                continue;
            }
            let Some(text) = &node.text else {
                continue;
            };
            {
                if strip {
                    let text = text.trim_matches(space);
                    if !text.is_empty() {
                        parts.push(text);
                    }
                } else {
                    parts.push(text);
                }
            }
        }
        parts.join(if strip { " " } else { "" })
    }
    pub fn projection(&self) -> Value {
        json!(self.nodes.iter().enumerate().filter(|(_,n)|matches!(n.name.as_str(),"a"|"meta"|"h1"|"p")).map(|(i,n)|json!({"name":n.name,"attrs":n.attrs.iter().filter(|(k,_)|matches!(k.as_str(),"href"|"name"|"property"|"content")).collect::<BTreeMap<_,_>>(),"text":self.text(i,true),"raw_text":self.text(i,false)})).collect::<Vec<_>>())
    }
}
