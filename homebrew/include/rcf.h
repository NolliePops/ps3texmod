/* Read-only RCF (Cement) index access for verification. */
#ifndef PS3TEXMOD_RCF_H
#define PS3TEXMOD_RCF_H

#include <ppu-types.h>
#include <stddef.h>

#define RCF_MAGIC "ATG CORE CEMENT LIBRARY"
#define RCF_HEADER_SIZE 0x3Cu
#define RCF_COUNT_MAX 2000000u
#define RCF_INDEX_MAX (24u * 1024u * 1024u)

typedef struct {
    u32 index_offset;
    u32 index_size;
    u32 count;
    u64 file_size;
    u8 *index;      /* raw big-endian triples */
    u32 *offsets;   /* ascending payload offsets */
} RcfInfo;

int rcf_load(const char *path, RcfInfo *info, char *err, size_t errcap);
void rcf_close(RcfInfo *info);

/* End of the 0x800-aligned payload slot that starts at `offset`. */
u64 rcf_slot_end(const RcfInfo *info, u32 offset);

/* 1 if any index entry has this hash (used to tell "moved" from "absent"). */
int rcf_entry_present(const RcfInfo *info, u32 hash);

#endif
