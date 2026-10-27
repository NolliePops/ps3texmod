/* Software framebuffer UI (CPU writes into the RSX display buffers). */
#ifndef PS3TEXMOD_UI_H
#define PS3TEXMOD_UI_H

#include <ppu-types.h>
#include <stddef.h>

#define UI_BG      0x00101820u
#define UI_PANEL   0x001A2430u
#define UI_PANEL2  0x00223040u
#define UI_TEXT    0x00E8EEF4u
#define UI_DIM     0x0090A4B8u
#define UI_ACCENT  0x00F0B040u
#define UI_OK      0x0060D080u
#define UI_BAD     0x00FF6A6Au
#define UI_SEL     0x002E4258u
#define UI_BLACK   0x00000000u

int ui_init(char *err, size_t errcap);
void ui_shutdown(void);
u32 ui_width(void);
u32 ui_height(void);
int ui_scale(void);

u32 *ui_begin(void);
void ui_end(void);

void ui_fill(u32 color);
void ui_rect(int x, int y, int w, int h, u32 color);
void ui_frame(int x, int y, int w, int h, u32 color, int thickness);

int ui_text_width(int scale, const char *text);
int ui_text_height(int scale);
void ui_text(int x, int y, int scale, u32 color, const char *text);
void ui_textn(int x, int y, int scale, u32 color, const char *text, int len);
void ui_textf(int x, int y, int scale, u32 color, const char *fmt, ...);
int ui_text_wrap(int x, int y, int max_width, int scale, u32 color,
                 const char *text);
void ui_progress(int x, int y, int w, int h, float fraction, u32 color);

#endif
