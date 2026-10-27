/* Two-pass verification and transactional in-place application. */
#ifndef PS3TEXMOD_INSTALL_H
#define PS3TEXMOD_INSTALL_H

#include <ppu-types.h>
#include <stddef.h>

#include "journal.h"
#include "plan.h"

typedef struct {
    u32 pending;
    u32 already;
    u32 problems;
    u32 relocated;     /* patches whose entry was found somewhere else */
    char first_problem[ERR_LEN];
} VerifyResult;

typedef struct {
    u32 applied;
    u32 already;
    u64 bytes;
    char journal[PATH_LEN];
} ApplyResult;

/* INSTALL_EXACT: the archive must be byte-layout identical to the plan's.
 * INSTALL_RELOCATE: find every target by entry hash in the real index and
 * apply only if its stored bytes match the plan's original CRC. */
#define INSTALL_EXACT    0
#define INSTALL_RELOCATE 1

int install_verify(const Plan *plan, const char *archive_path, VerifyResult *res,
                   ProgressFn cb, void *user, char *err, size_t errcap);
int install_verify_mode(const Plan *plan, const char *archive_path,
                        VerifyResult *res, ProgressFn cb, void *user,
                        int mode, char *err, size_t errcap);

int install_apply(const Plan *plan, const char *archive_path,
                  const char *journal_dir, ApplyResult *res,
                  ProgressFn cb, void *user, char *err, size_t errcap);
int install_apply_mode(const Plan *plan, const char *archive_path,
                       const char *journal_dir, ApplyResult *res,
                       ProgressFn cb, void *user, int mode,
                       char *err, size_t errcap);

#endif
