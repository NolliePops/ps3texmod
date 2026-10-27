/* Rollback journals (same file format as ps3texmod/plan.py). */
#ifndef PS3TEXMOD_JOURNAL_H
#define PS3TEXMOD_JOURNAL_H

#include <ppu-types.h>
#include <stddef.h>

#include "common.h"
#include "plan.h"

typedef void (*ProgressFn)(void *user, u32 done, u32 total, const char *stage);

typedef struct {
    char path[PATH_LEN];
    int fd;
    u32 crc;       /* running CRC state (pre-final xor) */
    u64 pos;       /* next append offset */
    u32 records;
} JournalWriter;

typedef struct {
    char path[PATH_LEN];
    char archive[NAME_LEN];
    u32 index;     /* numeric suffix; 0 = legacy append journal */
    u32 records;
    u64 bytes;
} JournalEntry;

int journal_create(const char *dir, const Plan *plan, JournalWriter *w,
                   char *err, size_t errcap);
/* Same, but with the layout of the archive actually being modified.  Needed by
 * relocated applies, where the journal must record the real index offset/count
 * so that restore validates against the real archive. */
int journal_create_at(const char *dir, const char *archive, const char *label,
                      u64 archive_size, u32 index_offset, u32 index_count,
                      JournalWriter *w, char *err, size_t errcap);
int journal_append(JournalWriter *w, const PlanPatch *p, const u8 *slot,
                   u32 slot_size, char *err, size_t errcap);
int journal_finish(JournalWriter *w, char *err, size_t errcap);
void journal_abort(JournalWriter *w);

/* Finds journals for `archive`, newest first. Returns the number stored in
 * out (capped at max); total matched is returned through `total`. */
int journal_scan(const char *dir, const char *archive, JournalEntry *out,
                 u32 max, u32 *total, char *err, size_t errcap);

int journal_restore(const JournalEntry *entry, const char *archive_path,
                    ProgressFn cb, void *user, char *err, size_t errcap);

#endif
