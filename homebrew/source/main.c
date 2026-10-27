/* ps3texmod console installer.
 *
 * Discovers the Prototype install, lists precomputed .ps3patch plans, verifies
 * them against the real archive, applies them in place with a rollback
 * journal, and restores journals.
 *
 * Everything format-specific happens in ps3texmod on the PC.  This program
 * only understands the small little-endian plan/journal formats and the RCF
 * header/index, so it never needs zlib, ZIP or JSON on the console.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <dirent.h>

#include <io/pad.h>
#include <sys/process.h>
#include <sysutil/sysutil.h>

#include "common.h"
#include "install.h"
#include "io.h"
#include "journal.h"
#include "plan.h"
#include "ui.h"

SYS_PROCESS_PARAM(1001, 0x100000);

#define JOURNAL_DIR "/dev_hdd0/ps3texmod/backups"

enum {
    SCR_LIST = 0,
    SCR_CONFIRM_APPLY,
    SCR_CONFIRM_RESTORE,
    SCR_MESSAGE,
    SCR_BUSY
};

typedef struct {
    char path[PATH_LEN];
    char name[NAME_LEN];
    char archive[NAME_LEN];
    u32 count;
    int valid;
    char error[ERR_LEN];
} PlanRow;

static volatile int g_running = 1;
static int g_screen = SCR_LIST;
static int g_sel;
static int g_top;
static int g_hold_up, g_hold_down;

static char g_usrdirs[MAX_USRDIRS][PATH_LEN];
static int g_usrdir_count;

static PlanRow g_rows[MAX_PLANS];
static int g_row_count;

static char g_action_plan[PATH_LEN];
static char g_action_archive[PATH_LEN];
static char g_action_name[NAME_LEN];
static u32 g_action_pending;
static u32 g_action_relocated;
static int g_action_mode = INSTALL_EXACT;

static JournalEntry g_journals[MAX_JOURNALS];
static u32 g_journal_count;
static u32 g_journal_total;

static char g_msg_title[NAME_LEN];
static char g_msg_body[2048];

static u32 g_prog_done, g_prog_total;
static const char *g_prog_stage;

static padData g_pad, g_prev;

/* ------------------------------------------------------------------------- */
/* utilities                                                                  */
/* ------------------------------------------------------------------------- */

static int scale(void)
{
    return ui_scale();
}

static int px(int v)
{
    return v * scale();
}

static void set_message(const char *title, const char *fmt, ...)
{
    va_list ap;

    str_copy(g_msg_title, sizeof(g_msg_title), title);
    va_start(ap, fmt);
    vsnprintf(g_msg_body, sizeof(g_msg_body), fmt, ap);
    va_end(ap);
    g_msg_body[sizeof(g_msg_body) - 1] = '\0';
    g_screen = SCR_MESSAGE;
}

static int path_exists_dir(const char *path)
{
    return io_is_dir(path);
}

static void add_usrdir(const char *path)
{
    int i;

    if (g_usrdir_count >= MAX_USRDIRS)
        return;
    if (!path_exists_dir(path))
        return;
    for (i = 0; i < g_usrdir_count; i++) {
        if (strcmp(g_usrdirs[i], path) == 0)
            return;
    }
    str_copy(g_usrdirs[g_usrdir_count], PATH_LEN, path);
    g_usrdir_count++;
}

static int dir_has_rcf(const char *path)
{
    DIR *d;
    struct dirent *de;
    int found = 0;

    d = opendir(path);
    if (d == NULL)
        return 0;
    while ((de = readdir(d)) != NULL) {
        if (str_ends_with_ci(de->d_name, ".rcf")) {
            found = 1;
            break;
        }
    }
    closedir(d);
    return found;
}

static void add_game_dir(const char *path)
{
    if (path_exists_dir(path) && dir_has_rcf(path))
        add_usrdir(path);
}

