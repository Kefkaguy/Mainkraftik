//! IR session cookies: Python's stable domain/path/name order, longest path first.
//! No cookie persistence or cross-workflow sharing.
use reqwest::{cookie::CookieStore, header::HeaderValue};
use std::{cmp::Reverse, sync::RwLock};

#[derive(Default)]
pub(crate) struct Jar(RwLock<cookie_store::CookieStore>);

impl CookieStore for Jar {
    fn set_cookies(&self, headers: &mut dyn Iterator<Item = &HeaderValue>, url: &url::Url) {
        let mut store = self.0.write().unwrap_or_else(|e| e.into_inner());
        for header in headers {
            if let Ok(text) = std::str::from_utf8(header.as_bytes()) {
                // Invalid cookies are ignored, as in httpx's CookieJar.
                let _ = store.parse(text, url);
            }
        }
        // Python removes expired entries. Reissuing one later must insert at the end,
        // rather than reuse an expired entry's original position.
        let expired: Vec<_> = store
            .iter_any()
            .filter(|c| c.is_expired())
            .map(|c| {
                (
                    String::from(&c.domain),
                    String::from(&c.path),
                    c.name().to_owned(),
                )
            })
            .collect();
        for (domain, path, name) in expired {
            store.remove(&domain, &path, &name);
        }
    }

    fn cookies(&self, url: &url::Url) -> Option<HeaderValue> {
        let store = self.0.read().unwrap_or_else(|e| e.into_inner());
        let mut cookies = store.matches(url);
        // preserve_order keeps nested-map traversal deterministic. A stable sort
        // retains that order for equal-length paths, matching Python CookieJar.
        cookies.sort_by_key(|c| Reverse(c.path.as_ref().len()));
        let value = cookies
            .iter()
            .map(|c| format!("{}={}", c.name(), c.value()))
            .collect::<Vec<_>>()
            .join("; ");
        if value.is_empty() {
            None
        } else {
            HeaderValue::from_str(&value).ok()
        }
    }
}
