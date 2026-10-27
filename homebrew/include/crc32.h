/* CRC-32 (IEEE 802.3, same polynomial/result as zlib's crc32()). */
#ifndef PS3TEXMOD_CRC32_H
#define PS3TEXMOD_CRC32_H

#include <ppu-types.h>
#include <stddef.h>

#define CRC32_SEED 0xFFFFFFFFu

void crc32_init(void);
u32 crc32_update(u32 state, const void *data, size_t len);
u32 crc32_final(u32 state);
u32 crc32_buffer(const void *data, size_t len);

/* CRC of [offset, offset+size) in an already-open file. Does not restore the
 * file position, so callers must seek before their next read. */
int crc32_fd(int fd, u64 offset, u64 size, u32 *out);

#endif
