//! Exact fixed chart geometry and Pillow-compatible PNG filtering/encoding.
//! Filter order/settings adapted from Pillow 12.3.0 ZipEncode.c; see retained license.
use crate::{Result, calendar::Calendar, canonical, financials, invalid, monitoring};
use chrono::{DateTime, FixedOffset, SecondsFormat};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
/// Explicit wire compatibility, independent of the host OS and optional libraries.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Compression {
    #[serde(rename = "pillow-zlib-ng-2.3.3")]
    ZlibNg233,
    #[serde(rename = "pillow-zlib-1.3")]
    Classic13,
}
impl std::str::FromStr for Compression {
    type Err = crate::Error;
    fn from_str(value: &str) -> Result<Self> {
        match value {
            "pillow-zlib-ng-2.3.3" => Ok(Self::ZlibNg233),
            "pillow-zlib-1.3" => Ok(Self::Classic13),
            _ => Err(invalid("Unverified chart compression profile")),
        }
    }
}
impl Compression {
    pub fn alternative(self) -> Self {
        match self {
            Self::ZlibNg233 => Self::Classic13,
            Self::Classic13 => Self::ZlibNg233,
        }
    }
}
pub const SIZE: usize = 640;
pub struct Chart {
    pub pixels: Vec<u8>,
    pub png: Vec<u8>,
    pub manifest: Value,
    pub image_hash: String,
    pub input_hash: String,
    pub filtered_hash: String,
}
pub fn pixel(pixels: &mut [u8], x: i32, y: i32, color: [u8; 3]) {
    if x >= 0 && x < SIZE as i32 && y >= 0 && y < SIZE as i32 {
        let index = (y as usize * SIZE + x as usize) * 3;
        pixels[index..index + 3].copy_from_slice(&color);
    }
}
fn rectangle(pixels: &mut [u8], x0: i32, y0: i32, x1: i32, y1: i32, color: [u8; 3]) {
    for y in y0.max(0)..=y1.min(SIZE as i32 - 1) {
        for x in x0.max(0)..=x1.min(SIZE as i32 - 1) {
            pixel(pixels, x, y, color);
        }
    }
}
fn norm_time(text: &str) -> Result<(DateTime<FixedOffset>, String)> {
    let time = DateTime::parse_from_rfc3339(text).map_err(|_| invalid("Invalid chart cutoff"))?;
    let text = time.to_rfc3339_opts(
        if time.timestamp_subsec_micros() == 0 {
            SecondsFormat::Secs
        } else {
            SecondsFormat::Micros
        },
        false,
    );
    Ok((time, text))
}
pub fn render(calendar: &Calendar, bars: &[Value], cutoff: &str) -> Result<Chart> {
    render_with(calendar, bars, cutoff, Compression::ZlibNg233)
}
pub fn render_with(
    calendar: &Calendar,
    bars: &[Value],
    cutoff: &str,
    compression: Compression,
) -> Result<Chart> {
    let (time, text) = norm_time(cutoff)?;
    let mut rows = Vec::new();
    for row in bars {
        if financials::timestamp(&row["available_at"])? <= time
            && financials::timestamp(&row["acquired_at"])? <= time
        {
            rows.push(row.clone());
        }
    }
    let rows = &rows[rows.len().saturating_sub(90)..];
    if rows.len() != 90 {
        return Err(invalid(
            "90 cutoff-safe bars are required for the fixed renderer",
        ));
    }
    let (valid, reason) = monitoring::continuity(calendar, rows)?;
    if !valid {
        return Err(invalid(reason.unwrap_or("Invalid chart continuity")));
    }
    let mut high = f64::NEG_INFINITY;
    let mut low = f64::INFINITY;
    for row in rows {
        high = high.max(financials::number(&row["high"])?);
        low = low.min(financials::number(&row["low"])?);
    }
    if high <= low {
        return Err(invalid("A non-degenerate price range is required"));
    }
    let mut pixels = [245, 248, 250].repeat(SIZE * SIZE);
    let y = |value: f64| 612 - ((value - low) / (high - low) * 584.0) as i32;
    for (index, row) in rows.iter().enumerate() {
        let x = 24.0 + index as f64 * 6.6;
        let open = financials::number(&row["open"])?;
        let close = financials::number(&row["close"])?;
        let color = if close >= open {
            [22, 134, 117]
        } else {
            [182, 90, 98]
        };
        let a = y(financials::number(&row["high"])?);
        let b = y(financials::number(&row["low"])?);
        for yy in a.min(b)..=a.max(b) {
            pixel(&mut pixels, x as i32, yy, color);
        }
        let (a, b) = (y(open), y(close));
        let top = a.min(b);
        let bottom = a.max(b).max(top + 1);
        rectangle(
            &mut pixels,
            (x - 2.0) as i32,
            top,
            (x + 2.0) as i32,
            bottom,
            color,
        );
    }
    let filtered = filter(&pixels)?;
    let filtered_hash = hex::encode(Sha256::digest(&filtered));
    let png = encode_filtered(&filtered, compression)?;
    let image_hash = hex::encode(Sha256::digest(&png));
    let manifest = json!({"renderer_version":"candles-90-square-v1","cutoff":text,"bar_ids":rows.iter().map(|r|r["id"].clone()).collect::<Vec<_>>(),"bar_hashes":rows.iter().map(|r|r["content_hash"].clone()).collect::<Vec<_>>(),"width":SIZE,"height":SIZE,"last_session":rows[rows.len()-1]["session_date"]});
    let input_hash = canonical::digest(&manifest)?;
    Ok(Chart {
        pixels,
        png,
        manifest,
        image_hash,
        input_hash,
        filtered_hash,
    })
}
pub fn filter(pixels: &[u8]) -> Result<Vec<u8>> {
    let stride = SIZE * 3;
    if pixels.len() != SIZE * stride {
        return Err(invalid("Invalid fixed RGB buffer"));
    }
    let mut output = Vec::with_capacity(SIZE * (stride + 1));
    let black = vec![0u8; stride];
    for row in 0..SIZE {
        let current = &pixels[row * stride..(row + 1) * stride];
        let prior = if row == 0 {
            &black[..]
        } else {
            &pixels[(row - 1) * stride..row * stride]
        };
        let score = |bytes: &[u8]| {
            bytes
                .iter()
                .map(|v| u32::from((*v).min(0u8.wrapping_sub(*v))))
                .sum::<u32>()
        };
        let mut best = current.to_vec();
        let mut best_score = score(&best);
        let mut tag = 0u8;
        for candidate in [2u8, 1, 4] {
            if best_score == 0 {
                break;
            }
            let values: Vec<u8> = current
                .iter()
                .enumerate()
                .map(|(i, value)| {
                    let prediction = match candidate {
                        2 => prior[i],
                        1 => {
                            if i < 3 {
                                0
                            } else {
                                current[i - 3]
                            }
                        }
                        _ => {
                            if i < 3 {
                                prior[i]
                            } else {
                                let a = i32::from(current[i - 3]);
                                let b = i32::from(prior[i]);
                                let c = i32::from(prior[i - 3]);
                                let pa = (b - c).abs();
                                let pb = (a - c).abs();
                                let pc = (a + b - 2 * c).abs();
                                if pa <= pb && pa <= pc {
                                    a as u8
                                } else if pb <= pc {
                                    b as u8
                                } else {
                                    c as u8
                                }
                            }
                        }
                    };
                    value.wrapping_sub(prediction)
                })
                .collect();
            let scored = score(&values);
            if scored < best_score {
                best = values;
                best_score = scored;
                tag = candidate;
            }
        }
        output.push(tag);
        output.extend_from_slice(&best);
    }
    Ok(output)
}
fn chunk(out: &mut Vec<u8>, kind: &[u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(kind);
    out.extend_from_slice(data);
    let mut crc = crc32fast::Hasher::new();
    crc.update(kind);
    crc.update(data);
    out.extend_from_slice(&crc.finalize().to_be_bytes());
}
fn encode_filtered(filtered: &[u8], compression: Compression) -> Result<Vec<u8>> {
    let compressed = match compression {
        Compression::ZlibNg233 => mainkraft_png_codec::filtered_rows(filtered, SIZE * 3 + 1, SIZE),
        Compression::Classic13 => {
            mainkraft_png_codec::classic_filtered_rows(filtered, SIZE * 3 + 1, SIZE)
        }
    }
    .map_err(invalid)?;
    let mut out = b"\x89PNG\r\n\x1a\n".to_vec();
    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&(SIZE as u32).to_be_bytes());
    ihdr.extend_from_slice(&(SIZE as u32).to_be_bytes());
    ihdr.extend_from_slice(&[8, 2, 0, 0, 0]);
    chunk(&mut out, b"IHDR", &ihdr);
    for data in compressed.chunks(65536) {
        chunk(&mut out, b"IDAT", data);
    }
    chunk(&mut out, b"IEND", &[]);
    Ok(out)
}
pub fn encode_pixels(pixels: &[u8]) -> Result<Vec<u8>> {
    encode_pixels_with(pixels, Compression::ZlibNg233)
}
pub fn encode_pixels_with(pixels: &[u8], compression: Compression) -> Result<Vec<u8>> {
    encode_filtered(&filter(pixels)?, compression)
}
fn detector_number(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(b) => Some(if *b { 1.0 } else { 0.0 }),
        _ => value.as_f64().filter(|v| v.is_finite()),
    }
}
pub fn validate_boxes(boxes: &Value) -> Result<Vec<([f64; 4], f64, String)>> {
    let rows = boxes
        .as_array()
        .filter(|r| r.len() <= 1000)
        .ok_or_else(|| invalid("Invalid detector result"))?;
    let mut output = Vec::new();
    for row in rows {
        let label = row["label"]
            .as_str()
            .filter(|s| s.chars().count() <= 100)
            .ok_or_else(|| invalid("Invalid detector label"))?;
        let coords = row["xyxy"]
            .as_array()
            .filter(|c| c.len() == 4)
            .ok_or_else(|| invalid("Detector coordinates must be finite"))?;
        let mut xy = [0.0; 4];
        for (i, value) in coords.iter().enumerate() {
            xy[i] = detector_number(value)
                .ok_or_else(|| invalid("Detector coordinates must be finite"))?;
        }
        let confidence = detector_number(&row["confidence"])
            .ok_or_else(|| invalid("Detector coordinates must be finite"))?;
        if !(0.0 <= xy[0]
            && xy[0] <= xy[2]
            && xy[2] <= 640.0
            && 0.0 <= xy[1]
            && xy[1] <= xy[3]
            && xy[3] <= 640.0
            && (0.0..=1.0).contains(&confidence))
        {
            return Err(invalid("Detector result outside image bounds"));
        }
        output.push((xy, confidence, label.to_owned()));
    }
    Ok(output)
}
fn hline(pixels: &mut [u8], x0: i32, y: i32, x1: i32, color: [u8; 3]) {
    for x in x0.max(0)..=x1.min(SIZE as i32 - 1) {
        pixel(pixels, x, y, color);
    }
}
fn vertical_exclusive(pixels: &mut [u8], x: i32, y0: i32, y1: i32, color: [u8; 3]) {
    let sign = if y1 < y0 { -1 } else { 1 };
    for n in 0..(y1 - y0).abs() {
        pixel(pixels, x, y0 + sign * n, color);
    }
}
pub fn annotate(pixels: &mut [u8], boxes: &Value) -> Result<Vec<u8>> {
    annotate_with(pixels, boxes, Compression::ZlibNg233)
}
pub fn annotate_with(
    pixels: &mut [u8],
    boxes: &Value,
    compression: Compression,
) -> Result<Vec<u8>> {
    if pixels.len() != SIZE * SIZE * 3 {
        return Err(invalid("Invalid fixed RGB buffer"));
    }
    for (xy, confidence, label) in validate_boxes(boxes)? {
        let [x0, y0, x1, y1] = xy.map(|v| v as i32);
        let color = [50, 111, 154];
        for i in 0..2 {
            hline(pixels, x0, y0 + i, x1, color);
            hline(pixels, x0, y1 - i, x1, color);
            vertical_exclusive(pixels, x1 - i, y0 + 2, y1 - 1, color);
            vertical_exclusive(pixels, x0 + i, y0 + 2, y1 - 1, color);
        }
        crate::chart_font::draw(
            pixels,
            &format!("{label} {confidence:.2}"),
            xy[0] + 3.0,
            (xy[1] - 13.0).max(0.0),
            [32, 56, 77],
        )?;
    }
    encode_pixels_with(pixels, compression)
}
