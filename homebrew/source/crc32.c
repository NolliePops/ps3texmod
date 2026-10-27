/* CRC-32 implementation, table generated once at startup. */
#include <stdlib.h>

#include <sys/file.h>

#include "crc32.h"

static u32 crc_table[256];
static int crc_ready;

void crc32_init(void)
{
    u32 i, j, c;

    if (crc_ready)
        return;
    for (i = 0; i < 256; i++) {
        c = i;
        for (j = 0; j < 8; j++)
            c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        crc_table[i] = c;
    }
    crc_ready = 1;
}

u32 crc32_update(u32 state, const void *data, size_t len)
{
    const u8 *p = (const u8 *)data;

    crc32_init();
    while (len--)
        state = crc_table[(state ^ *p++) & 0xFFu] ^ (state >> 8);
    return state;
}

u32 crc32_final(u32 state)
{
    return state ^ 0xFFFFFFFFu;
}

u32 crc32_buffer(const void *data, size_t len)
{
    return crc32_final(crc32_update(CRC32_SEED, data, len));
}

int crc32_fd(int fd, u64 offset, u64 size, u32 *out)
{
    u8 buf[64 * 1024];
    u32 state = CRC32_SEED;
    u64 pos = offset;
    u64 left = size;

    crc32_init();
    while (left > 0) {
        u64 got = 0;
        u64 want = left > sizeof(buf) ? (u64)sizeof(buf) : left;

        if (sysLv2FsLSeek64(fd, pos, 0 /* SEEK_SET */, &got) != 0)
            return -1;
        if (sysLv2FsRead(fd, buf, want, &got) != 0)
            return -1;
        if (got != want)
            return -1;
        state = crc32_update(state, buf, (size_t)got);
        pos += got;
        left -= got;
    }
    *out = crc32_final(state);
    return 0;
}
