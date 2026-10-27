/* Precomputed .ps3patch plans (little-endian, see ps3texmod/plan.py). */
#ifndef PS3TEXMOD_PLAN_H
#define PS3TEXMOD_PLAN_H

#include <ppu-types.h>
#include <stddef.h>

#include "common.h"

typedef struct {
    u32 hash;
    u32 index;
    u32 offset;
    u32 old_size;
    u32 new_size;
    u32 slot_size;
    u32 blob_offset;
    u32 old_crc;
    u32 new_crc;
    u32 label_rel;
    u32 label_len;
} PlanPatch;

typedef struct {
    char path[PATH_LEN];
    char name[NAME_LEN];
    char archive[NAME_LEN];
    u64 archive_size;
    u32 index_offset;
    u32 index_count;
    u32 flags;
    u32 count;
    u32 string_bytes;
    u32 blob_bytes;
    PlanPatch *patches;
    u8 *meta;          /* strings + record table */
    size_t meta_size;
    int fd;
    int crc_checked;
} Plan;

/* check_crc=0 is the fast list load; 1 verifies the whole-file trailer CRC. */
int plan_open(const char *path, Plan *plan, int check_crc, char *err, size_t errcap);
void plan_close(Plan *plan);
/* Copies patch i's label into buf (always NUL terminated). */
void plan_label_copy(const Plan *plan, u32 i, char *buf, size_t cap);
/* Reads patch i's replacement bytes into buf (new_size bytes). */
int plan_read_blob(const Plan *plan, u32 i, void *buf, char *err, size_t errcap);

#endif
