#include "serial_port.h"
#include <string.h>
#include <ctype.h>
static int native_write(void *context,const char *data,size_t length,char *e,size_t cap) {
    SerialPort *p=context; size_t offset=0;
    while(offset<length) { DWORD sent=0;
        if(!WriteFile(p->handle,data+offset,(DWORD)(length-offset),&sent,NULL)) { win_error(e,cap,"串口发送失败"); return 0; }
        if(!sent) { snprintf(e,cap,"串口发送超时"); return 0; } offset+=sent;
    } return 1;
}
static int native_read(void *context,char *byte,char *e,size_t cap) {
    SerialPort *p=context; DWORD received=0;
    if(!ReadFile(p->handle,byte,1,&received,NULL)) { win_error(e,cap,"串口读取失败"); return -1; }
    return received?1:0;
}
static void native_close(void *context) { SerialPort *p=context; if(p->handle!=INVALID_HANDLE_VALUE) CloseHandle(p->handle); p->handle=INVALID_HANDLE_VALUE; }
void serial_init(SerialPort *p,HANDLE stop,double timeout) { memset(p,0,sizeof(*p)); p->handle=INVALID_HANDLE_VALUE; p->stop=stop; p->timeout=timeout; }
void serial_attach(SerialPort *p,SerialOps ops,void *context) { p->ops=ops; p->context=context; p->attached=1; }
int serial_open(SerialPort *p,const Settings *s,char *e,size_t cap) {
    wchar_t path[128]; DCB dcb={0}; COMMTIMEOUTS timeouts={0};
    if(p->attached) return 1;
    _snwprintf_s(path,128,_TRUNCATE,L"\\\\.\\%s",s->port);
    p->handle=CreateFileW(path,GENERIC_READ|GENERIC_WRITE,0,NULL,OPEN_EXISTING,0,NULL);
    if(p->handle==INVALID_HANDLE_VALUE) { win_error(e,cap,"无法打开串口，请检查名称、驱动及占用"); return 0; }
    p->ops=(SerialOps){native_write,native_read,native_close}; p->context=p; p->attached=1;
    dcb.DCBlength=sizeof(dcb);
    if(!GetCommState(p->handle,&dcb)) goto fail;
    dcb.BaudRate=(DWORD)s->baud; dcb.ByteSize=8; dcb.Parity=NOPARITY; dcb.StopBits=ONESTOPBIT;
    dcb.fBinary=TRUE; dcb.fParity=FALSE; dcb.fOutxCtsFlow=FALSE; dcb.fOutxDsrFlow=FALSE;
    dcb.fDtrControl=DTR_CONTROL_DISABLE; dcb.fDsrSensitivity=FALSE; dcb.fTXContinueOnXoff=TRUE;
    dcb.fOutX=s->flow; dcb.fInX=s->flow; dcb.fErrorChar=FALSE; dcb.fNull=FALSE; dcb.fRtsControl=RTS_CONTROL_DISABLE; dcb.fAbortOnError=FALSE;
    dcb.XonChar=0x11; dcb.XoffChar=0x13; dcb.XonLim=128; dcb.XoffLim=128;
    timeouts.ReadIntervalTimeout=MAXDWORD; timeouts.ReadTotalTimeoutConstant=100;
    timeouts.WriteTotalTimeoutConstant=1000;
    if(!SetupComm(p->handle,8192,4096) || !SetCommState(p->handle,&dcb) || !SetCommTimeouts(p->handle,&timeouts) || !PurgeComm(p->handle,PURGE_RXCLEAR|PURGE_TXCLEAR)) goto fail;
    return 1;
fail: win_error(e,cap,"配置串口失败"); serial_close(p); return 0;
}
void serial_trace(SerialPort *p,const char *direction,const char *text) {
    if(p->trace) { char stamp[32]; local_timestamp(stamp,sizeof(stamp)); fprintf(p->trace,"%s %s %s\n",stamp,direction,text); fflush(p->trace); }
}
int serial_raw(SerialPort *p,const char *data,size_t len,char *e,size_t cap) {
    if(!p->attached) { snprintf(e,cap,"串口尚未打开"); return 0; }
    return p->ops.write(p->context,data,len,e,cap);
}
int serial_write(SerialPort *p,const char *command,char *e,size_t cap) {
    char payload[512]; size_t i,n=strlen(command);
    if(!n || n+2>sizeof(payload)) { snprintf(e,cap,"SCPI 命令长度无效"); return 0; }
    for(i=0;i<n;i++) if((unsigned char)command[i]<32 || (unsigned char)command[i]>126) { snprintf(e,cap,"SCPI 命令必须为单行 ASCII"); return 0; }
    memcpy(payload,command,n); payload[n++]='\r'; serial_trace(p,"TX",command);
    return serial_raw(p,payload,n,e,cap);
}
int serial_query(SerialPort *p,const char *command,char *reply,size_t reply_cap,char *e,size_t cap) {
    double deadline; size_t length=0;
    if(stop_requested(p->stop)) return K_CANCEL;
    if(!serial_write(p,command,e,cap)) return 0;
    p->pending=1; deadline=monotonic_seconds()+p->timeout;
    while(monotonic_seconds()<deadline) {
        char c; int r;
        if(stop_requested(p->stop)) return K_CANCEL;
        r=p->ops.read(p->context,&c,e,cap); if(r<0) return 0; if(!r) { Sleep(1); continue; }
        if(c=='\r' || c=='\n') {
            while(length && isspace((unsigned char)reply[length-1])) length--;
            if(!length) continue;
            reply[length]=0; p->pending=0; serial_trace(p,"RX",reply); return 1;
        }
        if((unsigned char)c>127 || !c) { snprintf(e,cap,"串口响应包含无效字符，请检查波特率"); return 0; }
        if(length+1>=reply_cap) { snprintf(e,cap,"串口响应过长"); return 0; }
        reply[length++]=c;
    }
    snprintf(e,cap,"%s 响应超时；检查串口、波特率、FLOW 和接线",command); return 0;
}
void serial_close(SerialPort *p) { if(p->attached && p->ops.close) p->ops.close(p->context); p->attached=0; }
int serial_list(wchar_t names[][64],int capacity) {
    wchar_t port[64],target[1024]; int i,n=0;
    for(i=1;i<=256 && n<capacity;i++) { swprintf_s(port,64,L"COM%d",i); if(QueryDosDeviceW(port,target,1024)) wcscpy_s(names[n++],64,port); }
    return n;
}