/* Tries every known release id below one base directory. */
static void add_known_game_dirs(const char *base)
{
    static const char *ids[] = {
        PS3TEXMOD_TITLE_ID,
        PS3TEXMOD_TITLE_ID_PSN
    };
    char path[PATH_LEN];
    int i;

    for (i = 0; i < (int)(sizeof(ids) / sizeof(ids[0])); i++) {
        snprintf(path, sizeof(path), "%s/%s/PS3_GAME/USRDIR", base, ids[i]);
        add_game_dir(path);
        snprintf(path, sizeof(path), "%s/%s/USRDIR", base, ids[i]);
        add_game_dir(path);
    }
}

/* Adds <base>/<child>/... for every child of base, using a callback name. */
static void scan_games_dir(const char *base)
{
    DIR *d;
    struct dirent *de;
    char path[PATH_LEN];

    d = opendir(base);
    if (d == NULL)
        return;
    while ((de = readdir(d)) != NULL) {
        if (de->d_name[0] == '.')
            continue;
        if (strcmp(de->d_name, "PS3_GAME") == 0)
            continue;
        snprintf(path, sizeof(path), "%s/%s/PS3_GAME/USRDIR", base, de->d_name);
        add_game_dir(path);
        snprintf(path, sizeof(path), "%s/%s/USRDIR", base, de->d_name);
        add_game_dir(path);
    }
    closedir(d);
}

static void discover_usrdirs(void)
{
    char path[PATH_LEN];
    int i;

    g_usrdir_count = 0;

    /* Package installs (PSN) and JB folders both live under
       /dev_hdd0/game/<TITLE_ID>. Scan the parent generically so an unknown
       regional id still works, then probe the known ids as a fallback in case
       the directory listing is not permitted. */
    add_known_game_dirs("/dev_hdd0/game");
    scan_games_dir("/dev_hdd0/game");
    add_known_game_dirs("/dev_hdd0/GAMES");
    scan_games_dir("/dev_hdd0/GAMES");
    add_known_game_dirs("/dev_hdd0/games");
    scan_games_dir("/dev_hdd0/games");

    for (i = 0; i < 7; i++) {
        snprintf(path, sizeof(path), "/dev_usb%03d", i);
        if (!path_exists_dir(path))
            continue;
        snprintf(path, sizeof(path), "/dev_usb%03d/PS3_GAME/USRDIR", i);
        add_game_dir(path);
        snprintf(path, sizeof(path), "/dev_usb%03d/USRDIR", i);
        add_game_dir(path);
        snprintf(path, sizeof(path), "/dev_usb%03d", i);
        add_known_game_dirs(path);
        snprintf(path, sizeof(path), "/dev_usb%03d/GAMES", i);
        scan_games_dir(path);
        snprintf(path, sizeof(path), "/dev_usb%03d/games", i);
        scan_games_dir(path);
    }
}

static int resolve_archive(const char *archive, char *out, size_t cap)
{
    char path[PATH_LEN];
    int i;

    if (archive == NULL || archive[0] == '\0')
        return 0;
    if (archive[0] == '/') {
        if (io_exists(archive)) {
            str_copy(out, cap, archive);
            return 1;
        }
        return 0;
    }
    for (i = 0; i < g_usrdir_count; i++) {
        snprintf(path, sizeof(path), "%s/%s", g_usrdirs[i], archive);
        if (io_exists(path)) {
            str_copy(out, cap, path);
            return 1;
        }
    }
    return 0;
}

static int plan_seen(const char *path)
{
    int i;

    for (i = 0; i < g_row_count; i++) {
        if (strcmp(g_rows[i].path, path) == 0)
            return 1;
    }
    return 0;
}

