/* RCF index parser: header + hash-sorted index only (no payload parsing). */
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "io.h"
#include "rcf.h"

static int cmp_u32(const void *a, const void *b)
{
    u32 x = *(const u32 *)a, y = *(const u32 *)b;

    return (x > y) - (x < y);
}

int rcf_load(const char *path, RcfInfo *info, char *err, size_t errcap)
{
    u8 hdr[RCF_HEADER_SIZE];
    int fd = -1;
    u32 i;

    memset(info, 0, sizeof(*info));
    if (io_size(path, &info->file_size) != 0) {
        set_err(err, errcap, "cannot stat %s", path);
        return -1;
    }
    if (info->file_size < RCF_HEADER_SIZE) {
        set_err(err, errcap, "%s is too small for an RCF archive", path);
        return -1;
    }
    if (io_open_ro(path, &fd) != 0) {
        set_err(err, errcap, "cannot open %s", path);
        return -1;
    }
    if (io_read_at(fd, 0, hdr, sizeof(hdr)) != 0) {
        set_err(err, errcap, "cannot read the RCF header");
        goto fail;
    }
    if (memcmp(hdr, RCF_MAGIC, 23) != 0) {
        set_err(err, errcap, "%s is not a Cement RCF archive", path);
        goto fail;
    }
    info->index_offset = rd_be32(hdr + 0x24);
    info->index_size = rd_be32(hdr + 0x28);
    info->count = rd_be32(hdr + 0x38);
    if (info->count == 0 || info->count > RCF_COUNT_MAX) {
        set_err(err, errcap, "implausible RCF entry count %u", info->count);
        goto fail;
    }
    if (info->index_size != 12u * info->count) {
        set_err(err, errcap, "RCF index size %u does not match %u entries",
                info->index_size, info->count);
        goto fail;
    }
    if (info->index_size > RCF_INDEX_MAX ||
        (u64)info->index_offset + info->index_size > info->file_size) {
        set_err(err, errcap, "RCF index is out of range");
        goto fail;
    }
    info->index = (u8 *)malloc(info->index_size);
    info->offsets = (u32 *)malloc((size_t)info->count * sizeof(u32));
    if (info->index == NULL || info->offsets == NULL) {
        set_err(err, errcap, "out of memory reading the RCF index");
        goto fail;
    }
    if (io_read_at(fd, info->index_offset, info->index, info->index_size) != 0) {
        set_err(err, errcap, "cannot read the RCF index");
        goto fail;
    }
    for (i = 0; i < info->count; i++) {
        u32 off = rd_be32(info->index + (size_t)i * 12 + 4);
        u32 size = rd_be32(info->index + (size_t)i * 12 + 8);

        if ((u64)off + size > info->file_size) {
            set_err(err, errcap, "RCF entry %u lies outside the archive", i);
            goto fail;
        }
        info->offsets[i] = off;
    }
    qsort(info->offsets, info->count, sizeof(u32), cmp_u32);
    io_close(fd);
    return 0;

fail:
    io_close(fd);
    rcf_close(info);
    return -1;
}

void rcf_close(RcfInfo *info)
{
    free(info->index);
    free(info->offsets);
    info->index = NULL;
    info->offsets = NULL;
}

u64 rcf_slot_end(const RcfInfo *info, u32 offset)
{
    u32 lo = 0, hi = info->count;

    while (lo < hi) {
        u32 mid = lo + (hi - lo) / 2;

        if (info->offsets[mid] <= offset)
            lo = mid + 1;
        else
            hi = mid;
    }
    if (lo < info->count)
        return info->offsets[lo];
    return info->file_size;
}

int rcf_entry_present(const RcfInfo *info, u32 hash)
{
    u32 i;

    for (i = 0; i < info->count; i++) {
        if (rd_be32(info->index + (size_t)i * 12) == hash)
            return 1;
    }
    return 0;
}
