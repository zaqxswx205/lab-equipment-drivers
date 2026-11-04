#ifndef K_STORAGE_H
#define K_STORAGE_H
#include "util.h"
typedef struct { HANDLE handle; } DataFile;
int data_open(DataFile *f,const wchar_t *path,char *error,size_t cap);
int data_append(DataFile *f,const Sample *sample,char *error,size_t cap);
void data_close(DataFile *f);
int csv_convert(const wchar_t *source,const wchar_t *destination,HANDLE stop,uint64_t *count,char *error,size_t cap);
#endif