static void load_plan_row(const char *path)
{
    Plan plan;
    PlanRow *row;
    char err[ERR_LEN];

    if (g_row_count >= MAX_PLANS || plan_seen(path))
        return;
    row = &g_rows[g_row_count];
    memset(row, 0, sizeof(*row));
    str_copy(row->path, sizeof(row->path), path);

    if (plan_open(path, &plan, 0, err, sizeof(err)) != 0) {
        row->valid = 0;
        str_copy(row->error, sizeof(row->error), err);
        str_copy(row->name, sizeof(row->name), strrchr(path, '/') ?
                 strrchr(path, '/') + 1 : path);
    } else {
        row->valid = 1;
        row->count = plan.count;
        str_copy(row->name, sizeof(row->name), plan.name);
        str_copy(row->archive, sizeof(row->archive), plan.archive);
        if (row->name[0] == '\0')
            str_copy(row->name, sizeof(row->name), strrchr(path, '/') ?
                     strrchr(path, '/') + 1 : path);
        plan_close(&plan);
    }
    g_row_count++;
}

static void scan_plan_dir(const char *dir)
{
    DIR *d;
    struct dirent *de;
    char path[PATH_LEN];

    d = opendir(dir);
    if (d == NULL)
        return;
    while ((de = readdir(d)) != NULL) {
        if (de->d_name[0] == '.')
            continue;
        if (!str_ends_with_ci(de->d_name, ".ps3patch"))
            continue;
        snprintf(path, sizeof(path), "%s/%s", dir, de->d_name);
        load_plan_row(path);
    }
    closedir(d);
}

static void sort_plan_rows(void)
{
    int i, j;

    for (i = 1; i < g_row_count; i++) {
        PlanRow tmp = g_rows[i];
        for (j = i; j > 0; j--) {
            const char *a = g_rows[j - 1].name;
            const char *b = tmp.name;
            if (strcmp(a, b) <= 0)
                break;
            g_rows[j] = g_rows[j - 1];
        }
        g_rows[j] = tmp;
    }
}

static void scan_plans(void)
{
    static const char *fixed[] = {
        "/dev_hdd0/game/" PS3TEXMOD_TITLE_ID "/ps3texmod/plans",
        "/dev_hdd0/game/" PS3TEXMOD_TITLE_ID_PSN "/ps3texmod/plans",
        "/dev_hdd0/ps3texmod/plans"
    };
    char path[PATH_LEN];
    int i;

    g_row_count = 0;
    g_sel = 0;
    g_top = 0;

    for (i = 0; i < (int)(sizeof(fixed) / sizeof(fixed[0])); i++)
        scan_plan_dir(fixed[i]);

    for (i = 0; i < g_usrdir_count; i++) {
        snprintf(path, sizeof(path), "%s/ps3texmod/plans", g_usrdirs[i]);
        scan_plan_dir(path);

        /* .../PS3_GAME/USRDIR -> .../ps3texmod/plans and .../PS3_GAME/ps3texmod/plans */
        {
            char tmp[PATH_LEN];
            char *slash;

            str_copy(tmp, sizeof(tmp), g_usrdirs[i]);
            slash = strrchr(tmp, '/');
            if (slash != NULL) {
                *slash = '\0';
                snprintf(path, sizeof(path), "%s/ps3texmod/plans", tmp);
                scan_plan_dir(path);
                slash = strrchr(tmp, '/');
                if (slash != NULL) {
                    *slash = '\0';
                    snprintf(path, sizeof(path), "%s/ps3texmod/plans", tmp);
                    scan_plan_dir(path);
                }
            }
        }
    }

    for (i = 0; i < 7; i++) {
        snprintf(path, sizeof(path), "/dev_usb%03d/ps3texmod/plans", i);
        scan_plan_dir(path);
    }
    sort_plan_rows();
}

static void rescan_all(void)
{
    discover_usrdirs();
    scan_plans();
}

/* ------------------------------------------------------------------------- */
/* progress / drawing                                                         */
/* ------------------------------------------------------------------------- */

static void draw_header(const char *title)
{
    int s = scale();

    ui_rect(0, 0, (int)ui_width(), 30 * s, UI_PANEL);
    ui_text(px(10), px(8), 2 * s, UI_ACCENT, title);
    ui_text((int)ui_width() - px(10) - ui_text_width(s, "v" PS3TEXMOD_VERSION),
            px(12), s, UI_DIM, "v" PS3TEXMOD_VERSION);
    ui_rect(px(10), 29 * s, (int)ui_width() - px(20), s, UI_ACCENT);
}

