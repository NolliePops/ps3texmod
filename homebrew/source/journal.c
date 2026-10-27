/* Rollback journal writer/reader. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <dirent.h>

#include "crc32.h"
#include "io.h"
#include "journal.h"
#include "rcf.h"

static int next_index(const char *dir, const char *archive, u32 *next)
{
    DIR *d;
    struct dirent *de;
    char prefix[NAME_LEN + 2];
    size_t plen;
    u32 best = 0;

    str_copy(prefix, sizeof(prefix), archive);
    strcat(prefix, ".");
    plen = strlen(prefix);
    d = opendir(dir);
    if (d == NULL) {
        *next = 1;
        return 0;
    }
    while ((de = readdir(d)) != NULL) {
        const char *name = de->d_name;
        const char *p;
        char *end = NULL;
        unsigned long n;

        if (strncmp(name, prefix, plen) != 0 || !str_ends_with_ci(name, ".journal"))
            continue;
        p = name + plen;
        n = strtoul(p, &end, 10);
        if (end == p || end == NULL || strcmp(end, ".journal") != 0)
            continue;
        if (n > 0 && n < 100000000ul && (u32)n > best)
            best = (u32)n;
    }
    closedir(d);
    *next = best + 1;
    return 0;
}

int journal_create(const char *dir, const Plan *plan, JournalWriter *w,
                   char *err, size_t errcap)
{
    return journal_create_at(dir, plan->archive, plan->name, plan->archive_size,
                             plan->index_offset, plan->index_count, w, err, errcap);
}

int journal_create_at(const char *dir, const char *archive, const char *label,
                      u64 archive_size, u32 index_offset, u32 index_count,
                      JournalWriter *w, char *err, size_t errcap)
{
    char name[NAME_LEN + 32];
    char labelbuf[96];
    u8 hdr[JOURNAL_HEADER + NAME_LEN + 96];
    size_t alen, llen, hlen;
    u32 idx = 1;

    memset(w, 0, sizeof(*w));
    w->fd = -1;
    if (io_mkdir_p(dir) != 0) {
        set_err(err, errcap, "cannot create journal directory %s", dir);
        return -1;
    }
    if (next_index(dir, archive, &idx) != 0) {
        set_err(err, errcap, "cannot scan %s", dir);
        return -1;
    }
    snprintf(name, sizeof(name), "%s.%u.journal", archive, idx);
    if (strlen(dir) + 1 + strlen(name) >= sizeof(w->path)) {
        set_err(err, errcap, "journal path is too long");
        return -1;
    }
    snprintf(w->path, sizeof(w->path), "%s/%s", dir, name);
    if (io_open_new(w->path, &w->fd) != 0) {
        set_err(err, errcap, "cannot create %s", w->path);
        w->fd = -1;
        return -1;
    }

    str_copy(labelbuf, sizeof(labelbuf), label);
    alen = strlen(archive);
    llen = strlen(labelbuf);
    memset(hdr, 0, sizeof(hdr));
    memcpy(hdr, JOURNAL_MAGIC, 8);
    wr_le32(hdr + 8, JOURNAL_VERSION);
    wr_le32(hdr + 12, index_offset);
    wr_le32(hdr + 16, index_count);
    wr_le32(hdr + 20, (u32)alen);
    wr_le32(hdr + 24, (u32)llen);
    wr_le64(hdr + 28, archive_size);
    memcpy(hdr + JOURNAL_HEADER, archive, alen);
    memcpy(hdr + JOURNAL_HEADER + alen, labelbuf, llen);
    hlen = JOURNAL_HEADER + alen + llen;

    if (io_write_at(w->fd, 0, hdr, hlen) != 0) {
        set_err(err, errcap, "cannot write %s", w->path);
        journal_abort(w);
        return -1;
    }
    w->crc = crc32_update(CRC32_SEED, hdr, hlen);
    w->pos = (u64)hlen;
    w->records = 0;
    return 0;
}

int journal_append(JournalWriter *w, const PlanPatch *p, const u8 *slot,
                   u32 slot_size, char *err, size_t errcap)
{
    u8 rec[JOURNAL_RECORD];

    if (slot_size != p->slot_size || p->old_size > slot_size) {
        set_err(err, errcap, "journal record has invalid sizes");
        return -1;
    }
    memcpy(rec, "JREC", 4);
    wr_le32(rec + 4, p->index);
    wr_le32(rec + 8, p->offset);
    wr_le32(rec + 12, p->slot_size);
    wr_le32(rec + 16, p->old_size);
    wr_le32(rec + 20, p->old_crc);
    wr_le32(rec + 24, p->new_crc);

    if (io_write_at(w->fd, w->pos, rec, sizeof(rec)) != 0 ||
        io_write_at(w->fd, w->pos + sizeof(rec), slot, slot_size) != 0) {
        set_err(err, errcap, "cannot write rollback data to %s", w->path);
        return -1;
    }
    w->crc = crc32_update(w->crc, rec, sizeof(rec));
    w->crc = crc32_update(w->crc, slot, slot_size);
    w->pos += sizeof(rec) + slot_size;
    w->records++;
    return 0;
}

int journal_finish(JournalWriter *w, char *err, size_t errcap)
{
    u8 tr[16];

    memcpy(tr, JOURNAL_TRAILER, 8);
    wr_le32(tr + 8, crc32_final(w->crc));
    wr_le32(tr + 12, 0);
    if (io_write_at(w->fd, w->pos, tr, sizeof(tr)) != 0) {
        set_err(err, errcap, "cannot finish %s", w->path);
        return -1;
    }
    if (io_sync(w->fd) != 0) {
        set_err(err, errcap, "cannot flush %s", w->path);
        return -1;
    }
    io_close(w->fd);
    w->fd = -1;
    return 0;
}

void journal_abort(JournalWriter *w)
{
    if (w->fd >= 0)
        io_close(w->fd);
    w->fd = -1;
    if (w->path[0])
        io_unlink(w->path);
}

int journal_scan(const char *dir, const char *archive, JournalEntry *out,
                 u32 max, u32 *total, char *err, size_t errcap)
{
    DIR *d;
    struct dirent *de;
    char prefix[NAME_LEN + 2];
    char legacy[NAME_LEN + 16];
    size_t plen;
    u32 count = 0, matched = 0;

    *total = 0;
    if (!io_is_dir(dir))
        return 0;
    str_copy(prefix, sizeof(prefix), archive);
    strcat(prefix, ".");
    plen = strlen(prefix);
    snprintf(legacy, sizeof(legacy), "%s.journal", archive);
    d = opendir(dir);
    if (d == NULL) {
        set_err(err, errcap, "cannot open %s", dir);
        return -1;
    }
    while ((de = readdir(d)) != NULL) {
        const char *name = de->d_name;
        u32 index = 0;
        u64 size = 0;
        int ok = 0;

        if (strcmp(name, legacy) == 0) {
            ok = 1;
            index = 0;
        } else if (strncmp(name, prefix, plen) == 0 &&
                   str_ends_with_ci(name, ".journal")) {
            const char *p = name + plen;
            char *end = NULL;
            unsigned long n = strtoul(p, &end, 10);

            if (end != p && end != NULL && strcmp(end, ".journal") == 0 &&
                n > 0 && n < 100000000ul) {
                ok = 1;
                index = (u32)n;
            }
        }
        if (!ok)
            continue;
        matched++;
        if (count >= max)
            continue;
        if (strlen(dir) + 1 + strlen(name) >= PATH_LEN)
            continue;
        snprintf(out[count].path, sizeof(out[count].path), "%s/%s", dir, name);
        str_copy(out[count].archive, sizeof(out[count].archive), archive);
        out[count].index = index;
        out[count].records = 0;
        if (io_size(out[count].path, &size) != 0)
            size = 0;
        out[count].bytes = size;
        count++;
    }
    closedir(d);

    /* newest first */
    {
        u32 i, j;
        for (i = 1; i < count; i++) {
            JournalEntry tmp = out[i];
            for (j = i; j > 0 && out[j - 1].index < tmp.index; j--)
                out[j] = out[j - 1];
            out[j] = tmp;
        }
    }
    *total = matched;
    return (int)count;
}

