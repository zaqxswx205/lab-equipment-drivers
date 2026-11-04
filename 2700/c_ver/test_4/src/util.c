#include "util.h"
#include <stdio.h>
#include <shlobj.h>
void to_wide(const char *s,wchar_t *out,size_t cap) {
    if(!MultiByteToWideChar(CP_UTF8,0,s,-1,out,(int)cap)) out[0]=0;
}
void to_utf8(const wchar_t *s,char *out,size_t cap) {
    if(!WideCharToMultiByte(CP_UTF8,0,s,-1,out,(int)cap,NULL,NULL)) out[0]=0;
}
void win_error(char *e,size_t cap,const char *action) {
    DWORD code=GetLastError(); wchar_t w[512]; char u[1600];
    if(!FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM|FORMAT_MESSAGE_IGNORE_INSERTS,NULL,code,0,w,512,NULL)) w[0]=0;
    to_utf8(w,u,sizeof(u)); snprintf(e,cap,"%s（Windows %lu）：%s",action,code,u);
}
void local_timestamp(char *out,size_t cap) {
    SYSTEMTIME t; GetLocalTime(&t);
    snprintf(out,cap,"%04u-%02u-%02uT%02u:%02u:%02u.%03u",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,t.wMilliseconds);
}
void unique_name(wchar_t *out,size_t cap,const wchar_t *prefix,const wchar_t *ext) {
    SYSTEMTIME t; GetLocalTime(&t);
    _snwprintf_s(out,cap,_TRUNCATE,L"%s_%04u%02u%02u_%02u%02u%02u_%03u_%lu%s",prefix,t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,t.wMilliseconds,GetCurrentProcessId(),ext);
}
int directory_create(const wchar_t *path,char *e,size_t cap) {
    DWORD attr=GetFileAttributesW(path); int result;
    if(attr!=INVALID_FILE_ATTRIBUTES && (attr&FILE_ATTRIBUTE_DIRECTORY)) return 1;
    result=SHCreateDirectoryExW(NULL,path,NULL);
    if(result==ERROR_SUCCESS || result==ERROR_ALREADY_EXISTS || result==ERROR_FILE_EXISTS) {
        attr=GetFileAttributesW(path);
        if(attr!=INVALID_FILE_ATTRIBUTES && (attr&FILE_ATTRIBUTE_DIRECTORY)) return 1;
    }
    SetLastError((DWORD)result); win_error(e,cap,"无法创建保存目录"); return 0;
}
int path_join(wchar_t *out,size_t cap,const wchar_t *folder,const wchar_t *name) {
    return _snwprintf_s(out,cap,_TRUNCATE,L"%s%s%s",folder,wcslen(folder)&&folder[wcslen(folder)-1]==L'\\'?L"":L"\\",name)>=0;
}
double monotonic_seconds(void) { LARGE_INTEGER c,f; QueryPerformanceCounter(&c); QueryPerformanceFrequency(&f); return (double)c.QuadPart/(double)f.QuadPart; }
int stop_requested(HANDLE stop) { return stop && WaitForSingleObject(stop,0)==WAIT_OBJECT_0; }
int wait_seconds(HANDLE stop,double sec) {
    DWORD ms=(DWORD)(sec*1000.0+0.5);
    if(stop) return WaitForSingleObject(stop,ms)==WAIT_OBJECT_0 ? K_CANCEL:K_OK;
    Sleep(ms); return K_OK;
}
