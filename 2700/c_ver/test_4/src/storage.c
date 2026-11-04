#include "storage.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
static int write_bytes(HANDLE file,const char *data,size_t n,char *e,size_t cap) {
    while(n) { DWORD done=0; if(!WriteFile(file,data,(DWORD)n,&done,NULL) || !done) { win_error(e,cap,"写入数据文件失败"); return 0; } data+=done; n-=done; } return 1;
}
static int sync_file(HANDLE h,char *e,size_t cap) { if(!FlushFileBuffers(h)) { win_error(e,cap,"同步数据文件失败"); return 0; } return 1; }
int data_open(DataFile *f,const wchar_t *path,char *e,size_t cap) {
    const char *header="timestamp\tchannel\tmode\tunit\tvalue\tstatus\r\n";
    f->handle=CreateFileW(path,GENERIC_WRITE,FILE_SHARE_READ,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if(f->handle==INVALID_HANDLE_VALUE) { win_error(e,cap,"无法创建 TXT（不覆盖已有文件）"); return 0; }
    if(!write_bytes(f->handle,header,strlen(header),e,cap) || !sync_file(f->handle,e,cap)) { data_close(f); DeleteFileW(path); return 0; }
    return 1;
}
int data_append(DataFile *f,const Sample *s,char *e,size_t cap) {
    char row[512],value[64]="",channel[32];
    if(s->channel) snprintf(channel,sizeof(channel),"%d",s->channel); else strcpy_s(channel,sizeof(channel),"FRONT");
    if(s->valid) snprintf(value,sizeof(value),"%.12g",s->value);
    snprintf(row,sizeof(row),"%s\t%s\t%s\t%s\t%s\t%s\r\n",s->timestamp,channel,mode_name(s->mode),mode_unit(s->mode),value,s->valid?"OK":"OVERLOAD");
    return write_bytes(f->handle,row,strlen(row),e,cap) && sync_file(f->handle,e,cap);
}
void data_close(DataFile *f) { if(f->handle && f->handle!=INVALID_HANDLE_VALUE) CloseHandle(f->handle); f->handle=INVALID_HANDLE_VALUE; }
/* TSV records from the Python and native apps; quoted cells and escaped quotes
   are decoded, empty cells preserved, malformed/overlong records rejected. */
static int split_record(char *line,char *fields[],int max) {
    char *r=line,*w=line; int n=0;
    for(;;) {
        int quoted=(*r=='"'); if(n>=max) return -1; fields[n++]=w;
        if(quoted) {
            r++;
            for(;;) {
                if(!*r) return -1;
                if(*r=='"') { r++; if(*r=='"') { *w++=*r++; } else break; }
                else *w++=*r++;
            }
            if(*r && *r!='\t' && *r!='\r' && *r!='\n') return -1;
        } else while(*r && *r!='\t' && *r!='\r' && *r!='\n') *w++=*r++;
        { char separator=*r; if(separator) r++; *w++=0;
          if(separator=='\t') continue;
          if(separator=='\r' && *r=='\n') r++;
          if(*r) return -1; return n; }
    }
}
static int csv_field(HANDLE out,const char *s,char *e,size_t cap) {
    const char *p=s,*start=s;
    if(!write_bytes(out,"\"",1,e,cap)) return 0;
    while(*p) {
        if(*p=='"') { if(!write_bytes(out,start,(size_t)(p-start),e,cap) || !write_bytes(out,"\"\"",2,e,cap)) return 0; start=p+1; } p++;
    }
    return write_bytes(out,start,(size_t)(p-start),e,cap) && write_bytes(out,"\"",1,e,cap);
}
int csv_convert(const wchar_t *source,const wchar_t *dest,HANDLE stop,uint64_t *count,char *e,size_t cap) {
    static const char *schemas[]={
        "timestamp\tchannel\tmode\tunit\tvalue\tstatus",
        "timestamp\tchannel\tmode\tunit\tvalue\tstatus\traw\tsource",
        "timestamp\tchannel\tmode\tunit\tvalue\tstatus\traw\tsource\trange_setting\tnplc_setting\tsettle_s",
        "timestamp\tmode\tunit\tvalue\tstatus\traw\tsource",
        "timestamp\telapsed_s\tmode\tunit\tvalue\tstatus\traw\tsource"};
    static const char *wanted[]={"timestamp","channel","mode","unit","value","status"};
    FILE *input=NULL; HANDLE output=INVALID_HANDLE_VALUE; char line[8192],header[8192],*fields[16];
    int n,map[6],i,j,known=0,result=0; *count=0;
    if(!_wcsicmp(source,dest)) { snprintf(e,cap,"输出文件不能与输入文件相同"); return 0; }
    if(_wfopen_s(&input,source,L"rb") || !input) { snprintf(e,cap,"无法读取 TXT 文件"); return 0; }
    if(!fgets(line,sizeof(line),input)) { snprintf(e,cap,"TXT 文件为空"); goto end; }
    { char *start=line; if(strlen(line)>=3 && !memcmp(line,"\xef\xbb\xbf",3)) start+=3;
      strcpy_s(header,sizeof(header),start); header[strcspn(header,"\r\n")]=0; }
    for(i=0;i<5;i++) if(!strcmp(header,schemas[i])) known=1;
    if(!known) { snprintf(e,cap,"TXT 字段不属于本程序或兼容旧版格式"); goto end; }
    n=split_record(header,fields,16); if(n<0) { snprintf(e,cap,"TXT 表头异常"); goto end; }
    for(i=0;i<6;i++) { map[i]=-1; for(j=0;j<n;j++) if(!strcmp(fields[j],wanted[i])) map[i]=j; }
    output=CreateFileW(dest,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if(output==INVALID_HANDLE_VALUE) { win_error(e,cap,"无法创建 CSV（请选择未使用的文件名）"); goto end; }
    if(!write_bytes(output,"\xef\xbb\xbf" "timestamp,channel,mode,unit,value,status\r\n",strlen("\xef\xbb\xbf" "timestamp,channel,mode,unit,value,status\r\n"),e,cap)) goto end;
    while(fgets(line,sizeof(line),input)) {
        int columns;
        if(stop_requested(stop)) { result=K_CANCEL; goto end; }
        if(!strchr(line,'\n') && !feof(input)) { snprintf(e,cap,"TXT 第 %llu 行过长",(unsigned long long)(*count+2)); goto end; }
        columns=split_record(line,fields,16);
        if(columns!=n) { snprintf(e,cap,"TXT 第 %llu 行不完整或格式异常",(unsigned long long)(*count+2)); goto end; }
        for(i=0;i<6;i++) {
            if(i && !write_bytes(output,",",1,e,cap)) goto end;
            if(!csv_field(output,map[i]<0?"FRONT":fields[map[i]],e,cap)) goto end;
        }
        if(!write_bytes(output,"\r\n",2,e,cap)) goto end;
        (*count)++;
    }
    if(ferror(input)) { snprintf(e,cap,"读取 TXT 失败"); goto end; }
    if(!sync_file(output,e,cap)) goto end;
    result=1;
end:
    fclose(input);
    if(output!=INVALID_HANDLE_VALUE) { CloseHandle(output); if(result!=1) DeleteFileW(dest); }
    return result;
}
