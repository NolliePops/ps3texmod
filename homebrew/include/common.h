/* Shared definitions for the ps3texmod console installer. */
#ifndef PS3TEXMOD_COMMON_H
#define PS3TEXMOD_COMMON_H

#include <ppu-types.h>
#include <stddef.h>
#include <string.h>

#define PS3TEXMOD_VERSION "1.0.0"

/* Search hints, not a trust check. Prototype shipped as BLUS30145 (US disc)
   and NPUB30389 (US PSN download); other regional ids are found by scanning
   /dev_hdd0/game. A plan is validated by archive size, index layout, slot
   offset and CRCs, so the title id can never make a wrong archive pass. */
#define PS3TEXMOD_TITLE_ID     "BLUS30145"
#define PS3TEXMOD_TITLE_ID_PSN "NPUB30389"

/* Small fixed limits keep the console code allocation-friendly. */
#define PATH_LEN 512
#define NAME_LEN 256
#define ERR_LEN 256
#define MAX_PLANS 64
#define MAX_USRDIRS 16
#define MAX_JOURNALS 64

/* Plan limits (must match ps3texmod/plan.py). */
#define PLAN_MAGIC "PS3TPAT1"
#define PLAN_TRAILER "PS3TPND1"
#define PLAN_VERSION 1u
#define PLAN_HEADER 64u
#define PLAN_RECORD 48u
#define PLAN_FLAG_INPLACE 1u
#define PLAN_FILE_MAX (512ull * 1024ull * 1024ull)
#define PLAN_META_MAX (8u * 1024u * 1024u)
#define PLAN_COUNT_MAX 100000u

/* Journal limits. */
#define JOURNAL_MAGIC "PS3TJRN1"
#define JOURNAL_TRAILER "PS3TJRE1"
#define JOURNAL_VERSION 1u
#define JOURNAL_HEADER 36u
#define JOURNAL_RECORD 28u
#define JOURNAL_RECORDS_MAX 100000u
#define JOURNAL_FILE_MAX (512ull * 1024ull * 1024ull)
#define SLOT_MAX (64ull * 1024ull * 1024ull)

static inline u32 rd_le32(const u8 *p)
{
    return (u32)p[0] | ((u32)p[1] << 8) | ((u32)p[2] << 16) | ((u32)p[3] << 24);
}

static inline u64 rd_le64(const u8 *p)
{
    return (u64)rd_le32(p) | ((u64)rd_le32(p + 4) << 32);
}

static inline u32 rd_be32(const u8 *p)
{
    return ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | (u32)p[3];
}

static inline void wr_le32(u8 *p, u32 v)
{
    p[0] = (u8)v;
    p[1] = (u8)(v >> 8);
    p[2] = (u8)(v >> 16);
    p[3] = (u8)(v >> 24);
}

static inline void wr_le64(u8 *p, u64 v)
{
    wr_le32(p, (u32)v);
    wr_le32(p + 4, (u32)(v >> 32));
}

static inline void wr_be32(u8 *p, u32 v)
{
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}

/* Copies at most cap-1 bytes and always terminates. */
void str_copy(char *dst, size_t cap, const char *src);
/* Copies a length-delimited name from the plan file. */
void str_copy_n(char *dst, size_t cap, const char *src, size_t len);
int str_ends_with_ci(const char *s, const char *suffix);
void str_upper(char *s);
void set_err(char *err, size_t cap, const char *fmt, ...);

#endif
