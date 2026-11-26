//! NumPy SeedSequence(u32) + PCG64 + Ziggurat f64 normal, not a substitute RNG.
//! Derived from NumPy/PCG sources; notices in third-party/ and source provenance report.
use crate::ziggurat::{FI_DOUBLE, KI_DOUBLE, WI_DOUBLE};
use crate::{Result, invalid};

fn hashmix(mut value: u32, constant: &mut u32) -> u32 {
    value ^= *constant;
    *constant = constant.wrapping_mul(0x931e8875);
    value = value.wrapping_mul(*constant);
    value ^ (value >> 16)
}
fn mix(x: u32, y: u32) -> u32 {
    let value = 0xca01f9ddu32
        .wrapping_mul(x)
        .wrapping_sub(0x4973f715u32.wrapping_mul(y));
    value ^ (value >> 16)
}
fn seed_sequence(seed: u32) -> [u64; 4] {
    let mut pool = [0u32; 4];
    let mut constant = 0x43b0d7e5u32;
    for (i, value) in pool.iter_mut().enumerate() {
        *value = hashmix(if i == 0 { seed } else { 0 }, &mut constant);
    }
    for src in 0..4 {
        for dst in 0..4 {
            if src != dst {
                pool[dst] = mix(pool[dst], hashmix(pool[src], &mut constant));
            }
        }
    }
    let mut output = [0u32; 8];
    constant = 0x8b51f9dd;
    for (i, value) in output.iter_mut().enumerate() {
        let mut next = pool[i % 4] ^ constant;
        constant = constant.wrapping_mul(0x58f38ded);
        next = next.wrapping_mul(constant);
        *value = next ^ (next >> 16);
    }
    std::array::from_fn(|i| output[2 * i] as u64 | ((output[2 * i + 1] as u64) << 32))
}

#[derive(Clone)]
pub struct Pcg64 {
    state: u128,
    increment: u128,
    cached_u32: Option<u32>,
}
impl Pcg64 {
    pub fn new(seed: u32) -> Self {
        let words = seed_sequence(seed);
        let initial = ((words[0] as u128) << 64) | words[1] as u128;
        let sequence = ((words[2] as u128) << 64) | words[3] as u128;
        let mut rng = Self {
            state: 0,
            increment: (sequence << 1) | 1,
            cached_u32: None,
        };
        rng.step();
        rng.state = rng.state.wrapping_add(initial);
        rng.step();
        rng
    }
    fn step(&mut self) {
        self.state = self
            .state
            .wrapping_mul((2549297995355413924u128 << 64) | 4865540595714422341u128)
            .wrapping_add(self.increment);
    }
    pub fn next_u64(&mut self) -> u64 {
        self.step();
        ((self.state as u64) ^ ((self.state >> 64) as u64)).rotate_right((self.state >> 122) as u32)
    }
    fn next_u32(&mut self) -> u32 {
        if let Some(value) = self.cached_u32.take() {
            return value;
        }
        let value = self.next_u64();
        self.cached_u32 = Some((value >> 32) as u32);
        value as u32
    }
    /// NumPy random_bounded_uint64_fill with use_masked=false. Its 32-bit
    /// fast path preserves PCG64's cached high half across other draw types.
    pub fn bounded_u64(&mut self, exclusive: u64) -> Result<u64> {
        let range = exclusive
            .checked_sub(1)
            .ok_or_else(|| invalid("Positive random bound required"))?;
        if range == 0 {
            return Ok(0);
        }
        if range <= u64::from(u32::MAX) {
            if range == u64::from(u32::MAX) {
                return Ok(u64::from(self.next_u32()));
            }
            let threshold = (u64::from(u32::MAX) - range) % exclusive;
            loop {
                let product = u64::from(self.next_u32()) * exclusive;
                if u64::from(product as u32) >= threshold {
                    return Ok(product >> 32);
                }
            }
        }
        let threshold = (u64::MAX - range) % exclusive;
        loop {
            let product = u128::from(self.next_u64()) * u128::from(exclusive);
            if product as u64 >= threshold {
                return Ok((product >> 64) as u64);
            }
        }
    }
    fn uniform(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / 9007199254740992.0)
    }
    pub fn normal(&mut self) -> f64 {
        loop {
            let bits = self.next_u64();
            let index = (bits & 255) as usize;
            let shifted = bits >> 8;
            let negative = shifted & 1 != 0;
            let magnitude = (shifted >> 1) & 0x000fffffffffffff;
            let mut x = magnitude as f64 * WI_DOUBLE[index];
            if negative {
                x = -x;
            }
            if magnitude < KI_DOUBLE[index] {
                return x;
            }
            if index == 0 {
                loop {
                    let xx = -0.273_661_237_329_758_3 * (-self.uniform()).ln_1p();
                    let yy = -(-self.uniform()).ln_1p();
                    if yy + yy > xx * xx {
                        let tail = 3.654_152_885_361_009 + xx;
                        return if (magnitude >> 8) & 1 != 0 {
                            -tail
                        } else {
                            tail
                        };
                    }
                }
            }
            if (FI_DOUBLE[index - 1] - FI_DOUBLE[index]) * self.uniform() + FI_DOUBLE[index]
                < (-0.5 * x * x).exp()
            {
                return x;
            }
        }
    }
}