static void draw_footer(const char *text)
{
    int s = scale();

    ui_rect(0, (int)ui_height() - 22 * s, (int)ui_width(), 22 * s, UI_PANEL);
    ui_text(px(10), (int)ui_height() - 16 * s, s, UI_DIM, text);
}

static void draw_trimmed(int x, int y, int s, u32 color, const char *text, int maxw)
{
    char buf[NAME_LEN];
    int maxc = maxw / (4 * s);
    size_t n;

    if (maxc < 4)
        maxc = 4;
    if (maxc > (int)sizeof(buf) - 1)
        maxc = (int)sizeof(buf) - 1;
    n = strlen(text);
    if ((int)n > maxc) {
        memcpy(buf, text, (size_t)(maxc - 3));
        memcpy(buf + maxc - 3, "...", 3);
        buf[maxc] = '\0';
    } else {
        str_copy(buf, sizeof(buf), text);
    }
    ui_text(x, y, s, color, buf);
}

static int list_rows(void)
{
    int s = scale();
    int rows = ((int)ui_height() - 32 * s - 24 * s) / (15 * s);

    return rows > 0 ? rows : 1;
}

static void draw_list(void)
{
    int s = scale();
    int rows = list_rows();
    int i;

    ui_begin();
    draw_header("PS3TEXMOD INSTALLER");

    if (g_row_count == 0) {
        ui_text(px(14), px(48), s, UI_TEXT, "NO PLANS FOUND.");
        ui_text(px(14), px(60), s, UI_DIM, "COPY .PS3PATCH FILES TO:");
        ui_text(px(14), px(70), s, UI_DIM, "/DEV_HDD0/PS3TEXMOD/PLANS");
        ui_text(px(14), px(80), s, UI_DIM, "OR <GAME>/PS3TEXMOD/PLANS");
        if (g_usrdir_count == 0) {
            ui_text(px(14), px(100), s, UI_BAD, "NO GAME INSTALL WITH .RCF FILES FOUND.");
        } else {
            ui_text(px(14), px(100), s, UI_OK, "GAME INSTALL(S) FOUND:");
            for (i = 0; i < g_usrdir_count && i < 3; i++)
                draw_trimmed(px(14), px(110 + i * 9), s, UI_DIM,
                             g_usrdirs[i], (int)ui_width() - px(28));
        }
    } else {
        if (g_sel < g_top)
            g_top = g_sel;
        if (g_sel >= g_top + rows)
            g_top = g_sel - rows + 1;

        for (i = 0; i < rows && g_top + i < g_row_count; i++) {
            int idx = g_top + i;
            PlanRow *row = &g_rows[idx];
            int y = 34 * s + i * 15 * s;

            if (idx == g_sel) {
                ui_rect(px(6), y - s, (int)ui_width() - px(12), 14 * s, UI_SEL);
                ui_rect(px(6), y - s, 3 * s, 14 * s, UI_ACCENT);
            }
            if (!row->valid) {
                ui_text(px(13), y, s, UI_BAD, "INVALID PLAN");
                draw_trimmed(px(13), y + 7 * s, s, UI_DIM, row->path,
                             (int)ui_width() - px(26));
            } else {
                draw_trimmed(px(13), y, s, UI_TEXT, row->name,
                             (int)ui_width() - px(26));
                ui_textf(px(13), y + 7 * s, s, UI_DIM, "%s  %u PATCH(ES)",
                         row->archive, row->count);
            }
        }
        ui_textf((int)ui_width() - px(10) - ui_text_width(s, "000/000"),
                 px(19), s, UI_DIM, "%d/%d",
                 g_row_count ? g_sel + 1 : 0, g_row_count);
    }

    draw_footer("X APPLY  /\\ VERIFY  [] RESTORE  L1 RE-ANCHOR  SELECT RESCAN  START EXIT");
    ui_end();
}

