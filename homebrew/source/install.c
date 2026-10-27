/* Verification and in-place application of a .ps3patch plan.
 *
 * Two modes:
 *
 *   INSTALL_EXACT     the archive must be the one the plan was built from.
 *                     Archive size, index offset/count, the index position of
 *                     every target, its payload offset and its aligned slot
 *                     are all compared before anything is written.
 *
 *   INSTALL_RELOCATE  the archive is a different build (different size, index
 *                     layout, entry order).  Each target is looked up by its
 *                     entry hash in the real index and applied only when its
 *                     stored bytes are byte-identical to the plan's original
 *                     (CRC-32) and the replacement fits the real slot.  The
 *                     offset, index position and slot size then come from the
 *                     real archive, and the journal records those.
 *
 * In both modes the archive is never rebuilt or resized.  Every replacement
 * must fit the existing 0x800-aligned payload slot, which is what makes
 * rollback byte-exact.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "common.h"
#include "crc32.h"
#include "install.h"
#include "io.h"
#include "rcf.h"

typedef struct {
    u32 index;
    u32 offset;
    u32 slot_size;
    int already;
} PatchLoc;

typedef struct {
    u64 archive_size;
    u32 index_offset;
    u32 index_count;
} ArchiveLayout;

static void problem(VerifyResult *res, const char *fmt, ...)
{
    va_list ap;

    if (res->problems == 0) {
        va_start(ap, fmt);
        vsnprintf(res->first_problem, sizeof(res->first_problem), fmt, ap);
        va_end(ap);
        res->first_problem[sizeof(res->first_problem) - 1] = '\0';
    }
    res->problems++;
}

static int check_layout(const Plan *plan, const RcfInfo *rcf, VerifyResult *res)
{
    if (rcf->file_size != plan->archive_size) {
        problem(res, "archive size %llu differs from the plan's %llu "
                     "(different game build?)",
                (unsigned long long)rcf->file_size,
                (unsigned long long)plan->archive_size);
        return -1;
    }
    if (rcf->index_offset != plan->index_offset || rcf->count != plan->index_count) {
        problem(res, "archive index layout differs from the plan");
        return -1;
    }
    return 0;
}

static int blob_ok(const Plan *plan, const PlanPatch *p, char *err, size_t errcap)
{
    u32 blob_crc;

    if (crc32_fd(plan->fd, p->blob_offset, p->new_size, &blob_crc) != 0) {
        set_err(err, errcap, "%08X: cannot read the replacement from the plan", p->hash);
        return -1;
    }
    if (blob_crc != p->new_crc) {
        set_err(err, errcap, "%08X: replacement bytes in the plan are corrupt", p->hash);
        return -1;
    }
    return 0;
}

/* Locate one target in the loaded archive and classify it as pending/already.
 * On failure `err` explains why; the caller turns it into a plan problem. */
