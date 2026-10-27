/* Software framebuffer UI.
 *
 * PSL1GHT's RSX guide documents writing pixel data directly into the display
 * color buffer; this file only uses that path (no Cg shaders, no RSX draws).
 */
#include <malloc.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include <rsx/rsx.h>
#include <sysutil/video.h>

#include "common.h"
#include "font3x5.h"
#include "ui.h"

#define CB_SIZE 0x100000u
#define HOST_SIZE (32u * 1024u * 1024u)
#define FB_COUNT 2
#define LABEL_INDEX 255

static gcmContextData *ctx;
static u32 width, height, pitch;
static u32 color_offset[FB_COUNT];
static u32 *color_buffer[FB_COUNT];
static u32 depth_offset, depth_pitch;
static u32 *depth_buffer;
static u32 draw_fb;
static int first_frame = 1;
static u32 *back;
static int ui_px_scale = 1;
static u32 label_value = 1;

static void wait_finish(void)
{
    rsxSetWriteBackendLabel(ctx, LABEL_INDEX, label_value);
    rsxFlushBuffer(ctx);
    while (*(vu32 *)gcmGetLabelAddress(LABEL_INDEX) != label_value)
        usleep(30);
    label_value++;
}

static void wait_rsx_idle(void)
{
    rsxSetWriteBackendLabel(ctx, LABEL_INDEX, label_value);
    rsxSetWaitLabel(ctx, LABEL_INDEX, label_value);
    label_value++;
    wait_finish();
}

static void set_render_target(u32 index)
{
    gcmSurface sf;

    memset(&sf, 0, sizeof(sf));
    sf.colorFormat = GCM_SURFACE_X8R8G8B8;
    sf.colorTarget = GCM_SURFACE_TARGET_0;
    sf.colorLocation[0] = GCM_LOCATION_RSX;
    sf.colorOffset[0] = color_offset[index];
    sf.colorPitch[0] = pitch;
    sf.colorLocation[1] = GCM_LOCATION_RSX;
    sf.colorLocation[2] = GCM_LOCATION_RSX;
    sf.colorLocation[3] = GCM_LOCATION_RSX;
    sf.colorPitch[1] = 64;
    sf.colorPitch[2] = 64;
    sf.colorPitch[3] = 64;
    sf.depthFormat = GCM_SURFACE_ZETA_Z16;
    sf.depthLocation = GCM_LOCATION_RSX;
    sf.depthOffset = depth_offset;
    sf.depthPitch = depth_pitch;
    sf.type = GCM_SURFACE_TYPE_LINEAR;
    sf.antiAlias = GCM_SURFACE_CENTER_1;
    sf.width = (u16)width;
    sf.height = (u16)height;
    sf.x = 0;
    sf.y = 0;
    rsxSetSurface(ctx, &sf);
}

int ui_init(char *err, size_t errcap)
{
    videoState state;
    videoResolution res;
    videoConfiguration vconfig;
    void *host;
    int i;

    host = memalign(1024 * 1024, HOST_SIZE);
    if (host == NULL) {
        set_err(err, errcap, "out of memory for the RSX host buffer");
        return -1;
    }
    if (rsxInit(&ctx, CB_SIZE, HOST_SIZE, host) != 0) {
        set_err(err, errcap, "rsxInit failed");
        return -1;
    }
    if (videoGetState(0, 0, &state) != 0 ||
        videoGetResolution(state.displayMode.resolution, &res) != 0) {
        set_err(err, errcap, "cannot query the video mode");
        return -1;
    }
    memset(&vconfig, 0, sizeof(vconfig));
    vconfig.resolution = state.displayMode.resolution;
    vconfig.format = VIDEO_BUFFER_FORMAT_XRGB;
    vconfig.pitch = res.width * sizeof(u32);
    wait_rsx_idle();
    if (videoConfigure(0, &vconfig, NULL, 0) != 0) {
        set_err(err, errcap, "videoConfigure failed");
        return -1;
    }
    gcmSetFlipMode(GCM_FLIP_VSYNC);

    width = res.width;
    height = res.height;
    pitch = width * sizeof(u32);
    ui_px_scale = (int)(height / 240);
    if (ui_px_scale < 1)
        ui_px_scale = 1;
    if (ui_px_scale > 5)
        ui_px_scale = 5;

    for (i = 0; i < FB_COUNT; i++) {
        color_buffer[i] = (u32 *)rsxMemalign(64, height * pitch);
        if (color_buffer[i] == NULL) {
            set_err(err, errcap, "out of memory for display buffers");
            return -1;
        }
        if (rsxAddressToOffset(color_buffer[i], &color_offset[i]) != 0) {
            set_err(err, errcap, "cannot map a display buffer");
            return -1;
        }
        memset(color_buffer[i], 0, height * pitch);
        gcmSetDisplayBuffer(i, color_offset[i], pitch, width, height);
    }
    depth_pitch = pitch;
    depth_buffer = (u32 *)rsxMemalign(64, height * depth_pitch);
    if (depth_buffer == NULL) {
        set_err(err, errcap, "out of memory for the depth buffer");
        return -1;
    }
    if (rsxAddressToOffset(depth_buffer, &depth_offset) != 0) {
        set_err(err, errcap, "cannot map the depth buffer");
        return -1;
    }
    memset(depth_buffer, 0, height * depth_pitch);

    gcmResetFlipStatus();
    set_render_target(0);
    draw_fb = 0;
    first_frame = 1;
    back = color_buffer[0];
    return 0;
}

