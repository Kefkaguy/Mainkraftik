//! Scalar rendering of NumPy 2.5.4's f32 FMA ndarray exponential kernel.
//! Pinned source receipt: assets/numpy-exp-provenance.json; BSD-3-Clause license retained.
//! This matches the model's reference computation; it does not retrain the model.
pub fn exp(value: f32) -> f32 {
    if value.is_nan() {
        return f32::NAN;
    }
    if value >= f32::from_bits(0x42b17218) {
        return f32::INFINITY;
    }
    if value <= f32::from_bits(0xc2cff1b5) {
        return 0.;
    }
    let magic = f32::from_bits(0x4b400000);
    let q = (value * f32::from_bits(0x3fb8aa3b) + magic) - magic;
    let mut reduced = q.mul_add(f32::from_bits(0xbf317200), value);
    reduced = q.mul_add(f32::from_bits(0xb5bfbe8e), reduced);
    reduced = q.mul_add(0., reduced);
    let mut numerator = f32::from_bits(0x3a053dd8);
    for coefficient in [0x3bdd7159, 0x3d517d8c, 0x3e7d4c58, 0x3f39cbd5, 0x3f800000] {
        numerator = numerator.mul_add(reduced, f32::from_bits(coefficient));
    }
    let denominator = f32::from_bits(0x3cb0e832)
        .mul_add(reduced, f32::from_bits(0xbe8c6857))
        .mul_add(reduced, 1.);
    let ratio = numerator / denominator;
    let exponent = q as i32;
    if exponent <= -125 {
        let normal = f32::from_bits(
            ratio
                .to_bits()
                .wrapping_add((-125_i32 as u32).wrapping_shl(23)),
        );
        normal / (1_u32 << (-exponent - 125)) as f32
    } else {
        f32::from_bits(
            ratio
                .to_bits()
                .wrapping_add((exponent as u32).wrapping_shl(23)),
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pinned_numpy_fma_bits_match_for_dense_random_and_boundary_inputs() {
        for text in [
            include_str!("../tests/fixtures/numpy-exp-parity.json"),
            include_str!("../tests/fixtures/numpy-exp-parity-linux.json"),
        ] {
            let reference: serde_json::Value = serde_json::from_str(text).unwrap();
            let inputs = reference["input_bits"].as_array().unwrap();
            let outputs = reference["expected_bits"].as_array().unwrap();
            assert_eq!(inputs.len(), outputs.len());
            for (input, output) in inputs.iter().zip(outputs) {
                let bits = input.as_u64().unwrap() as u32;
                assert_eq!(
                    exp(f32::from_bits(bits)).to_bits(),
                    output.as_u64().unwrap() as u32,
                    "input {bits:08x}, {}",
                    f32::from_bits(bits)
                );
            }
        }
    }
}
