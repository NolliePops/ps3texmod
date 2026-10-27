/* .ps3patch parser. */
#include <stdlib.h>
#include <string.h>

#include "crc32.h"
#include "io.h"
#include "plan.h"

static int check_trailer(Plan *plan, u64 file_size, char *err, size_t errcap)
{
    u8 tr[16];
    u32 want, got;

    if (io_read_at(plan->fd, file_size - 16, tr, sizeof(tr)) != 0) {
        set_err(err, errcap, "cannot read the plan trailer");
        return -1;
    }
    if (memcmp(tr, PLAN_TRAILER, 8) != 0) {
        set_err(err, errcap, "plan trailer is missing");
        return -1;
    }
    want = rd_le32(tr + 8);
    if (crc32_fd(plan->fd, 0, file_size - 16, &got) != 0) {
        set_err(err, errcap, "cannot checksum the plan");
        return -1;
    }
    if (got != want) {
        set_err(err, errcap, "plan checksum mismatch (corrupt file)");
        return -1;
    }
    return 0;
}

int plan_open(const char *path, Plan *plan, int check_crc, char *err, size_t errcap)
{
    u8 hdr[PLAN_HEADER];
    u64 file_size = 0, trailer, blob_start, region;
    u32 version, header_bytes, archive_len, plan_len, string_bytes;
    u32 i;

    memset(plan, 0, sizeof(*plan));
    plan->fd = -1;
    str_copy(plan->path, sizeof(plan->path), path);

    if (io_size(path, &file_size) != 0) {
        set_err(err, errcap, "cannot stat plan %s", path);
        return -1;
    }
    if (file_size < PLAN_HEADER + 16) {
        set_err(err, errcap, "plan is truncated");
        return -1;
    }
    if (file_size > PLAN_FILE_MAX) {
        set_err(err, errcap, "plan is too large (limit 512 MiB)");
        return -1;
    }
    if (io_open_ro(path, &plan->fd) != 0) {
        set_err(err, errcap, "cannot open plan %s", path);
        return -1;
    }
    if (io_read_at(plan->fd, 0, hdr, sizeof(hdr)) != 0) {
        set_err(err, errcap, "cannot read the plan header");
        goto fail;
    }
    if (memcmp(hdr, PLAN_MAGIC, 8) != 0) {
        set_err(err, errcap, "not a ps3texmod plan (bad magic)");
        goto fail;
    }
    version = rd_le32(hdr + 8);
    header_bytes = rd_le32(hdr + 12);
    archive_len = rd_le32(hdr + 16);
    plan_len = rd_le32(hdr + 20);
    plan->count = rd_le32(hdr + 24);
    plan->blob_bytes = rd_le32(hdr + 28);
    plan->index_offset = rd_le32(hdr + 0x20);
    plan->index_count = rd_le32(hdr + 0x24);
    plan->archive_size = rd_le64(hdr + 0x28);
    plan->flags = rd_le32(hdr + 0x30);
    string_bytes = rd_le32(hdr + 0x34);

    if (version != PLAN_VERSION) {
        set_err(err, errcap, "unsupported plan version %u", version);
        goto fail;
    }
    if (header_bytes != PLAN_HEADER) {
        set_err(err, errcap, "unexpected plan header size %u", header_bytes);
        goto fail;
    }
    if (plan->count == 0 || plan->count > PLAN_COUNT_MAX) {
        set_err(err, errcap, "plan has an implausible patch count");
        goto fail;
    }
    if ((plan->flags & PLAN_FLAG_INPLACE) == 0) {
        set_err(err, errcap, "plan needs a full archive rebuild; not supported on console");
        goto fail;
    }
    if (archive_len == 0 || archive_len >= NAME_LEN ||
        plan_len >= NAME_LEN || archive_len + plan_len > string_bytes) {
        set_err(err, errcap, "plan names are invalid");
        goto fail;
    }
    if (string_bytes > PLAN_META_MAX) {
        set_err(err, errcap, "plan metadata is too large");
        goto fail;
    }
    if (plan->index_count == 0 ||
        (u64)plan->index_offset + 12ull * plan->index_count > plan->archive_size) {
        set_err(err, errcap, "plan index layout is invalid");
        goto fail;
    }

    trailer = file_size - 16;
    blob_start = (u64)PLAN_HEADER + string_bytes + (u64)PLAN_RECORD * plan->count;
    if (blob_start > trailer ||
        (u64)plan->blob_bytes > trailer - blob_start) {
        set_err(err, errcap, "plan records or blobs overrun the file");
        goto fail;
    }
    region = (u64)string_bytes + (u64)PLAN_RECORD * plan->count;
    plan->meta = (u8 *)malloc((size_t)region);
    plan->patches = (PlanPatch *)calloc(plan->count, sizeof(PlanPatch));
    if (plan->meta == NULL || plan->patches == NULL) {
        set_err(err, errcap, "out of memory reading the plan");
        goto fail;
    }
    plan->meta_size = (size_t)region;
    if (io_read_at(plan->fd, PLAN_HEADER, plan->meta, (size_t)region) != 0) {
        set_err(err, errcap, "cannot read plan records");
        goto fail;
    }
    str_copy_n(plan->name, sizeof(plan->name),
               (const char *)plan->meta + archive_len, plan_len);
    str_copy_n(plan->archive, sizeof(plan->archive),
               (const char *)plan->meta, archive_len);

    for (i = 0; i < plan->count; i++) {
        const u8 *rec = plan->meta + string_bytes + (size_t)i * PLAN_RECORD;
        PlanPatch *p = &plan->patches[i];
        u32 label_off;

        p->hash = rd_le32(rec + 0x00);
        p->index = rd_le32(rec + 0x04);
        p->offset = rd_le32(rec + 0x08);
        p->old_size = rd_le32(rec + 0x0C);
        p->new_size = rd_le32(rec + 0x10);
        p->slot_size = rd_le32(rec + 0x14);
        p->blob_offset = rd_le32(rec + 0x18);
        p->old_crc = rd_le32(rec + 0x1C);
        p->new_crc = rd_le32(rec + 0x20);
        label_off = rd_le32(rec + 0x24);
        p->label_len = rd_le32(rec + 0x28);

        if (p->old_size == 0 || p->new_size == 0 || p->slot_size == 0 ||
            p->new_size > p->slot_size || p->old_size > p->slot_size) {
            set_err(err, errcap, "patch %u has invalid sizes", i);
            goto fail;
        }
        if (p->slot_size > SLOT_MAX) {
            set_err(err, errcap, "patch %u slot is too large", i);
            goto fail;
        }
        if ((u64)p->offset + p->slot_size > plan->archive_size) {
            set_err(err, errcap, "patch %u is outside the archive", i);
            goto fail;
        }
        if (p->index >= plan->index_count) {
            set_err(err, errcap, "patch %u has an invalid index position", i);
            goto fail;
        }
        if (p->blob_offset < blob_start ||
            (u64)p->blob_offset + p->new_size > blob_start + plan->blob_bytes) {
            set_err(err, errcap, "patch %u blob is out of range", i);
            goto fail;
        }
        if (label_off < PLAN_HEADER ||
            (u64)label_off + p->label_len > (u64)PLAN_HEADER + string_bytes ||
            p->label_len > 4096) {
            set_err(err, errcap, "patch %u label is out of range", i);
            goto fail;
        }
        p->label_rel = label_off - PLAN_HEADER;
    }

    if (check_crc && check_trailer(plan, file_size, err, errcap) != 0)
        goto fail;
    plan->crc_checked = check_crc ? 1 : 0;

    return 0;

fail:
    plan_close(plan);
    return -1;
}

void plan_close(Plan *plan)
{
    if (plan->fd >= 0)
        io_close(plan->fd);
    plan->fd = -1;
    free(plan->patches);
    free(plan->meta);
    plan->patches = NULL;
    plan->meta = NULL;
}

void plan_label_copy(const Plan *plan, u32 i, char *buf, size_t cap)
{
    const PlanPatch *p;

    if (cap == 0)
        return;
    if (i >= plan->count) {
        buf[0] = '\0';
        return;
    }
    p = &plan->patches[i];
    if ((u64)p->label_rel + p->label_len > plan->meta_size) {
        buf[0] = '\0';
        return;
    }
    str_copy_n(buf, cap, (const char *)plan->meta + p->label_rel, p->label_len);
}

int plan_read_blob(const Plan *plan, u32 i, void *buf, char *err, size_t errcap)
{
    const PlanPatch *p;

    if (i >= plan->count) {
        set_err(err, errcap, "invalid patch number");
        return -1;
    }
    p = &plan->patches[i];
    if (io_read_at(plan->fd, p->blob_offset, buf, p->new_size) != 0) {
        set_err(err, errcap, "cannot read patch blob from the plan");
        return -1;
    }
    return 0;
}
