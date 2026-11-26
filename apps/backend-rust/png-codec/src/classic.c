/* Bounded encoder only. All zlib symbols use Z_PREFIX; no inflate is linked. */
#include "zlib.h"
#include <string.h>
#include <stdint.h>
#include <stdlib.h>

static voidpf owned_allocate(voidpf opaque, uInt items, uInt size) {
    (void)opaque;
    if (size && items > SIZE_MAX / size) return Z_NULL;
    return calloc(items, size);
}
static void owned_release(voidpf opaque, voidpf address) {
    (void)opaque;
    free(address);
}

int mainkraft_zlib13_rows(const unsigned char *input, size_t row_length,
                         size_t height, unsigned char *output,
                         size_t capacity, size_t *written) {
    z_stream stream;
    size_t row;
    int result;
    if (!input || !output || !written || !row_length || row_length > 1921 ||
        !height || height > 640 || capacity > 2 * 1024 * 1024)
        return Z_STREAM_ERROR;
    *written = 0;
    memset(&stream, 0, sizeof(stream));
    stream.zalloc = owned_allocate;
    stream.zfree = owned_release;
    result = deflateInit2(&stream, 6, Z_DEFLATED, 15, 9, Z_FILTERED);
    if (result != Z_OK) return result;
    if (deflateBound(&stream, (uLong)(row_length * height)) > capacity) {
        deflateEnd(&stream);
        return Z_BUF_ERROR;
    }
    stream.next_out = output;
    stream.avail_out = (uInt)capacity;
    for (row = 0; row < height; row++) {
        stream.next_in = (Bytef *)(input + row * row_length);
        stream.avail_in = (uInt)row_length;
        result = deflate(&stream, Z_NO_FLUSH);
        if (result != Z_OK || stream.avail_in != 0) {
            deflateEnd(&stream);
            return Z_STREAM_ERROR;
        }
    }
    stream.next_in = Z_NULL;
    stream.avail_in = 0;
    result = deflate(&stream, Z_FINISH);
    if (result == Z_STREAM_END && stream.total_in == row_length * height &&
        stream.total_out <= capacity) {
        *written = (size_t)stream.total_out;
        result = Z_OK;
    } else result = Z_STREAM_ERROR;
    deflateEnd(&stream);
    return result;
}