static void draw_message(void)
{
    int s = scale();

    ui_begin();
    draw_header(g_msg_title[0] ? g_msg_title : "RESULT");
    ui_text_wrap(px(14), px(42), (int)ui_width() - px(28), s, UI_TEXT, g_msg_body);
    draw_footer("O / X  BACK");
    ui_end();
}

static void draw_confirm_apply(void)
{
    int s = scale();
    char body[1400];

    if (g_action_mode == INSTALL_RELOCATE) {
        snprintf(body, sizeof(body),
                 "RE-ANCHORED APPLY: %u PATCH(ES)\n%s\n\nPLAN: %s\n\n"
                 "THIS PLAN WAS MADE FOR ANOTHER LAYOUT OF THIS ARCHIVE. EACH "
                 "TEXTURE WAS FOUND BY ENTRY HASH AND ITS CURRENT BYTES WERE "
                 "CHECKED AGAINST THE PLAN BEFORE THIS SCREEN; %u TARGET(S) SIT "
                 "AT A DIFFERENT OFFSET AND WILL BE PATCHED THERE. ANY MISMATCH "
                 "REFUSES THE WHOLE APPLY.\n\n"
                 "A ROLLBACK JOURNAL IS WRITTEN TO:\n" JOURNAL_DIR "\n\n"
                 "USE THIS ONLY ON A JAILBROKEN / CFW INSTALL YOU CAN REINSTALL.",
                 g_action_pending, g_action_archive, g_action_name,
                 g_action_relocated);
        ui_begin();
        draw_header("CONFIRM RE-ANCHORED APPLY");
    } else {
        snprintf(body, sizeof(body),
                 "APPLY %u PATCH(ES) TO:\n%s\n\nPLAN: %s\n\n"
                 "THIS WRITES INTO THE GAME INSTALL. A ROLLBACK JOURNAL IS "
                 "WRITTEN TO:\n" JOURNAL_DIR "\n\n"
                 "USE THIS ONLY ON A JAILBROKEN / CFW INSTALL YOU CAN REINSTALL.",
                 g_action_pending, g_action_archive, g_action_name);
        ui_begin();
        draw_header("CONFIRM APPLY");
    }

    ui_text_wrap(px(14), px(40), (int)ui_width() - px(28), s, UI_TEXT, body);
    draw_footer("X  APPLY     O  CANCEL");
    ui_end();
}

static void draw_confirm_restore(void)
{
    int s = scale();
    char body[1024];

    snprintf(body, sizeof(body),
             "RESTORE %u JOURNAL FILE(S) FOR:\n%s\n\n"
             "%u JOURNAL FILE(S) FOUND. NEWEST IS RESTORED FIRST.\n\n"
             "THE ARCHIVE IS PUT BACK TO THE BYTES RECORDED BEFORE EACH "
             "APPLY, THEN THE JOURNAL IS DELETED.",
             g_journal_count, g_action_archive, g_journal_total);

    ui_begin();
    draw_header("CONFIRM RESTORE");
    ui_text_wrap(px(14), px(40), (int)ui_width() - px(28), s, UI_TEXT, body);
    draw_footer("X  RESTORE   O  CANCEL");
    ui_end();
}

static void draw_busy(void)
{
    int s = scale();
    float frac = 0.0f;
    char line[256];

    if (g_prog_total)
        frac = (float)g_prog_done / (float)g_prog_total;
    if (g_prog_stage == NULL)
        g_prog_stage = "WORKING";

    ui_begin();
    draw_header("WORKING");
    ui_text(px(16), px(54), s, UI_TEXT, g_prog_stage);
    ui_progress(px(16), px(100), (int)ui_width() - px(32), px(18), frac, UI_ACCENT);
    snprintf(line, sizeof(line), "%u / %u", g_prog_done, g_prog_total);
    ui_text(px(16), px(126), s, UI_DIM, line);
    ui_textf(px(16), px(136), s, UI_ACCENT, "%d%%", (int)(frac * 100.0f));
    ui_text(px(16), px(160), s, UI_DIM,
            "DO NOT POWER OFF THE CONSOLE.");
    ui_end();
}