int journal_restore(const JournalEntry *entry, const char *archive_path,
                    ProgressFn cb, void *user, char *err, size_t errcap)
{
    u8 hdr[JOURNAL_HEADER];
    u8 tr[16];
    u8 rec[JOURNAL_RECORD];
    char hdr_archive[NAME_LEN];
    u64 file_size = 0, archive_size = 0, trailer, pos, max_slot = 0;
    u32 version, alen, llen, index_offset, index_count;
    u32 want, got;
    u32 count = 0, i;
    u64 *positions = NULL;
    u8 *slot = NULL;
    RcfInfo rcf;
    int fd = -1, afd = -1, rc = -1;

    memset(&rcf, 0, sizeof(rcf));
    memset(hdr_archive, 0, sizeof(hdr_archive));
    if (io_size(entry->path, &file_size) != 0) {
        set_err(err, errcap, "cannot stat journal %s", entry->path);
        return -1;
    }
    if (file_size < JOURNAL_HEADER + 16 || file_size > JOURNAL_FILE_MAX) {
        set_err(err, errcap, "journal size is invalid");
        return -1;
    }
    if (io_open_ro(entry->path, &fd) != 0) {
        set_err(err, errcap, "cannot open journal %s", entry->path);
        return -1;
    }
    if (io_read_at(fd, 0, hdr, sizeof(hdr)) != 0 ||
        memcmp(hdr, JOURNAL_MAGIC, 8) != 0) {
        set_err(err, errcap, "not a ps3texmod journal");
        goto out;
    }
    version = rd_le32(hdr + 8);
    index_offset = rd_le32(hdr + 12);
    index_count = rd_le32(hdr + 16);
    alen = rd_le32(hdr + 20);
    llen = rd_le32(hdr + 24);
    archive_size = rd_le64(hdr + 28);
    if (version != JOURNAL_VERSION || alen == 0 || alen >= NAME_LEN || llen > 4096 ||
        (u64)JOURNAL_HEADER + alen + llen > file_size - 16) {
        set_err(err, errcap, "journal header is invalid");
        goto out;
    }
    if (io_read_at(fd, JOURNAL_HEADER, hdr_archive, alen) != 0) {
        set_err(err, errcap, "cannot read the journal header");
        goto out;
    }
    hdr_archive[alen] = '\0';
    if (strcmp(hdr_archive, entry->archive) != 0) {
        set_err(err, errcap, "journal is for %s, not %s", hdr_archive, entry->archive);
        goto out;
    }
    trailer = file_size - 16;
    if (io_read_at(fd, trailer, tr, sizeof(tr)) != 0 ||
        memcmp(tr, JOURNAL_TRAILER, 8) != 0) {
        set_err(err, errcap, "journal trailer is missing");
        goto out;
    }
    want = rd_le32(tr + 8);
    if (crc32_fd(fd, 0, trailer, &got) != 0 || got != want) {
        set_err(err, errcap, "journal checksum mismatch");
        goto out;
    }

    if (rcf_load(archive_path, &rcf, err, errcap) != 0)
        goto out;
    if (rcf.file_size != archive_size) {
        set_err(err, errcap, "archive size changed since the journal was written");
        goto out;
    }
    if (rcf.index_offset != index_offset || rcf.count != index_count) {
        set_err(err, errcap, "archive index layout changed; refusing to restore");
        goto out;
    }

    positions = (u64 *)malloc((size_t)JOURNAL_RECORDS_MAX * sizeof(u64));
    if (positions == NULL) {
        set_err(err, errcap, "out of memory restoring the journal");
        goto out;
    }
    pos = (u64)JOURNAL_HEADER + alen + llen;
    while (pos < trailer) {
        u32 slot_size;

        if (pos + JOURNAL_RECORD > trailer ||
            io_read_at(fd, pos, rec, sizeof(rec)) != 0 ||
            memcmp(rec, "JREC", 4) != 0) {
            set_err(err, errcap, "journal record %u is invalid", count);
            goto out;
        }
        slot_size = rd_le32(rec + 12);
        if (slot_size == 0 || slot_size > SLOT_MAX ||
            pos + JOURNAL_RECORD + slot_size > trailer) {
            set_err(err, errcap, "journal record %u has a bad slot size", count);
            goto out;
        }
        if (count >= JOURNAL_RECORDS_MAX) {
            set_err(err, errcap, "journal has too many records");
            goto out;
        }
        positions[count++] = pos;
        if (slot_size > max_slot)
            max_slot = slot_size;
        pos += JOURNAL_RECORD + slot_size;
    }
    if (pos != trailer) {
        set_err(err, errcap, "journal has trailing bytes");
        goto out;
    }

    slot = (u8 *)malloc((size_t)max_slot);
    if (slot == NULL) {
        set_err(err, errcap, "out of memory restoring the journal");
        goto out;
    }
    if (io_open_rw(archive_path, &afd) != 0) {
        set_err(err, errcap, "cannot open %s for writing", archive_path);
        goto out;
    }
    for (i = count; i > 0; i--) {
        u32 index, offset, slot_size, old_size, old_crc;
        u8 sizebuf[4];

        if (io_read_at(fd, positions[i - 1], rec, sizeof(rec)) != 0) {
            set_err(err, errcap, "cannot read journal record %u", i - 1);
            goto out;
        }
        index = rd_le32(rec + 4);
        offset = rd_le32(rec + 8);
        slot_size = rd_le32(rec + 12);
        old_size = rd_le32(rec + 16);
        old_crc = rd_le32(rec + 20);
        if (index >= rcf.count || (u64)offset + slot_size > archive_size ||
            old_size == 0 || old_size > slot_size) {
            set_err(err, errcap, "journal record %u is out of range", i - 1);
            goto out;
        }
        if (io_read_at(fd, positions[i - 1] + JOURNAL_RECORD, slot, slot_size) != 0) {
            set_err(err, errcap, "cannot read journal slot %u", i - 1);
            goto out;
        }
        if (crc32_buffer(slot, old_size) != old_crc) {
            set_err(err, errcap, "journal slot %u is corrupt", i - 1);
            goto out;
        }
        wr_be32(sizebuf, old_size);
        if (io_write_at(afd, offset, slot, slot_size) != 0 ||
            io_write_at(afd, index_offset + (u64)index * 12 + 8, sizebuf, 4) != 0) {
            set_err(err, errcap, "cannot write %s", archive_path);
            goto out;
        }
        if (cb)
            cb(user, count - i + 1, count, "Restoring");
    }
    if (io_sync(afd) != 0) {
        set_err(err, errcap, "cannot flush %s", archive_path);
        goto out;
    }
    rc = 0;

out:
    if (afd >= 0)
        io_close(afd);
    if (fd >= 0)
        io_close(fd);
    free(slot);
    free(positions);
    rcf_close(&rcf);
    if (rc == 0) {
        if (io_unlink(entry->path) != 0)
            set_err(err, errcap, "restored, but could not delete %s", entry->path);
    }
    return rc;
}
