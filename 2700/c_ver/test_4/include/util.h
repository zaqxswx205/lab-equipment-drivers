#ifndef K_UTIL_H
#define K_UTIL_H
#include <windows.h>
#include "model.h"
void to_wide(const char *s,wchar_t *out,size_t cap);
void to_utf8(const wchar_t *s,char *out,size_t cap);
void win_error(char *error,size_t cap,const char *action);
void local_timestamp(char *out,size_t cap);
void unique_name(wchar_t *out,size_t cap,const wchar_t *prefix,const wchar_t *ext);
int directory_create(const wchar_t *path,char *error,size_t cap);
int path_join(wchar_t *out,size_t cap,const wchar_t *folder,const wchar_t *name);
double monotonic_seconds(void);
int stop_requested(HANDLE stop);
int wait_seconds(HANDLE stop,double seconds);
#endif
