/* Small string/error helpers. */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "common.h"

void str_copy(char *dst, size_t cap, const char *src)
{
    size_t n;

    if (cap == 0)
        return;
    if (src == NULL)
        src = "";
    n = strlen(src);
    if (n >= cap)
        n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

void str_copy_n(char *dst, size_t cap, const char *src, size_t len)
{
    if (cap == 0)
        return;
    if (src == NULL)
        len = 0;
    if (len >= cap)
        len = cap - 1;
    if (len)
        memcpy(dst, src, len);
    dst[len] = '\0';
}

int str_ends_with_ci(const char *s, const char *suffix)
{
    size_t ls = strlen(s), lf = strlen(suffix);
    size_t i;

    if (lf > ls)
        return 0;
    s += ls - lf;
    for (i = 0; i < lf; i++) {
        char a = s[i], b = suffix[i];
        if (a >= 'A' && a <= 'Z')
            a = (char)(a - 'A' + 'a');
        if (b >= 'A' && b <= 'Z')
            b = (char)(b - 'A' + 'a');
        if (a != b)
            return 0;
    }
    return 1;
}

void str_upper(char *s)
{
    for (; *s; s++) {
        if (*s >= 'a' && *s <= 'z')
            *s = (char)(*s - 'a' + 'A');
    }
}

void set_err(char *err, size_t cap, const char *fmt, ...)
{
    va_list ap;

    if (err == NULL || cap == 0)
        return;
    va_start(ap, fmt);
    vsnprintf(err, cap, fmt, ap);
    va_end(ap);
    err[cap - 1] = '\0';
}