static void progress_cb(void *user, u32 done, u32 total, const char *stage)
{
    (void)user;
    g_prog_done = done;
    g_prog_total = total;
    g_prog_stage = stage;
    draw_busy();
}

/* ------------------------------------------------------------------------- */
/* actions                                                                    */
/* ------------------------------------------------------------------------- */

static void action_verify(int apply_when_pending, int mode)
{
    Plan plan;
    VerifyResult vr;
    char err[ERR_LEN];
    char archive[PATH_LEN];
    char body[1024];

    if (g_sel < 0 || g_sel >= g_row_count)
        return;
    if (!g_rows[g_sel].valid) {
        set_message("INVALID PLAN", "%s", g_rows[g_sel].error);
        return;
    }
    str_copy(g_action_plan, sizeof(g_action_plan), g_rows[g_sel].path);
    str_copy(g_action_name, sizeof(g_action_name), g_rows[g_sel].name);

    if (plan_open(g_action_plan, &plan, 1, err, sizeof(err)) != 0) {
        set_message("PLAN ERROR", "%s", err);
        return;
    }
    if (!resolve_archive(plan.archive, archive, sizeof(archive))) {
        char target[NAME_LEN];

        str_copy(target, sizeof(target), plan.archive);
        plan_close(&plan);
        set_message("ARCHIVE NOT FOUND",
                    "THE PLAN TARGETS %s, BUT NO DISCOVERED GAME INSTALL "
                    "CONTAINS THAT FILE.\n\nCHECK THE GAME IS INSTALLED AS A "
                    "JB FOLDER OR PACKAGE INSTALL (USRDIR).", target);
        return;
    }
    str_copy(g_action_archive, sizeof(g_action_archive), archive);

    g_prog_done = 0;
    g_prog_total = 0;
    g_prog_stage = mode == INSTALL_RELOCATE ? "RE-ANCHORING" : "VERIFYING";
    if (install_verify_mode(&plan, archive, &vr, progress_cb, NULL, mode,
                            err, sizeof(err)) != 0) {
        plan_close(&plan);
        if (mode == INSTALL_EXACT) {
            set_message("VERIFY FAILED",
                        "%s\n\nIF THIS IS A DIFFERENT BUILD OF THE SAME GAME "
                        "(DIFFERENT ARCHIVE SIZE), PRESS L1 IN THE LIST TO "
                        "RE-ANCHOR. IT STILL CHECKS EVERY TEXTURE'S CRC AND "
                        "REFUSES ANYTHING THAT DOES NOT MATCH.", err);
        } else {
            set_message("RE-ANCHOR REFUSED", "%s", err);
        }
        return;
    }
    plan_close(&plan);

    if (vr.pending == 0) {
        set_message("NOTHING TO DO",
                    "%u PATCH(ES) ARE ALREADY APPLIED.\n\n%s",
                    vr.already, g_action_archive);
        return;
    }
    if (!apply_when_pending) {
        snprintf(body, sizeof(body),
                 "VERIFY OK.\n\n%u PENDING, %u ALREADY APPLIED.\n\n%s",
                 vr.pending, vr.already, g_action_archive);
        set_message("VERIFY OK", "%s", body);
        return;
    }

    g_action_pending = vr.pending;
    g_action_relocated = vr.relocated;
    g_action_mode = mode;
    g_screen = SCR_CONFIRM_APPLY;
}