static int resolve_one(const Plan *plan, const RcfInfo *rcf, int afd,
                       const PlanPatch *p, int mode, PatchLoc *loc,
                       char *err, size_t errcap)
{
    u32 i, size, off, cur_crc;
    u64 slot_end;

    memset(loc, 0, sizeof(*loc));
    if (blob_ok(plan, p, err, errcap) != 0)
        return -1;

    if (mode == INSTALL_EXACT) {
        const u8 *rec;

        if (p->index >= rcf->count) {
            set_err(err, errcap, "%08X: index position %u is out of range",
                    p->hash, p->index);
            return -1;
        }
        rec = rcf->index + (size_t)p->index * 12;
        if (rd_be32(rec) != p->hash) {
            set_err(err, errcap, "%08X: archive index does not contain this entry",
                    p->hash);
            return -1;
        }
        if (rd_be32(rec + 4) != p->offset) {
            set_err(err, errcap, "%08X: payload moved (0x%X != 0x%X)",
                    p->hash, rd_be32(rec + 4), p->offset);
            return -1;
        }
        slot_end = rcf_slot_end(rcf, p->offset);
        if (slot_end < p->offset || slot_end - p->offset != p->slot_size) {
            set_err(err, errcap, "%08X: aligned slot size changed", p->hash);
            return -1;
        }
        size = rd_be32(rec + 8);
        if (size != p->old_size && size != p->new_size) {
            set_err(err, errcap, "%08X: stored size %u is neither original nor patched",
                    p->hash, size);
            return -1;
        }
        if (crc32_fd(afd, p->offset, size, &cur_crc) != 0) {
            set_err(err, errcap, "%08X: cannot read the current payload", p->hash);
            return -1;
        }
        if (size == p->new_size && cur_crc == p->new_crc) {
            loc->already = 1;
        } else if (size == p->old_size && cur_crc == p->old_crc) {
            loc->already = 0;
        } else {
            set_err(err, errcap,
                    "%08X: original data differs (wrong build or already modded)",
                    p->hash);
            return -1;
        }
        loc->index = p->index;
        loc->offset = p->offset;
        loc->slot_size = p->slot_size;
        return 0;
    }

    /* Relocated: find the entry by hash, then prove the stored bytes match.
     * Size alone is not enough - a different build can share it. */
    for (i = 0; i < rcf->count; i++) {
        const u8 *rec = rcf->index + (size_t)i * 12;

        if (rd_be32(rec) != p->hash)
            continue;
        off = rd_be32(rec + 4);
        size = rd_be32(rec + 8);
        if (size != p->old_size && size != p->new_size)
            continue;
        if (crc32_fd(afd, off, size, &cur_crc) != 0)
            continue;
        if (size == p->new_size && cur_crc == p->new_crc)
            loc->already = 1;
        else if (size == p->old_size && cur_crc == p->old_crc)
            loc->already = 0;
        else
            continue;

        slot_end = rcf_slot_end(rcf, off);
        if (slot_end < off)
            continue;
        loc->index = i;
        loc->offset = off;
        loc->slot_size = (u32)(slot_end - off);
        if (p->new_size > loc->slot_size) {
            set_err(err, errcap,
                    "%08X: replacement (%u bytes) does not fit the %u-byte slot",
                    p->hash, p->new_size, loc->slot_size);
            return -1;
        }
        if (loc->slot_size > SLOT_MAX) {
            set_err(err, errcap, "%08X: slot is too large to journal (%u bytes)",
                    p->hash, loc->slot_size);
            return -1;
        }
        return 0;
    }

    if (rcf_entry_present(rcf, p->hash)) {
        set_err(err, errcap,
                "%08X: entry data differs from the plan (different game build); "
                "rebuild the plan from this archive", p->hash);
    } else {
        set_err(err, errcap, "%08X: archive has no entry with this hash", p->hash);
    }
    return -1;
}

static int verify_impl(const Plan *plan, const char *archive_path,
                       VerifyResult *res, ProgressFn cb, void *user,
                       int mode, ArchiveLayout *layout, char *err, size_t errcap)
{
    RcfInfo rcf;
    int afd = -1, rc = -1;
    u32 i;

    memset(res, 0, sizeof(*res));
    memset(&rcf, 0, sizeof(rcf));
    memset(layout, 0, sizeof(*layout));

    if (!plan->crc_checked) {
        set_err(err, errcap, "internal: plan integrity was not checked");
        return -1;
    }
    if (rcf_load(archive_path, &rcf, err, errcap) != 0)
        return -1;
    layout->archive_size = rcf.file_size;
    layout->index_offset = rcf.index_offset;
    layout->index_count = rcf.count;

    if (mode == INSTALL_EXACT && check_layout(plan, &rcf, res) != 0) {
        set_err(err, errcap, "%s", res->first_problem);
        goto out;
    }
    if (io_open_ro(archive_path, &afd) != 0) {
        set_err(err, errcap, "cannot open %s", archive_path);
        goto out;
    }

    for (i = 0; i < plan->count; i++) {
        const PlanPatch *p = &plan->patches[i];
        PatchLoc loc;
        char why[ERR_LEN];

        if (resolve_one(plan, &rcf, afd, p, mode, &loc, why, sizeof(why)) != 0) {
            problem(res, "%s", why);
            continue;
        }
        if (loc.already)
            res->already++;
        else
            res->pending++;
        if (loc.index != p->index || loc.offset != p->offset ||
            loc.slot_size != p->slot_size)
            res->relocated++;
        if (cb)
            cb(user, i + 1, plan->count, "Verifying");
    }
    rc = 0;

out:
    if (afd >= 0)
        io_close(afd);
    rcf_close(&rcf);
    if (rc == 0 && res->problems) {
        set_err(err, errcap, "%s", res->first_problem);
        rc = -1;
    }
    return rc;
}

