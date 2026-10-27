/* Thin, explicit file helpers over the lv2 syscalls (no stdio buffering). */
#ifndef PS3TEXMOD_IO_H
#define PS3TEXMOD_IO_H

#include <ppu-types.h>
#include <stddef.h>

int io_size(const char *path, u64 *size);
int io_exists(const char *path);
int io_is_dir(const char *path);
int io_open_ro(const char *path, int *fd);
int io_open_rw(const char *path, int *fd);
int io_open_create(const char *path, int *fd);
int io_open_new(const char *path, int *fd);
int io_read_at(int fd, u64 off, void *buf, size_t len);
int io_write_at(int fd, u64 off, const void *buf, size_t len);
int io_sync(int fd);
int io_close(int fd);
int io_unlink(const char *path);
int io_mkdir(const char *path);
int io_mkdir_p(const char *path);

#endif
