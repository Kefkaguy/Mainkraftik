//! Frozen rasterization of the original Pillow Aileron default at 10 px.
//! No runtime font substitution or platform font lookup; see third-party notice.
use crate::{Result, charts::SIZE, invalid};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, sync::OnceLock};
#[derive(Deserialize)]
struct RawGlyph {
    size: [usize; 2],
    offset: [i32; 2],
    advance64: i64,
    mask: String,
}
#[derive(Deserialize)]
struct Atlas {
    line_height: i32,
    fallback: u32,
    glyphs: BTreeMap<u32, RawGlyph>,
    kerning64: BTreeMap<String, i64>,
}
struct Glyph {
    size: [usize; 2],
    offset: [i32; 2],
    advance64: i64,
    mask: Vec<u8>,
}
struct Font {
    line_height: i32,
    fallback: u32,
    glyphs: BTreeMap<u32, Glyph>,
    kerning: BTreeMap<(u32, u32), i64>,
}
static FONT: OnceLock<Option<Font>> = OnceLock::new();
fn font() -> Result<&'static Font> {
    FONT.get_or_init(|| {
        let bytes = include_bytes!("../assets/pillow-default-font-10.json");
        if hex::encode(Sha256::digest(bytes))
            != "e7db724a29c00f98ca5f91ae4e65b4876babc2f55636e5540a6501770564f743"
        {
            return None;
        }
        let atlas: Atlas = serde_json::from_slice(bytes).ok()?;
        let mut glyphs = BTreeMap::new();
        for (cp, glyph) in atlas.glyphs {
            let mask = STANDARD.decode(glyph.mask).ok()?;
            if glyph.size[0] > 32
                || glyph.size[1] > 32
                || glyph.size[0] * glyph.size[1] != mask.len()
            {
                return None;
            }
            glyphs.insert(
                cp,
                Glyph {
                    size: glyph.size,
                    offset: glyph.offset,
                    advance64: glyph.advance64,
                    mask,
                },
            );
        }
        if !glyphs.contains_key(&atlas.fallback) {
            return None;
        }
        let mut kerning = BTreeMap::new();
        for (pair, value) in atlas.kerning64 {
            let (a, b) = pair.split_once(',')?;
            kerning.insert((a.parse().ok()?, b.parse().ok()?), value);
        }
        Some(Font {
            line_height: atlas.line_height,
            fallback: atlas.fallback,
            glyphs,
            kerning,
        })
    })
    .as_ref()
    .ok_or_else(|| invalid("Frozen chart font integrity failure"))
}
fn divide255(value: u32) -> u8 {
    let value = value + 128;
    (((value >> 8) + value) >> 8) as u8
}
pub fn draw(pixels: &mut [u8], text: &str, x: f64, y: f64, color: [u8; 3]) -> Result<()> {
    if pixels.len() != SIZE * SIZE * 3 || !x.is_finite() || !y.is_finite() {
        return Err(invalid("Invalid fixed text canvas"));
    }
    let font = font()?;
    let x_integer = x as i32;
    // Pillow crosses a C float boundary before placing text at 26.6 precision.
    let x_fraction = (f64::from(x.fract() as f32) * 64.0).round() as i64;
    for (line, text) in text.split('\n').enumerate() {
        let y = y + line as f64 * f64::from(font.line_height);
        let y_integer = y as i32;
        let y_shift = -(((-f64::from(y.fract() as f32) * 64.0).round() as i64 + 32) >> 6) as i32;
        let mut placements = Vec::new();
        let mut pen = 0i64;
        let mut previous = None;
        for ch in text.chars() {
            let cp = if font.glyphs.contains_key(&(ch as u32)) {
                ch as u32
            } else {
                font.fallback
            };
            if let Some(previous) = previous {
                pen += font.kerning.get(&(previous, cp)).copied().unwrap_or(0);
            }
            let glyph = &font.glyphs[&cp];
            let left = x_integer + ((pen + x_fraction + 32) >> 6) as i32 + glyph.offset[0];
            let top = y_integer + y_shift + glyph.offset[1];
            if !glyph.mask.is_empty() {
                placements.push((glyph, left, top));
            }
            pen += glyph.advance64;
            previous = Some(cp);
        }
        let left = placements
            .iter()
            .map(|(_, x, _)| *x)
            .min()
            .unwrap_or(0)
            .max(0);
        let top = placements
            .iter()
            .map(|(_, _, y)| *y)
            .min()
            .unwrap_or(0)
            .max(0);
        let right = placements
            .iter()
            .map(|(g, x, _)| *x + g.size[0] as i32)
            .max()
            .unwrap_or(0)
            .min(SIZE as i32);
        let bottom = placements
            .iter()
            .map(|(g, _, y)| *y + g.size[1] as i32)
            .max()
            .unwrap_or(0)
            .min(SIZE as i32);
        if right <= left || bottom <= top {
            continue;
        }
        let width = (right - left) as usize;
        let mut mask = vec![0u8; width * (bottom - top) as usize];
        for (glyph, x, y) in placements {
            for yy in 0..glyph.size[1] {
                for xx in 0..glyph.size[0] {
                    let (px, py) = (x + xx as i32, y + yy as i32);
                    if px < left || px >= right || py < top || py >= bottom {
                        continue;
                    }
                    let incoming = glyph.mask[yy * glyph.size[0] + xx];
                    let index = (py - top) as usize * width + (px - left) as usize;
                    mask[index] = incoming.saturating_add(divide255(
                        u32::from(mask[index]) * u32::from(255 - incoming),
                    ));
                }
            }
        }
        for yy in top..bottom {
            for xx in left..right {
                let alpha = u32::from(mask[(yy - top) as usize * width + (xx - left) as usize]);
                if alpha == 0 {
                    continue;
                }
                let index = (yy as usize * SIZE + xx as usize) * 3;
                for channel in 0..3 {
                    pixels[index + channel] = divide255(
                        u32::from(pixels[index + channel]) * (255 - alpha)
                            + u32::from(color[channel]) * alpha,
                    );
                }
            }
        }
    }
    Ok(())
}