int install_verify(const Plan *plan, const char *archive_path, VerifyResult *res,
                   ProgressFn cb, void *user, char *err, size_t errcap)
{
    ArchiveLayout layout;

    return verify_impl(plan, archive_path, res, cb, user, INSTALL_EXACT,
                       &layout, err, errcap);
}

int install_verify_mode(const Plan *plan, const char *archive_path,
                        VerifyResult *res, ProgressFn cb, void *user,
                        int mode, char *err, size_t errcap)
{
    ArchiveLayout layout;

    return verify_impl(plan, archive_path, res, cb, user, mode,
                       &layout, err, errcap);
}

static int write_zeros(int fd, u64 offset, u64 count, char *err, size_t errcap)
{
    static const u8 zeros[4096];
    u64 done = 0;

    while (done < count) {
        size_t chunk = (size_t)((count - done) > sizeof(zeros) ? sizeof(zeros) : (count - done));

        if (io_write_at(fd, offset + done, zeros, chunk) != 0) {
            set_err(err, errcap, "cannot write archive padding");
            return -1;
        }
        done += chunk;
    }
    return 0;
}

static int apply_impl(const Plan *plan, const char *archive_path,
                      const char *journal_dir, ApplyResult *res,
                      ProgressFn cb, void *user, int mode,
                      char *err, size_t errcap)
{
    RcfInfo rcf;
    VerifyResult vr;
    ArchiveLayout layout;
    JournalWriter jw;
    PatchLoc *locs = NULL;
    u8 *todo = NULL, *slotbuf = NULL, *blobbuf = NULL;
    int afd = -1, rc = -1, journal_open = 0;
    u32 max_slot = 0, max_new = 0, i, pending = 0, backed_up = 0;

    memset(res, 0, sizeof(*res));
    memset(&rcf, 0, sizeof(rcf));
    memset(&jw, 0, sizeof(jw));
    jw.fd = -1;

    if (verify_impl(plan, archive_path, &vr, cb, user, mode, &layout,
                    err, errcap) != 0)
        return -1;
    res->already = vr.already;
    if (vr.pending == 0) {
        res->applied = 0;
        return 0;
    }

    if (rcf_load(archive_path, &rcf, err, errcap) != 0)
        goto out;
    if (rcf.file_size != layout.archive_size ||
        rcf.index_offset != layout.index_offset || rcf.count != layout.index_count) {
        set_err(err, errcap, "archive layout changed during apply");
        goto out;
    }
    if (io_open_rw(archive_path, &afd) != 0) {
        set_err(err, errcap, "cannot open %s for writing", archive_path);
        goto out;
    }

    locs = (PatchLoc *)calloc(plan->count, sizeof(PatchLoc));
    todo = (u8 *)calloc(plan->count, 1);
    if (locs == NULL || todo == NULL) {
        set_err(err, errcap, "out of memory applying the plan");
        goto out;
    }
    for (i = 0; i < plan->count; i++) {
        PatchLoc *loc = &locs[i];

        if (resolve_one(plan, &rcf, afd, &plan->patches[i], mode, loc,
                        err, errcap) != 0)
            goto out;
        if (loc->already)
            continue;
        todo[i] = 1;
        pending++;
        if (loc->slot_size > max_slot)
            max_slot = loc->slot_size;
        if (plan->patches[i].new_size > max_new)
            max_new = plan->patches[i].new_size;
    }
    if (pending != vr.pending) {
        set_err(err, errcap, "archive changed between verification and backup");
        goto out;
    }

    slotbuf = (u8 *)malloc(max_slot ? max_slot : 1);
    blobbuf = (u8 *)malloc(max_new ? max_new : 1);
    if (slotbuf == NULL || blobbuf == NULL) {
        set_err(err, errcap, "out of memory applying the plan");
        goto out;
    }

    /* Step 1: write a complete rollback journal before touching the archive. */
    if (journal_create_at(journal_dir, plan->archive, plan->name,
                          layout.archive_size, layout.index_offset,
                          layout.index_count, &jw, err, errcap) != 0)
        goto out;
    journal_open = 1;
    for (i = 0; i < plan->count; i++) {
        const PlanPatch *p = &plan->patches[i];
        PlanPatch cur = *p;

        if (!todo[i])
            continue;
        if (io_read_at(afd, locs[i].offset, slotbuf, locs[i].slot_size) != 0) {
            set_err(err, errcap, "%08X: cannot read the original slot", p->hash);
            goto out;
        }
        if (crc32_buffer(slotbuf, p->old_size) != p->old_crc) {
            set_err(err, errcap, "%08X: original slot checksum changed", p->hash);
            goto out;
        }
        cur.index = locs[i].index;
        cur.offset = locs[i].offset;
        cur.slot_size = locs[i].slot_size;
        if (journal_append(&jw, &cur, slotbuf, locs[i].slot_size, err, errcap) != 0)
            goto out;
        if (cb)
            cb(user, ++backed_up, vr.pending, "Backing up");
    }
    if (journal_finish(&jw, err, errcap) != 0)
        goto out;
    journal_open = 0;
    str_copy(res->journal, sizeof(res->journal), jw.path);

    /* Step 2: write replacement bytes and the new stored-size fields. */
    for (i = 0; i < plan->count; i++) {
        const PlanPatch *p = &plan->patches[i];
        u8 sizebuf[4];

        if (!todo[i])
            continue;
        if (plan_read_blob(plan, i, blobbuf, err, errcap) != 0)
            goto out;
        if (crc32_buffer(blobbuf, p->new_size) != p->new_crc) {
            set_err(err, errcap, "%08X: replacement bytes are corrupt", p->hash);
            goto out;
        }
        if (io_write_at(afd, locs[i].offset, blobbuf, p->new_size) != 0) {
            set_err(err, errcap, "%08X: cannot write the replacement", p->hash);
            goto out;
        }
        if (locs[i].slot_size > p->new_size &&
            write_zeros(afd, (u64)locs[i].offset + p->new_size,
                        locs[i].slot_size - p->new_size, err, errcap) != 0)
            goto out;
        wr_be32(sizebuf, p->new_size);
        if (io_write_at(afd, (u64)layout.index_offset + (u64)locs[i].index * 12 + 8,
                        sizebuf, 4) != 0) {
            set_err(err, errcap, "%08X: cannot update the index size", p->hash);
            goto out;
        }
        res->applied++;
        res->bytes += p->new_size;
        if (cb)
            cb(user, res->applied, pending, "Writing");
    }
    if (io_sync(afd) != 0) {
        set_err(err, errcap, "cannot flush %s", archive_path);
        goto out;
    }
    rc = 0;

out:
    if (journal_open)
        journal_abort(&jw);
    if (afd >= 0)
        io_close(afd);
    rcf_close(&rcf);
    free(locs);
    free(todo);
    free(slotbuf);
    free(blobbuf);
    return rc;
}

int install_apply(const Plan *plan, const char *archive_path,
                  const char *journal_dir, ApplyResult *res,
                  ProgressFn cb, void *user, char *err, size_t errcap)
{
    return apply_impl(plan, archive_path, journal_dir, res, cb, user,
                      INSTALL_EXACT, err, errcap);
}

int install_apply_mode(const Plan *plan, const char *archive_path,
                       const char *journal_dir, ApplyResult *res,
                       ProgressFn cb, void *user, int mode,
                       char *err, size_t errcap)
{
    return apply_impl(plan, archive_path, journal_dir, res, cb, user,
                      mode, err, errcap);
}