static void action_apply(void)
{
    Plan plan;
    ApplyResult ar;
    char err[ERR_LEN];

    if (plan_open(g_action_plan, &plan, 1, err, sizeof(err)) != 0) {
        set_message("PLAN ERROR", "%s", err);
        return;
    }
    g_prog_done = 0;
    g_prog_total = 0;
    g_prog_stage = g_action_mode == INSTALL_RELOCATE ? "RE-ANCHORED APPLY" : "APPLYING";

    if (install_apply_mode(&plan, g_action_archive, JOURNAL_DIR, &ar,
                           progress_cb, NULL, g_action_mode, err, sizeof(err)) != 0) {
        plan_close(&plan);
        set_message("APPLY FAILED",
                    "%s\n\nIF A JOURNAL WAS CREATED, USE RESTORE TO ROLL BACK:\n%s",
                    err, ar.journal[0] ? ar.journal : "(NO JOURNAL)");
        return;
    }
    plan_close(&plan);

    if (ar.applied == 0) {
        set_message("NOTHING TO DO", "%u PATCH(ES) WERE ALREADY APPLIED.\n\n%s",
                    ar.already, g_action_archive);
    } else {
        set_message("APPLIED",
                    "%u PATCH(ES) WRITTEN (%llu BYTES).\n\n%s\n\nJOURNAL:\n%s",
                    ar.applied, (unsigned long long)ar.bytes,
                    g_action_archive, ar.journal);
    }
}

static void action_scan_journals(void)
{
    Plan plan;
    char err[ERR_LEN];
    char archive[PATH_LEN];
    u32 total = 0;
    int n;

    if (g_sel < 0 || g_sel >= g_row_count)
        return;
    if (!g_rows[g_sel].valid) {
        set_message("INVALID PLAN", "%s", g_rows[g_sel].error);
        return;
    }
    if (plan_open(g_rows[g_sel].path, &plan, 0, err, sizeof(err)) != 0) {
        set_message("PLAN ERROR", "%s", err);
        return;
    }
    if (!resolve_archive(plan.archive, archive, sizeof(archive))) {
        set_message("ARCHIVE NOT FOUND", "NO INSTALL CONTAINS %s", plan.archive);
        plan_close(&plan);
        return;
    }
    str_copy(g_action_archive, sizeof(g_action_archive), archive);
    plan_close(&plan);

    n = journal_scan(JOURNAL_DIR, g_rows[g_sel].archive,
                     g_journals, MAX_JOURNALS, &total, err, sizeof(err));
    if (n < 0) {
        set_message("JOURNAL ERROR", "%s", err);
        return;
    }
    if (n == 0) {
        set_message("NO JOURNALS",
                    "NO JOURNAL FOR %s WAS FOUND IN\n" JOURNAL_DIR,
                    g_rows[g_sel].archive);
        return;
    }
    g_journal_count = (u32)n;
    g_journal_total = total;
    g_screen = SCR_CONFIRM_RESTORE;
}

static void action_restore(void)
{
    char err[ERR_LEN];
    u32 done = 0, i;

    for (i = 0; i < g_journal_count; i++) {
        g_prog_done = i;
        g_prog_total = g_journal_count;
        g_prog_stage = "RESTORING";

        if (journal_restore(&g_journals[i], g_action_archive,
                            progress_cb, NULL, err, sizeof(err)) != 0) {
            set_message("RESTORE STOPPED",
                        "RESTORED %u OF %u JOURNAL FILE(S), THEN FAILED:\n%s\n\n"
                        "REMAINING JOURNAL FILES WERE KEPT.",
                        done, g_journal_count, err);
            g_journal_count = 0;
            return;
        }
        done++;
    }
    g_journal_count = 0;
    set_message("RESTORED",
                "%u JOURNAL FILE(S) RESTORED AND DELETED.\n\n%s",
                done, g_action_archive);
}

/* ------------------------------------------------------------------------- */
/* input                                                                      */
/* ------------------------------------------------------------------------- */

static int edge_cross(void) { return g_pad.BTN_CROSS && !g_prev.BTN_CROSS; }
static int edge_circle(void) { return g_pad.BTN_CIRCLE && !g_prev.BTN_CIRCLE; }
static int edge_square(void) { return g_pad.BTN_SQUARE && !g_prev.BTN_SQUARE; }
static int edge_triangle(void) { return g_pad.BTN_TRIANGLE && !g_prev.BTN_TRIANGLE; }
static int edge_l1(void) { return g_pad.BTN_L1 && !g_prev.BTN_L1; }
static int edge_select(void) { return g_pad.BTN_SELECT && !g_prev.BTN_SELECT; }
static int edge_start(void) { return g_pad.BTN_START && !g_prev.BTN_START; }

