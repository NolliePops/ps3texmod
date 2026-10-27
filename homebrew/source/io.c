/* lv2 file helpers. */
#include <dirent.h>
#include <string.h>

#include <sys/file.h>

#include "common.h"
#include "io.h"

static int seek_set(int fd, u64 off)
{
    u64 pos = 0;

    return sysLv2FsLSeek64(fd, off, 0 /* SEEK_SET */, &pos);
}

int io_size(const char *path, u64 *size)
{
    sysFSStat st;

    if (sysLv2FsStat(path, &st) != 0)
        return -1;
    *size = st.st_size;
    return 0;
}

int io_exists(const char *path)
{
    sysFSStat st;

    return sysLv2FsStat(path, &st) == 0;
}

int io_is_dir(const char *path)
{
    DIR *d = opendir(path);

    if (d == NULL)
        return 0;
    closedir(d);
    return 1;
}

static int open_flags(const char *path, int flags, int mode, int *fd)
{
    s32 out = -1;

    if (sysLv2FsOpen(path, flags, &out, mode, NULL, 0) != 0 || out < 0)
        return -1;
    if (flags & SYS_O_CREAT)
        sysLv2FsChmod(path, mode);
    *fd = out;
    return 0;
}

int io_open_ro(const char *path, int *fd)
{
    return open_flags(path, SYS_O_RDONLY, 0, fd);
}

int io_open_rw(const char *path, int *fd)
{
    return open_flags(path, SYS_O_RDWR, 0, fd);
}

int io_open_create(const char *path, int *fd)
{
    return open_flags(path, SYS_O_RDWR | SYS_O_CREAT | SYS_O_TRUNC, 0666, fd);
}

int io_open_new(const char *path, int *fd)
{
    return open_flags(path, SYS_O_RDWR | SYS_O_CREAT | SYS_O_EXCL, 0666, fd);
}

int io_read_at(int fd, u64 off, void *buf, size_t len)
{
    u8 *p = (u8 *)buf;
    size_t left = len;

    if (seek_set(fd, off) != 0)
        return -1;
    while (left > 0) {
        u64 got = 0;

        if (sysLv2FsRead(fd, p, left, &got) != 0)
            return -1;
        if (got == 0)
            return -1;
        p += got;
        left -= (size_t)got;
    }
    return 0;
}

int io_write_at(int fd, u64 off, const void *buf, size_t len)
{
    const u8 *p = (const u8 *)buf;
    size_t left = len;

    if (seek_set(fd, off) != 0)
        return -1;
    while (left > 0) {
        u64 wrote = 0;

        if (sysLv2FsWrite(fd, p, left, &wrote) != 0)
            return -1;
        if (wrote == 0)
            return -1;
        p += wrote;
        left -= (size_t)wrote;
    }
    return 0;
}

int io_sync(int fd)
{
    return sysLv2FsFsync(fd) == 0 ? 0 : -1;
}

int io_close(int fd)
{
    return sysLv2FsClose(fd) == 0 ? 0 : -1;
}

int io_unlink(const char *path)
{
    return sysLv2FsUnlink(path) == 0 ? 0 : -1;
}

int io_mkdir(const char *path)
{
    return sysLv2FsMkdir(path, 0777) == 0 ? 0 : -1;
}

int io_mkdir_p(const char *path)
{
    char tmp[PATH_LEN];
    size_t i, n;

    str_copy(tmp, sizeof(tmp), path);
    n = strlen(tmp);
    for (i = 1; i < n; i++) {
        if (tmp[i] == '/') {
            tmp[i] = '\0';
            if (!io_exists(tmp))
                io_mkdir(tmp);
            tmp[i] = '/';
        }
    }
    if (!io_exists(tmp))
        io_mkdir(tmp);
    return io_is_dir(tmp) ? 0 : -1;
}