void ui_shutdown(void)
{
    if (ctx == NULL)
        return;
    gcmSetWaitFlip(ctx);
    rsxFinish(ctx, 1);
    ctx = NULL;
}

u32 ui_width(void) { return width; }
u32 ui_height(void) { return height; }
int ui_scale(void) { return ui_px_scale; }

u32 *ui_begin(void)
{
    back = color_buffer[draw_fb];
    ui_fill(UI_BG);
    return back;
}

void ui_end(void)
{
    if (!first_frame) {
        while (gcmGetFlipStatus() != 0)
            usleep(200);
        gcmResetFlipStatus();
    } else {
        gcmResetFlipStatus();
    }

    gcmSetFlip(ctx, draw_fb);
    rsxFlushBuffer(ctx);
    gcmSetWaitFlip(ctx);

    draw_fb ^= 1;
    set_render_target(draw_fb);
    first_frame = 0;
}

void ui_rect(int x, int y, int w, int h, u32 color)
{
    int row;

    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > (int)width) w = (int)width - x;
    if (y + h > (int)height) h = (int)height - y;
    if (w <= 0 || h <= 0)
        return;
    for (row = 0; row < h; row++) {
        u32 *p = back + (size_t)(y + row) * (pitch / 4) + x;
        int col;

        for (col = 0; col < w; col++)
            p[col] = color;
    }
}

void ui_fill(u32 color)
{
    ui_rect(0, 0, (int)width, (int)height, color);
}

void ui_frame(int x, int y, int w, int h, u32 color, int t)
{
    ui_rect(x, y, w, t, color);
    ui_rect(x, y + h - t, w, t, color);
    ui_rect(x, y, t, h, color);
    ui_rect(x + w - t, y, t, h, color);
}

static const unsigned char *glyph_for(char c)
{
    if (c >= 'a' && c <= 'z')
        c = (char)(c - 'a' + 'A');
    if (c < FONT3X5_FIRST || c > FONT3X5_LAST)
        c = '?';
    return font3x5[(int)c - FONT3X5_FIRST];
}

int ui_text_width(int scale, const char *text)
{
    int n = text ? (int)strlen(text) : 0;

    return n > 0 ? n * 4 * scale - scale : 0;
}

int ui_text_height(int scale)
{
    return FONT3X5_H * scale;
}

void ui_textn(int x, int y, int scale, u32 color, const char *text, int len)
{
    int i;
    int start_x = x;

    for (i = 0; i < len; i++) {
        const unsigned char *g;
        int row, col;

        if (text[i] == '\n') {
            y += (FONT3X5_H + 1) * scale;
            x = start_x;
            continue;
        }
        g = glyph_for(text[i]);
        for (row = 0; row < FONT3X5_H; row++) {
            for (col = 0; col < FONT3X5_W; col++) {
                if (g[row] & (1u << (FONT3X5_W - 1 - col)))
                    ui_rect(x + col * scale, y + row * scale, scale, scale, color);
            }
        }
        x += (FONT3X5_W + 1) * scale;
    }
}

void ui_text(int x, int y, int scale, u32 color, const char *text)
{
    if (text)
        ui_textn(x, y, scale, color, text, (int)strlen(text));
}

void ui_textf(int x, int y, int scale, u32 color, const char *fmt, ...)
{
    char buf[1024];
    va_list ap;

    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    buf[sizeof(buf) - 1] = '\0';
    ui_text(x, y, scale, color, buf);
}

int ui_text_wrap(int x, int y, int max_width, int scale, u32 color,
                 const char *text)
{
    char line[256];
    int cols = max_width / (4 * scale);
    int line_len = 0;
    const char *p = text;
    int line_h = (FONT3X5_H + 3) * scale;

    if (cols < 8)
        cols = 8;
    if (cols > (int)sizeof(line) - 1)
        cols = (int)sizeof(line) - 1;

    while (p && *p) {
        const char *q;
        int wlen;

        if (*p == '\n') {
            if (line_len) {
                ui_textn(x, y, scale, color, line, line_len);
                y += line_h;
                line_len = 0;
            }
            p++;
            continue;
        }
        while (*p == ' ')
            p++;
        if (!*p)
            break;
        q = p;
        while (*q && *q != ' ' && *q != '\n')
            q++;
        wlen = (int)(q - p);
        if (wlen > cols) {
            if (line_len) {
                ui_textn(x, y, scale, color, line, line_len);
                y += line_h;
                line_len = 0;
            }
            while (wlen > cols) {
                ui_textn(x, y, scale, color, p, cols);
                y += line_h;
                p += cols;
                wlen -= cols;
            }
            continue;
        }
        if (line_len + (line_len ? 1 : 0) + wlen > cols) {
            ui_textn(x, y, scale, color, line, line_len);
            y += line_h;
            line_len = 0;
        }
        if (line_len)
            line[line_len++] = ' ';
        memcpy(line + line_len, p, (size_t)wlen);
        line_len += wlen;
        p = q;
        while (*p == ' ')
            p++;
    }
    if (line_len) {
        ui_textn(x, y, scale, color, line, line_len);
        y += line_h;
    }
    return y;
}

void ui_progress(int x, int y, int w, int h, float fraction, u32 color)
{
    int fill;

    if (fraction < 0.0f)
        fraction = 0.0f;
    if (fraction > 1.0f)
        fraction = 1.0f;
    ui_rect(x, y, w, h, UI_PANEL2);
    fill = (int)((w - 4) * fraction);
    if (fill > 0)
        ui_rect(x + 2, y + 2, fill, h - 4, color);
    ui_frame(x, y, w, h, UI_DIM, 2);
}
