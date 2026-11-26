//! Narrow safe interface to the same zlib-ng 2.3.3 PNG settings as Pillow.
//! The application crate continues to forbid unsafe code. FFI is isolated here
//! because the general-purpose flate2 interface does not expose memLevel/strategy.
use libz_ng_sys as z;
use std::{ffi::c_void, ptr};

unsafe extern "C" {
    fn mainkraft_zlib13_rows(
        input: *const u8,
        row_length: usize,
        height: usize,
        output: *mut u8,
        capacity: usize,
        written: *mut usize,
    ) -> i32;
}
/// The pinned classic encoder used by the original Linux Pillow reference.
/// Both implementations accept only an initialized, bounded fixed-image buffer.
pub fn classic_filtered_rows(
    input: &[u8],
    row_length: usize,
    height: usize,
) -> Result<Vec<u8>, &'static str> {
    if row_length == 0
        || row_length > 1921
        || height == 0
        || height > 640
        || row_length.checked_mul(height) != Some(input.len())
    {
        return Err("invalid bounded PNG rows");
    }
    let mut output = vec![0u8; 2 * 1024 * 1024];
    let mut written = 0usize;
    // SAFETY: both slices are fully initialized, disjoint and remain alive for
    // the synchronous call. The validated product bounds every C input read;
    // C checks deflateBound before writing and retains no pointer after return.
    let status = unsafe {
        mainkraft_zlib13_rows(
            input.as_ptr(),
            row_length,
            height,
            output.as_mut_ptr(),
            output.len(),
            &mut written,
        )
    };
    if status != 0 || written > output.len() {
        return Err("classic PNG compression failed");
    }
    output.truncate(written);
    Ok(output)
}

unsafe extern "C" fn allocate(_: *mut c_void, n: u32, size: u32) -> *mut c_void {
    if (n as usize).checked_mul(size as usize).is_none() {
        return ptr::null_mut();
    }
    // SAFETY: calloc accepts arbitrary sizes and returns null on allocation failure.
    unsafe { libc::calloc(n as usize, size as usize) }
}
unsafe extern "C" fn release(_: *mut c_void, address: *mut c_void) {
    // SAFETY: zlib only passes pointers returned by the paired allocate callback.
    unsafe { libc::free(address) }
}
struct Stream(Box<z::z_stream>);
impl Drop for Stream {
    fn drop(&mut self) {
        self.0.next_in = ptr::null_mut();
        self.0.avail_in = 0;
        self.0.next_out = ptr::null_mut();
        self.0.avail_out = 0;
        // SAFETY: Stream exists only after successful initialization. It is
        // uniquely owned, its address is stable, and deflateEnd runs exactly once.
        unsafe { z::deflateEnd(&mut *self.0) };
    }
}
/// Compress fully initialized PNG rows, each including its one-byte filter tag.
/// Dimensions are bounded before FFI; no pointer or C allocation escapes.
pub fn filtered_rows(
    input: &[u8],
    row_length: usize,
    height: usize,
) -> Result<Vec<u8>, &'static str> {
    if row_length == 0
        || row_length > 1921
        || height == 0
        || height > 640
        || row_length.checked_mul(height) != Some(input.len())
    {
        return Err("invalid bounded PNG rows");
    }
    let mut raw = Box::new(z::z_stream {
        next_in: ptr::null_mut(),
        avail_in: 0,
        total_in: 0,
        next_out: ptr::null_mut(),
        avail_out: 0,
        total_out: 0,
        msg: ptr::null_mut(),
        state: ptr::null_mut(),
        zalloc: allocate,
        zfree: release,
        opaque: ptr::null_mut(),
        data_type: 0,
        adler: 0,
        reserved: 0,
    });
    // SAFETY: all stream fields are initialized, allocator callbacks are valid,
    // and the owned Box pins the stream address for its entire C lifetime.
    let status = unsafe { z::zng_deflateInit2(&mut *raw, 6, z::Z_DEFLATED, 15, 9, z::Z_FILTERED) };
    if status != z::Z_OK {
        return Err("PNG compressor initialization failed");
    }
    let mut stream = Stream(raw);
    // SAFETY: initialized stream, input is <= 1,229,440 bytes and fits uLong.
    let bound = unsafe { z::deflateBound(&mut *stream.0, input.len() as _) } as usize;
    if bound > 2 * 1024 * 1024 {
        return Err("PNG output bound exceeded");
    }
    let mut output = vec![0u8; bound];
    stream.0.next_out = output.as_mut_ptr();
    stream.0.avail_out = bound as u32;
    for row in input.chunks_exact(row_length) {
        stream.0.next_in = row.as_ptr().cast_mut();
        stream.0.avail_in = row.len() as u32;
        // SAFETY: row remains live and immutable throughout this synchronous
        // call; deflate reads input only. Output points to initialized unique
        // storage with avail_out tracking its remaining capacity.
        let status = unsafe { z::deflate(&mut *stream.0, z::Z_NO_FLUSH) };
        if status != z::Z_OK || stream.0.avail_in != 0 {
            return Err("PNG row compression failed");
        }
    }
    stream.0.next_in = ptr::null_mut();
    stream.0.avail_in = 0;
    // SAFETY: the initialized stream retains valid exclusive output storage;
    // no input remains and deflateBound reserves enough room for finishing.
    let status = unsafe { z::deflate(&mut *stream.0, z::Z_FINISH) };
    if status != z::Z_STREAM_END || stream.0.total_in as usize != input.len() {
        return Err("PNG compression did not finish");
    }
    let size = stream.0.total_out as usize;
    if size > output.len() {
        return Err("invalid PNG compressor length");
    }
    stream.0.next_out = ptr::null_mut();
    stream.0.avail_out = 0;
    output.truncate(size);
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn invalid_buffers_never_reach_ffi() {
        assert!(filtered_rows(&[], 0, 0).is_err());
        assert!(filtered_rows(&[0], 2, 1).is_err());
        assert!(filtered_rows(&[0], 1, usize::MAX).is_err());
        assert!(classic_filtered_rows(&[], 0, 0).is_err());
        assert!(classic_filtered_rows(&[0], 2, 1).is_err());
        assert!(classic_filtered_rows(&[0], 1, usize::MAX).is_err());
    }
    #[test]
    fn repeated_compression_has_no_global_stream_state() {
        let rows = vec![0u8; 1921 * 640];
        let first = filtered_rows(&rows, 1921, 640).expect("compress");
        assert_eq!(first, filtered_rows(&rows, 1921, 640).expect("compress"));
        let first = classic_filtered_rows(&rows, 1921, 640).expect("classic");
        assert_eq!(
            first,
            classic_filtered_rows(&rows, 1921, 640).expect("classic")
        );
    }
    #[test]
    fn maximum_high_entropy_buffers_round_trip_through_independent_decoder() {
        use std::io::Read;
        let mut state = 0x3a7498e2365120dfu64;
        let rows: Vec<u8> = (0..1921 * 640)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state as u8
            })
            .collect();
        for encode in [filtered_rows, classic_filtered_rows] {
            let compressed = encode(&rows, 1921, 640).expect("maximum shape");
            assert!(compressed.len() > 1024 * 1024 && compressed.len() <= 2 * 1024 * 1024);
            let mut decoded = Vec::new();
            flate2::read::ZlibDecoder::new(compressed.as_slice())
                .take(rows.len() as u64 + 1)
                .read_to_end(&mut decoded)
                .expect("independent decoder");
            assert_eq!(decoded, rows);
        }
    }
}