static int nav_up(void)
{
    if (g_pad.BTN_UP && !g_prev.BTN_UP)
        return 1;
    if (g_pad.BTN_UP) {
        g_hold_up++;
        if (g_hold_up >= 20 && (g_hold_up - 20) % 5 == 0)
            return 1;
    } else {
        g_hold_up = 0;
    }
    return 0;
}

static int nav_down(void)
{
    if (g_pad.BTN_DOWN && !g_prev.BTN_DOWN)
        return 1;
    if (g_pad.BTN_DOWN) {
        g_hold_down++;
        if (g_hold_down >= 20 && (g_hold_down - 20) % 5 == 0)
            return 1;
    } else {
        g_hold_down = 0;
    }
    return 0;
}

static void poll_pad(void)
{
    padInfo info;

    memset(&g_pad, 0, sizeof(g_pad));
    if (ioPadGetInfo(&info) == 0 && info.status[0])
        ioPadGetData(0, &g_pad);
}

static void handle_input(void)
{
    switch (g_screen) {
    case SCR_LIST:
        if (nav_up() && g_row_count) {
            g_sel--;
            if (g_sel < 0)
                g_sel = g_row_count - 1;
        }
        if (nav_down() && g_row_count) {
            g_sel++;
            if (g_sel >= g_row_count)
                g_sel = 0;
        }
        if (edge_cross())
            action_verify(1, INSTALL_EXACT);
        else if (edge_triangle())
            action_verify(0, INSTALL_EXACT);
        else if (edge_l1())
            action_verify(1, INSTALL_RELOCATE);
        else if (edge_square())
            action_scan_journals();
        else if (edge_select())
            rescan_all();
        else if (edge_start())
            g_running = 0;
        break;

    case SCR_CONFIRM_APPLY:
        if (edge_cross())
            action_apply();
        else if (edge_circle())
            g_screen = SCR_LIST;
        break;

    case SCR_CONFIRM_RESTORE:
        if (edge_cross())
            action_restore();
        else if (edge_circle())
            g_screen = SCR_LIST;
        break;

    case SCR_MESSAGE:
        if (edge_circle() || edge_cross())
            g_screen = SCR_LIST;
        break;

    default:
        break;
    }
    g_prev = g_pad;
}

/* ------------------------------------------------------------------------- */
/* entry point                                                                */
/* ------------------------------------------------------------------------- */

static void sysutil_cb(u64 status, u64 param, void *usrdata)
{
    (void)param;
    (void)usrdata;
    if (status == SYSUTIL_EXIT_GAME)
        g_running = 0;
}

int main(int argc, const char *argv[])
{
    char err[ERR_LEN];

    (void)argc;
    (void)argv;

    sysUtilRegisterCallback(SYSUTIL_EVENT_SLOT0, sysutil_cb, NULL);
    if (ioPadInit(7) != 0) {
        printf("ps3texmod: ioPadInit failed\n");
        return 1;
    }
    if (ui_init(err, sizeof(err)) != 0) {
        printf("ps3texmod: %s\n", err);
        ioPadEnd();
        return 1;
    }

    rescan_all();

    while (g_running) {
        sysUtilCheckCallback();
        poll_pad();
        handle_input();
        if (!g_running)
            break;

        switch (g_screen) {
        case SCR_LIST:
            draw_list();
            break;
        case SCR_CONFIRM_APPLY:
            draw_confirm_apply();
            break;
        case SCR_CONFIRM_RESTORE:
            draw_confirm_restore();
            break;
        case SCR_MESSAGE:
            draw_message();
            break;
        case SCR_BUSY:
            draw_busy();
            break;
        default:
            g_screen = SCR_LIST;
            break;
        }
        usleep(16000);
    }

    ui_shutdown();
    ioPadEnd();
    return 0;
}
