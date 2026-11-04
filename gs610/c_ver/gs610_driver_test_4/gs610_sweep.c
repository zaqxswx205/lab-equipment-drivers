/* GS610 RS-232 DC current sweep, Windows C11.
 * Protocol: info/IM765501-01E.pdf, sections 14, 16, 18.1.
 * All source currents use exact signed 100 pA ticks, never accumulated doubles.
 */
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <inttypes.h>
#include <string.h>
#include <ctype.h>
#include <math.h>
#include <stdarg.h>
#include <wchar.h>
#include <io.h>
#include <fcntl.h>

#define SCALE INT64_C(10000000000)
#define MAX_POINTS 100000
#define PATH_CAP 32768
#define RESPONSE_CAP 4096
typedef struct { int64_t nominal, capacity, resolution; double max_voltage; } Range;
static const Range ranges[] = {
    {200000,205000,1,110}, {2000000,2050000,10,110},
    {20000000,20500000,100,110}, {200000000,205000000,1000,110},
    {2000000000,2050000000,10000,110},
    {5000000000,5000000000,100000,110},
    {10000000000,10000000000,100000,60},
    {20000000000,20000000000,100000,30},
    {30000000000,32000000000,100000,12}
};
typedef struct {
    char port[256], start[128], stop[128], step[128], limit[128], csv[PATH_CAP];
    int baud, bits, stops, round_trip, keep, list, identify, dry, interactive;
    char parity, flow[16], term[8];
    double timeout, dwell;
} Options;
typedef struct { int64_t *points; size_t count, turn; const Range *range; double limit; } Plan;
typedef struct { size_t point; double elapsed, voltage; int64_t current; } Record;
#ifndef GS610_RECORD
#define GS610_RECORD(record, plan) ((void)0)
#endif
typedef struct { HANDLE handle; int synced; const char *term; DWORD timeout; } Instrument;
static char error_text[1024];
static volatile LONG interrupted = 0;
static wchar_t app_dir[PATH_CAP];

static int fail(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vsnprintf(error_text,sizeof(error_text),fmt,ap); va_end(ap);
    return 0;
}
static int winfail(const char *what) { return fail("%s (Windows error %lu)",what,GetLastError()); }
static BOOL WINAPI ctrl_handler(DWORD event) {
    if (event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT) {
        InterlockedExchange(&interrupted,1); return TRUE;
    }
    return FALSE;
}
static char *trim(char *s) {
    char *end; while (isspace((unsigned char)*s)) ++s;
    end=s+strlen(s); while(end>s && isspace((unsigned char)end[-1])) --end;
    *end=0; return s;
}
static int copy_text(char *dest, size_t cap, const char *src) {
    if(strlen(src)>=cap) return fail("参数过长");
    strcpy(dest,src); return 1;
}
static int finite_number(const char *s, double *value) {
    char *end; *value=strtod(s,&end);
    if(end==s || *end || !isfinite(*value)) return fail("无效的有限数字: %s",s);
    return 1;
}
/* Parse decimal/scientific notation exactly; ignore redundant leading/trailing zeros.
 * Reject nonzero digits below the instrument's finest 100 pA resolution.
 */
static int ticks(const char *s, int64_t *out) {
    const char *p=s; char digits[256]; size_t n=0, first=0;
    int sign=1, dot=0, frac=0, exponent=0, esign=1, shift;
    int64_t v=0;
    if(*p=='+' || *p=='-') { if(*p=='-') sign=-1; ++p; }
    while(isdigit((unsigned char)*p) || *p=='.') {
        if(*p=='.') { if(dot) return fail("无效电流: %s",s); dot=1; }
        else { if(n>=sizeof(digits)) return fail("电流数字过长"); digits[n++]=*p; if(dot) ++frac; }
        ++p;
    }
    if(!n) return fail("无效电流: %s",s);
    if(*p=='e' || *p=='E') {
        ++p; if(*p=='+' || *p=='-') {if(*p=='-') esign=-1; ++p;}
        if(!isdigit((unsigned char)*p)) return fail("无效指数: %s",s);
        while(isdigit((unsigned char)*p)) {
            if(exponent>10000) return fail("电流指数过大: %s",s);
            exponent=exponent*10+(*p++-'0');
        }
    }
    if(*p) return fail("无效电流: %s",s);
    shift=10-frac+esign*exponent;
    while(first<n && digits[first]=='0') ++first;
    if(first==n) { *out=0; return 1; }
    while(n>first && digits[n-1]=='0') { --n; ++shift; }
    if(shift<0) return fail("电流精度不能小于 100 pA: %s",s);
    if(shift>18 || n-first+(size_t)shift>19) return fail("电流数字超出支持范围: %s",s);
    for(size_t i=first;i<n;++i) {
        int d=digits[i]-'0';
        if(v>(INT64_MAX-d)/10) return fail("电流数字溢出: %s",s);
        v=v*10+d;
    }
    for(int i=0;i<shift;++i) {
        if(v>INT64_MAX/10) return fail("电流数字溢出: %s",s);
        v*=10;
    }
    *out=sign*v; return 1;
}
static void format_ticks(int64_t value, char out[64]) {
    uint64_t v=(uint64_t)(value<0 ? -value : value);
    char *end;
    snprintf(out,64,"%s%" PRIu64 ".%010" PRIu64,value<0?"-":"",v/(uint64_t)SCALE,v%(uint64_t)SCALE);
    end=out+strlen(out); while(end>out && end[-1]=='0') --end;
    if(end>out && end[-1]=='.') --end; *end=0;
}
static int make_plan(const Options *o, Plan *p) {
    int64_t start,stop,step,distance,peak; size_t leg, total;
    memset(p,0,sizeof(*p));
    if(!ticks(o->start,&start) || !ticks(o->stop,&stop) || !ticks(o->step,&step)) return 0;
    if(step<=0) return fail("步进电流必须大于 0，方向由起止电流决定");
    if(llabs(start)>32000000000 || llabs(stop)>32000000000) return fail("电流必须在 -3.2 A 到 +3.2 A 之间");
    distance=llabs(stop-start);
    if(distance/step>MAX_POINTS) return fail("扫描点数超过 100000，请增大步进");
    leg=(size_t)(distance/step)+1+(distance%step!=0);
    total=leg*(o->round_trip?2:1);
    if(total>MAX_POINTS) return fail("扫描总点数超过 100000，请增大步进");
    peak=llabs(start)>llabs(stop)?llabs(start):llabs(stop);
    for(size_t i=0;i<sizeof(ranges)/sizeof(ranges[0]);++i)
        if(peak<=ranges[i].capacity) { p->range=&ranges[i]; break; }
    if(!finite_number(o->limit,&p->limit)) return 0;
    if(p->limit<0.001 || p->limit>p->range->max_voltage)
        return fail("此量程限压必须在 0.001 V 到 %g V 之间",p->range->max_voltage);
    p->points=malloc(total*sizeof(*p->points));
    if(!p->points) return fail("扫描点内存分配失败");
    p->count=total; p->turn=leg;
    for(size_t i=0;i<total;++i) {
        size_t j=i%leg; int returning=i>=leg; int64_t a=returning?stop:start, b=returning?start:stop;
        /* The final point is always the exact endpoint, including a short last step. */
        p->points[i]=(j==leg-1)?b:a+(b>=a?1:-1)*step*(int64_t)j;
        if(p->points[i]%p->range->resolution) {
            char r[64],q[64]; format_ticks(p->range->nominal,r); format_ticks(p->range->resolution,q);
            free(p->points); p->points=NULL;
            return fail("%s A 量程分辨率为 %s A，扫描点必须为其整数倍",r,q);
        }
    }
    return 1;
}
static int to_wide(const char *s, wchar_t *out, int cap) {
    if(!MultiByteToWideChar(CP_UTF8,MB_ERR_INVALID_CHARS,s,-1,out,cap)) return winfail("UTF-8 路径转换失败");
    return 1;
}
static int from_wide(const wchar_t *s, char *out, int cap) {
    if(!WideCharToMultiByte(CP_UTF8,0,s,-1,out,cap,NULL,NULL)) return winfail("路径转换失败");
    return 1;
}
static int absolute_path(const char *s, wchar_t out[PATH_CAP]) {
    wchar_t raw[PATH_CAP], joined[PATH_CAP]; DWORD length;
    if(!to_wide(s,raw,PATH_CAP)) return 0;
    if((wcslen(raw)>=3 && raw[1]==L':' && (raw[2]==L'\\' || raw[2]==L'/')) ||
       (raw[0]==L'\\' && raw[1]==L'\\')) {
        wcscpy(joined,raw);
    } else {
        /* Reject drive-relative and root-relative names rather than use an ambiguous cwd. */
        if(wcschr(raw,L':') || raw[0]==L'\\' || raw[0]==L'/') return fail("请使用完整绝对路径或相对于程序目录的路径");
        if(swprintf(joined,PATH_CAP,L"%ls\\%ls",app_dir,raw)<0) return fail("路径过长");
    }
    length=GetFullPathNameW(joined,PATH_CAP,out,NULL);
    if(!length || length>=PATH_CAP) return winfail("解析路径失败");
    return 1;
}
static int init_paths(Options *o) {
    SYSTEMTIME utc,china; FILETIME ft; ULARGE_INTEGER t; wchar_t *slash;
    DWORD len=GetModuleFileNameW(NULL,app_dir,PATH_CAP);
    if(!len || len>=PATH_CAP) return winfail("无法确定程序目录");
    slash=wcsrchr(app_dir,L'\\'); if(!slash) return fail("无法确定程序目录"); *slash=0;
    GetSystemTime(&utc); SystemTimeToFileTime(&utc,&ft);
    t.LowPart=ft.dwLowDateTime; t.HighPart=ft.dwHighDateTime;
    t.QuadPart+=UINT64_C(8)*60*60*10000000;
    ft.dwLowDateTime=t.LowPart; ft.dwHighDateTime=t.HighPart; FileTimeToSystemTime(&ft,&china);
    snprintf(o->csv,sizeof(o->csv),"result/result-%04u%02u%02u-%02u%02u%02u-%03u-%lu.csv",
        china.wYear,china.wMonth,china.wDay,china.wHour,china.wMinute,china.wSecond,china.wMilliseconds,GetCurrentProcessId());
    return 1;
}
static int create_result_dir(void) {
    wchar_t path[PATH_CAP];
    if(swprintf(path,PATH_CAP,L"%ls\\result",app_dir)<0) return fail("路径过长");
    if(!CreateDirectoryW(path,NULL) && GetLastError()!=ERROR_ALREADY_EXISTS) return winfail("创建 result 目录失败");
    return 1;
}
static int valid_settings(const Options *o) {
    int bauds[]={9600,14400,19200,38400,57600,115200}, found=0;
    for(size_t i=0;i<sizeof(bauds)/sizeof(bauds[0]);++i) if(o->baud==bauds[i]) found=1;
    if(!found) return fail("不支持的波特率");
    if(o->bits!=7 && o->bits!=8) return fail("数据位必须为 7 或 8");
    if(!strchr("NEO",o->parity) || !o->parity) return fail("校验位必须为 N/E/O");
    if(o->stops!=1 && o->stops!=2) return fail("停止位必须为 1 或 2");
    if(strcmp(o->flow,"none") && strcmp(o->flow,"xon") && strcmp(o->flow,"rtscts")) return fail("流控必须为 none/xon/rtscts");
    if(strcmp(o->term,"LF") && strcmp(o->term,"CR") && strcmp(o->term,"CRLF")) return fail("结束符必须为 CR/LF/CRLF");
    if(!isfinite(o->timeout) || o->timeout<=0 || o->timeout>4294967.0) return fail("超时必须为正数且不大于 4294967 秒");
    if(!isfinite(o->dwell) || o->dwell<0) return fail("等待时间必须为有限非负数");
    return 1;
}
static void usage(void) {
    puts("GS610 RS-232 电流扫描（单位 A / V / s）\n"
         "不带参数运行：交互设置。默认 -3.2 -> 3.2 A，步进 0.08 A，限压 12 V。\n"
         "gs610_sweep.exe --port COM3 --round-trip [选项]\n"
         "  --list                       列出串口\n"
         "  --identify                   仅查询设备型号\n"
         "  --start A --stop A --step A   起始、截止、正步进\n"
         "  --voltage-limit V            对称限压（默认 12）\n"
         "  --round-trip | --single-pass 往返或单程，未指定时询问\n"
         "  --dwell S                    每点采样前等待（默认 0.1）\n"
         "  --csv PATH                   保存路径，相对路径以 exe 目录为基准\n"
         "  --keep-output                仅在全部成功后保持最终输出\n"
         "  --baudrate N                 9600/14400/19200/38400/57600/115200\n"
         "  --bytesize 7|8 --parity N|E|O --stopbits 1|2\n"
         "  --flow none|xon|rtscts --terminator CR|LF|CRLF\n"
         "  --timeout S                  读写超时（默认 5）\n"
         "  --dry-run                    预览，不连接设备或创建 CSV\n"
         "  --interactive                逐项设置\n"
         "  --help                       显示帮助");
}
static int integer_arg(const char *s, int *out) {
    double v; if(!finite_number(s,&v)) return 0;
    if(v<0 || v>1000000 || floor(v)!=v) return fail("无效整数: %s",s);
    *out=(int)v; return 1;
}
static int parse_options(int argc, char **argv, Options *o) {
    int mode_seen=0;
    for(int i=1;i<argc;++i) {
        const char *a=argv[i], *v;
        if(!strcmp(a,"--help") || !strcmp(a,"-h")) { usage(); return 2; }
        if(!strcmp(a,"--list")) {o->list=1; continue;}
        if(!strcmp(a,"--identify")) {o->identify=1; continue;}
        if(!strcmp(a,"--dry-run")) {o->dry=1; continue;}
        if(!strcmp(a,"--interactive")) {o->interactive=1; continue;}
        if(!strcmp(a,"--keep-output")) {o->keep=1; continue;}
        if(!strcmp(a,"--round-trip") || !strcmp(a,"--single-pass")) {
            int mode=!strcmp(a,"--round-trip");
            if(mode_seen && o->round_trip!=mode) return fail("往返和单程选项不能同时使用");
            o->round_trip=mode; mode_seen=1; continue;
        }
        if(i+1==argc) return fail("选项缺少值: %s",a);
        v=argv[++i];
#define TEXT_OPT(name,field) if(!strcmp(a,name)) {if(!copy_text(o->field,sizeof(o->field),v)) return 0; continue;}
#define INT_OPT(name,field) if(!strcmp(a,name)) {if(!integer_arg(v,&o->field)) return 0; continue;}
#define NUM_OPT(name,field) if(!strcmp(a,name)) {if(!finite_number(v,&o->field)) return 0; continue;}
        TEXT_OPT("--port",port) TEXT_OPT("--start",start) TEXT_OPT("--stop",stop)
        TEXT_OPT("--step",step) TEXT_OPT("--voltage-limit",limit) TEXT_OPT("--csv",csv)
        TEXT_OPT("--flow",flow) TEXT_OPT("--terminator",term)
        INT_OPT("--baudrate",baud) INT_OPT("--bytesize",bits) INT_OPT("--stopbits",stops)
        NUM_OPT("--timeout",timeout) NUM_OPT("--dwell",dwell)
        if(!strcmp(a,"--parity")) {if(strlen(v)!=1) return fail("校验位必须为 N/E/O"); o->parity=v[0]; continue;}
        return fail("未知选项: %s",a);
    }
    if(o->dry && (o->list || o->identify)) return fail("--dry-run 不能与 --list/--identify 一起使用");
    if(o->interactive && (o->list || o->identify)) return fail("交互设置不能与 --list/--identify 一起使用");
    return 1;
}
static int prompt(const char *label, const char *def, char *out, size_t cap) {
    char line[PATH_CAP], *value;
    if(interrupted) return fail("操作已中断");
    printf("%s [%s]: ",label,def); fflush(stdout);
    if(!fgets(line,sizeof(line),stdin)) return fail("输入结束或操作已中断");
    if(!strchr(line,'\n') && !feof(stdin)) {
        int c; while((c=getchar())!='\n' && c!=EOF) {} return fail("输入过长");
    }
    value=trim(line); return copy_text(out,cap,*value?value:def);
}
static int prompt_text(const char *label, char *value, size_t cap, const char *choices) {
    char raw[PATH_CAP];
    for(;;) {
        if(!prompt(label,value,raw,sizeof(raw))) return 0;
        if(!*raw) {puts("此项不能为空"); continue;}
        if(choices) {
            char needle[128]; snprintf(needle,sizeof(needle),"|%s|",raw);
            if(!strstr(choices,needle)) {puts("输入不在可选值中，请重新输入"); continue;}
        }
        if(copy_text(value,cap,raw)) return 1;
        puts(error_text);
    }
}
static int prompt_int(const char *label,int *value,const char *choices) {
    char raw[64]; snprintf(raw,sizeof(raw),"%d",*value);
    if(!prompt_text(label,raw,sizeof(raw),choices)) return 0;
    return integer_arg(raw,value);
}
static int prompt_time(const char *label,double *value,int positive) {
    char def[64],raw[128];
    for(;;) {
        /* Human-facing defaults should not expose binary floating-point tails. */
        snprintf(def,sizeof(def),"%.15g",*value);
        if(!prompt(label,def,raw,sizeof(raw))) return 0;
        if(finite_number(raw,value) && (positive?*value>0 && *value<=4294967.0:*value>=0)) return 1;
        puts("请输入有效时间，超时须为正数，等待时间可为 0");
    }
}
static int prompt_yes(const char *label,int *value) {
    char raw[8]; strcpy(raw,*value==0?"n":"y");
    if(!prompt_text(label,raw,sizeof(raw),"|y||n|")) return 0;
    *value=!strcmp(raw,"y"); return 1;
}
static int list_ports(void) {
    wchar_t name[32], target[2048]; int found=0;
    for(int n=1;n<=4096;++n) {
        swprintf(name,32,L"COM%d",n);
        if(QueryDosDeviceW(name,target,2048)) {
            char desc[4096]; if(!from_wide(target,desc,sizeof(desc))) return 0;
            printf("COM%d: %s\n",n,desc); found=1;
        }
    }
    if(!found) puts("未找到串口，请检查 RS-232 适配器及驱动");
    return 1;
}
static int interactive(Options *o) {
    char parity[8]={o->parity,0}; Plan p; wchar_t path[PATH_CAP];
    puts("GS610 串口电流扫描设置（回车采用默认值，Ctrl+C 退出）");
    if(!list_ports() || !prompt_text("串口名（例如 COM3）",o->port,sizeof(o->port),NULL) ||
       !prompt_int("波特率",&o->baud,"|9600||14400||19200||38400||57600||115200|") ||
       !prompt_int("数据位（7/8）",&o->bits,"|7||8|") ||
       !prompt_text("校验位（N/E/O）",parity,sizeof(parity),"|N||E||O|") ||
       !prompt_int("停止位（1/2）",&o->stops,"|1||2|") ||
       !prompt_text("流控（none/xon/rtscts）",o->flow,sizeof(o->flow),"|none||xon||rtscts|") ||
       !prompt_text("结束符（CR/LF/CRLF）",o->term,sizeof(o->term),"|CR||LF||CRLF|") ||
       !prompt_time("串口超时（秒）",&o->timeout,1) ||
       !prompt_yes("是否双程扫描（y/n）",&o->round_trip)) return 0;
    o->parity=parity[0];
    for(;;) {
        if(!prompt_text("初始电流（A）",o->start,sizeof(o->start),NULL) ||
           !prompt_text("截止电流（A）",o->stop,sizeof(o->stop),NULL) ||
           !prompt_text("步进电流（A，正数）",o->step,sizeof(o->step),NULL) ||
           !prompt_text("限压（V）",o->limit,sizeof(o->limit),NULL)) return 0;
        if(make_plan(o,&p)) {free(p.points); break;}
        printf("扫描参数不匹配: %s，请重新设置\n",error_text);
    }
    if(!prompt_time("每点采样前等待（秒）",&o->dwell,0) ||
       !prompt_yes("成功结束后保持最终电流输出（y/n）",&o->keep)) return 0;
    for(;;) {
        wchar_t parent[PATH_CAP], *slash; DWORD attrs;
        if(!prompt_text("CSV 保存路径",o->csv,sizeof(o->csv),NULL)) return 0;
        if(!absolute_path(o->csv,path)) {puts(error_text); continue;}
        if(GetFileAttributesW(path)!=INVALID_FILE_ATTRIBUTES) {puts("文件已存在，请换一个文件名"); continue;}
        wcscpy(parent,path); slash=wcsrchr(parent,L'\\'); if(slash) *slash=0;
        attrs=GetFileAttributesW(parent);
        if(attrs!=INVALID_FILE_ATTRIBUTES && (attrs&FILE_ATTRIBUTE_DIRECTORY)) break;
        puts("保存目录不存在，请输入已有目录下的路径");
    }
    return 1;
}
static int serial_open(Instrument *d,const Options *o) {
    char port[300]; wchar_t wide[300]; DCB cfg={0}; COMMTIMEOUTS tm={0};
    if(strncmp(o->port,"\\\\.\\",4)) snprintf(port,sizeof(port),"\\\\.\\%s",o->port);
    else if(!copy_text(port,sizeof(port),o->port)) return 0;
    if(!to_wide(port,wide,300)) return 0;
    d->handle=CreateFileW(wide,GENERIC_READ|GENERIC_WRITE,0,NULL,OPEN_EXISTING,0,NULL);
    if(d->handle==INVALID_HANDLE_VALUE) return winfail("打开串口失败");
    cfg.DCBlength=sizeof(cfg);
    if(!GetCommState(d->handle,&cfg)) return winfail("读取串口配置失败");
    cfg.BaudRate=(DWORD)o->baud; cfg.ByteSize=(BYTE)o->bits;
    cfg.Parity=o->parity=='N'?NOPARITY:o->parity=='E'?EVENPARITY:ODDPARITY;
    cfg.StopBits=o->stops==1?ONESTOPBIT:TWOSTOPBITS;
    cfg.fBinary=TRUE; cfg.fParity=o->parity!='N';
    cfg.fOutxCtsFlow=!strcmp(o->flow,"rtscts"); cfg.fOutxDsrFlow=FALSE;
    cfg.fDtrControl=DTR_CONTROL_ENABLE; cfg.fDsrSensitivity=FALSE;
    cfg.fTXContinueOnXoff=FALSE; cfg.fOutX=cfg.fInX=!strcmp(o->flow,"xon");
    cfg.fErrorChar=FALSE; cfg.fNull=FALSE; cfg.fAbortOnError=FALSE;
    cfg.fRtsControl=cfg.fOutxCtsFlow?RTS_CONTROL_HANDSHAKE:RTS_CONTROL_ENABLE;
    cfg.XonChar=0x11; cfg.XoffChar=0x13; cfg.XonLim=128; cfg.XoffLim=128;
    d->timeout=(DWORD)ceil(o->timeout*1000.0);
    tm.ReadIntervalTimeout=MAXDWORD; tm.ReadTotalTimeoutMultiplier=0;
    tm.ReadTotalTimeoutConstant=50; tm.WriteTotalTimeoutConstant=d->timeout;
    if(!SetCommState(d->handle,&cfg) || !SetCommTimeouts(d->handle,&tm) ||
       !PurgeComm(d->handle,PURGE_RXCLEAR)) return winfail("配置串口失败");
    d->term=!strcmp(o->term,"CR")?"\r":!strcmp(o->term,"CRLF")?"\r\n":"\n";
    d->synced=1; return 1;
}
static int device_write(Instrument *d,const char *command) {
    char wire[512]; DWORD written; size_t len;
    if(strchr(command,'\r') || strchr(command,'\n')) return fail("单条命令不能包含换行");
    if(strlen(command)+strlen(d->term)>=sizeof(wire)) return fail("命令过长");
    snprintf(wire,sizeof(wire),"%s%s",command,d->term); len=strlen(wire);
    if(!WriteFile(d->handle,wire,(DWORD)len,&written,NULL) || written!=len) {
        d->synced=0; return winfail("串口发送不完整，请检查流控和连接");
    }
    return 1;
}
static int query(Instrument *d,const char *command,char out[RESPONSE_CAP]) {
    size_t n=0,term_len=strlen(d->term); ULONGLONG started;
    if(interrupted) return fail("扫描已中断");
    if(!d->synced) return fail("串口响应已失步，请重新连接；仍可发送关闭输出命令");
    if(!device_write(d,command)) return 0;
    started=GetTickCount64();
    while(n<RESPONSE_CAP-1 && GetTickCount64()-started<d->timeout) {
        DWORD got;
        if(interrupted) {d->synced=0; return fail("扫描已中断");}
        if(!ReadFile(d->handle,out+n,1,&got,NULL)) {d->synced=0; return winfail("串口读取失败");}
        if(!got) continue;
        if((unsigned char)out[n]>127 || out[n]==0) {
            d->synced=0; return fail("设备响应不是有效的 ASCII 文本");
        }
        ++n;
        if(n>=term_len && !memcmp(out+n-term_len,d->term,term_len)) {
            char *text; out[n-term_len]=0; text=trim(out);
            if(!*text) {d->synced=0; return fail("设备返回空响应");}
            memmove(out,text,strlen(text)+1); return 1;
        }
    }
    d->synced=0; return fail("串口响应超时或不完整（命令 %s），检查波特率、接线和 Term 设置",command);
}
static int device_error(Instrument *d) {
    char response[RESPONSE_CAP], *end; long code;
    if(!query(d,":SYST:ERR?",response)) return 0;
    code=strtol(response,&end,10);
    if(end==response || (*end && *end!=',')) return fail("设备错误响应无法解析: %s",response);
    if(code) return fail("GS610 错误: %s",response);
    return 1;
}
static int checked_write(Instrument *d,const char *command) {
    if(interrupted) return fail("扫描已中断");
    return device_write(d,command) && device_error(d);
}
static int number_query(Instrument *d,const char *command,double *v) {
    char response[RESPONSE_CAP]; return query(d,command,response) && finite_number(response,v);
}
static int expect(Instrument *d,const char *command,const char *expected) {
    char response[RESPONSE_CAP];
    if(!query(d,command,response)) return 0;
    if(strcmp(response,expected)) return fail("设备状态不符合要求: %s 返回 %s，应为 %s",command,response,expected);
    return 1;
}
static int configure(Instrument *d,const Plan *p) {
    static const char *commands[]={
        ":SOUR:FUNC CURR", ":SOUR:SHAP DC", ":SOUR:MODE FIX", ":TRIG:SOUR IMM", ":SOUR:DEL MIN",
        NULL, ":SOUR:VOLT:PROT:STAT ON", ":SOUR:VOLT:PROT:LINK ON", NULL,
        ":SENS:ACH OFF", ":SENS:FUNC VOLT", ":SENS:STAT ON", ":SENS:RANG:AUTO ON",
        ":SENS:ITIM PLC", ":SENS:DEL MIN", ":SENS:AVER:STAT OFF", ":CALC:MATH:STAT OFF", ":CALC:NULL:STAT OFF"
    };
    char range[64],command[128],response[RESPONSE_CAP]; double upper,lower;
    if(!device_write(d,":SYST:REM") || !checked_write(d,":OUTP OFF")) return 0;
    format_ticks(p->range->nominal,range);
    for(size_t i=0;i<sizeof(commands)/sizeof(commands[0]);++i) {
        if(i==5) snprintf(command,sizeof(command),":SOUR:CURR:RANG %s",range);
        else if(i==8) snprintf(command,sizeof(command),":SOUR:VOLT:PROT:ULIM %.17g",p->limit);
        else strcpy(command,commands[i]);
        if(!checked_write(d,command)) return 0;
    }
    if(!number_query(d,":SOUR:VOLT:PROT:ULIM?",&upper) ||
       !number_query(d,":SOUR:VOLT:PROT:LLIM?",&lower) ||
       !expect(d,":SOUR:VOLT:PROT:STAT?","1")) return 0;
    if(upper<=0 || lower>=0 || upper>p->limit || -lower>p->limit)
        return fail("实际限压不符合请求: %g V ~ %g V",lower,upper);
    printf("实际限压: %g V ~ %g V\n",lower,upper);
    if(!query(d,":SENS:FUNC?",response)) return 0;
    if(_stricmp(response,"VOLT") && _stricmp(response,"VOLTAGE")) return fail("设备未切换为电压测量");
    return 1;
}
static int set_current(Instrument *d,int64_t current) {
    char value[64],command[128],response[RESPONSE_CAP]; int64_t actual;
    format_ticks(current,value); snprintf(command,sizeof(command),":SOUR:CURR:LEV %s",value);
    if(!checked_write(d,command) || !query(d,":SOUR:CURR:LEV?",response) || !ticks(response,&actual)) return 0;
    if(actual!=current) return fail("电流被设备调整: 请求 %s A，读回 %s A",value,response);
    return 1;
}
static int measure(Instrument *d,double *voltage) {
    char result[RESPONSE_CAP],status[RESPONSE_CAP],*end; unsigned long bits;
    if(!query(d,":READ?",result) || !device_error(d) || !query(d,":STAT:SENS:COND?",status)) return 0;
    bits=strtoul(status,&end,10);
    if(end==status || *end || bits>65535) return fail("无效的测量状态: %s",status);
    if(bits&(1u<<5)) return fail("反馈电压测量超量程");
    return finite_number(result,voltage);
}
static int wait_dwell(double seconds) {
    ULONGLONG start=GetTickCount64();
    while((double)(GetTickCount64()-start)/1000.0<seconds) {
        if(interrupted) return fail("扫描已中断");
        double remaining=seconds*1000.0-(double)(GetTickCount64()-start);
        Sleep(remaining>=50?50:remaining>1?(DWORD)ceil(remaining):1);
    }
    if(interrupted) return fail("扫描已中断");
    return 1;
}
static const char *csv_fields="point,elapsed_s,set_current_A,measured_voltage_V";
static int csv_header(FILE *f,int returning) {
    if(fwrite("\xef\xbb\xbf",1,3,f)!=3) return fail("CSV 写入失败");
    if(fprintf(f,"%s%s\r\n",csv_fields,returning?",return_point,return_elapsed_s,return_set_current_A,return_measured_voltage_V":"")<0)
        return fail("CSV 表头写入失败");
    return 1;
}
static FILE *exclusive_file(const wchar_t *path) {
    /* CREATE_NEW reserves before any scan configuration/output commands. */
    HANDLE h=CreateFileW(path,GENERIC_WRITE,0,NULL,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,NULL);
    if(h==INVALID_HANDLE_VALUE) {winfail("无法创建 CSV（不会覆盖已有文件）"); return NULL;}
    int fd=_open_osfhandle((intptr_t)h,_O_BINARY|_O_WRONLY);
    if(fd<0) {CloseHandle(h); fail("无法打开已预留的 CSV"); return NULL;}
    FILE *f=_fdopen(fd,"wb");
    if(!f) {_close(fd); fail("无法创建 CSV 文件流");}
    return f;
}
static int csv_record(FILE *f,const Record *r) {
    char value[64]; format_ticks(r->current,value);
    if(fprintf(f,"%zu,%.4f,%s,%.4f",r->point,r->elapsed,value,r->voltage)<0) return fail("CSV 数据写入失败");
    return 1;
}
static int close_csv(FILE *f,int ok) {
    if(fflush(f)!=0) ok=fail("CSV 刷新失败");
    if(fclose(f)!=0) ok=fail("CSV 关闭失败");
    return ok;
}
static int csv_save(const wchar_t *path,const Plan *p,const Record *records,size_t count) {
    FILE *f; int ok=1;
    if(count<=p->turn) {
        f=_wfopen(path,L"ab"); if(!f) return fail("CSV 追加打开失败");
        ok=csv_record(f,&records[count-1]);
        if(ok && fprintf(f,"%s\r\n",p->count>p->turn?",,,,":"")<0) ok=fail("CSV 追加失败");
        return close_csv(f,ok);
    }
    /* Return columns require a full snapshot in an adjacent temporary file. */
    wchar_t temporary[PATH_CAP];
    if(swprintf(temporary,PATH_CAP,L"%ls.%lu.tmp",path,GetCurrentProcessId())<0) return fail("CSV 临时路径过长");
    f=exclusive_file(temporary); if(!f) return 0;
    ok=csv_header(f,1);
    for(size_t i=0;ok && i<p->turn;++i) {
        ok=csv_record(f,&records[i]);
        if(ok && i+p->turn<count) {
            if(fputc(',',f)==EOF) ok=fail("CSV 写入失败");
            if(ok) ok=csv_record(f,&records[i+p->turn]);
        } else if(ok && fputs(",,,,",f)==EOF) ok=fail("CSV 写入失败");
        if(ok && fputs("\r\n",f)==EOF) ok=fail("CSV 写入失败");
    }
    ok=close_csv(f,ok);
    if(ok && !MoveFileExW(temporary,path,MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)) ok=winfail("CSV 原子替换失败，保留上次结果");
    if(!ok) DeleteFileW(temporary);
    return ok;
}
static int shutdown_output(Instrument *d) {
    /* Writes remain permitted after loss of response synchronization or Ctrl+C. */
    if(!device_write(d,":OUTP OFF")) return 0;
    if(!d->synced || interrupted) return fail("已发送关闭命令，通信中断导致无法确认输出状态，请在面板确认关闭");
    if(!device_error(d) || !expect(d,":OUTP?","0")) return 0;
    puts("输出已关闭"); return 1;
}
static int run_sweep(Instrument *d,const Options *o,const Plan *p) {
    wchar_t path[PATH_CAP]; FILE *f; Record *records; int ok=0; LARGE_INTEGER start,freq,now;
    if(!absolute_path(o->csv,path)) return 0;
    records=calloc(p->count,sizeof(*records)); if(!records) return fail("记录内存分配失败");
    f=exclusive_file(path);
    if(!f) {free(records); return 0;}
    ok=csv_header(f,p->count>p->turn); ok=close_csv(f,ok);
    if(!ok) {free(records); return 0;}
    if(!configure(d,p)) goto cleanup;
    QueryPerformanceFrequency(&freq); QueryPerformanceCounter(&start);
    for(size_t i=0;i<p->count;++i) {
        char value[64]; Record *r=&records[i];
        if(!set_current(d,p->points[i])) goto cleanup;
        if(i==0 && (!checked_write(d,":OUTP ON") || !expect(d,":OUTP?","1"))) goto cleanup;
        if(!wait_dwell(o->dwell) || !measure(d,&r->voltage)) goto cleanup;
        QueryPerformanceCounter(&now);
        r->point=i+1; r->elapsed=(double)(now.QuadPart-start.QuadPart)/(double)freq.QuadPart;
        r->current=p->points[i];
        if(!csv_save(path,p,records,i+1)) goto cleanup;
        GS610_RECORD(r,p);
        format_ticks(r->current,value);
        printf("[%zu/%zu] 设定: %s A | 电压: %.4f V\n",i+1,p->count,value,r->voltage); fflush(stdout);
    }
    ok=1;
    goto finish;
cleanup:
    ok=0;
finish:
    if(!(ok && o->keep && !interrupted)) {
        char saved[sizeof(error_text)]; strcpy(saved,error_text);
        if(!shutdown_output(d)) {
            fprintf(stderr,"关闭输出未确认: %s\n",error_text);
            if(ok) ok=0; else strcpy(error_text,saved);
        }
    }
    if(ok && o->keep) puts("扫描成功，按要求保持最终电流输出");
    free(records); return ok;
}
static int check_identity(Instrument *d) {
    char response[RESPONSE_CAP],copy[RESPONSE_CAP], *first,*second,*comma;
    if(!query(d,"*IDN?",response)) return 0;
    printf("设备: %s\n",response); strcpy(copy,response);
    first=strchr(copy,','); if(!first) return fail("设备型号不是 GS610 (765501)");
    second=first+1; comma=strchr(second,','); if(comma) *comma=0; second=trim(second);
    if(_stricmp(second,"765501") && _stricmp(second,"GS610")) return fail("设备型号不是 GS610 (765501)");
    return 1;
}
int wmain(int argc,wchar_t **wargv) {
    Options o={0}; Plan p={0}; Instrument d={INVALID_HANDLE_VALUE,0,NULL,0};
    char **argv=calloc((size_t)argc,sizeof(*argv)); int ok=0,parsed;
    SetConsoleOutputCP(CP_UTF8); SetConsoleCP(CP_UTF8); SetConsoleCtrlHandler(ctrl_handler,TRUE);
    o.baud=9600; o.bits=8; o.stops=1; o.parity='N'; o.timeout=5; o.dwell=0.1; o.round_trip=-1;
    strcpy(o.start,"-3.2"); strcpy(o.stop,"3.2"); strcpy(o.step,"0.08"); strcpy(o.limit,"12");
    strcpy(o.flow,"none"); strcpy(o.term,"LF");
    if(!argv) {fail("参数内存分配失败"); goto finish;}
    for(int i=0;i<argc;++i) {
        int n=WideCharToMultiByte(CP_UTF8,0,wargv[i],-1,NULL,0,NULL,NULL);
        if(!n || !(argv[i]=malloc((size_t)n))) {fail("参数转换失败"); goto finish;}
        if(!from_wide(wargv[i],argv[i],n)) goto finish;
    }
    if(!init_paths(&o)) goto finish;
    if(argc==1) o.interactive=1;
    parsed=parse_options(argc,argv,&o); if(parsed==2) {ok=1; goto finish;} if(!parsed) goto finish;
    if(!o.list && !o.identify && !o.dry && !create_result_dir()) goto finish;
    if(o.interactive) {
        if(o.dry && !create_result_dir()) goto finish;
        if(!interactive(&o)) goto finish;
    }
    if(!valid_settings(&o)) goto finish;
    if(o.list) {ok=list_ports(); goto finish;}
    if(!o.identify) {
        if(o.round_trip<0 && !prompt_yes("是否双程扫描（y/n）",&o.round_trip)) goto finish;
        if(!make_plan(&o,&p)) goto finish;
        char first[64],last[64],range[64]; format_ticks(p.points[0],first);
        format_ticks(p.points[p.turn-1],last); format_ticks(p.range->nominal,range);
        printf("%s A -> %s A%s，共 %zu 点，固定量程 %s A，限压 ±%g V\n",first,last,o.round_trip?" -> 初始电流":"",p.count,range,p.limit);
        if(o.dry) {
            for(size_t i=0;i<p.count;++i) {format_ticks(p.points[i],first); printf("%s A\n",first);}
            ok=1; goto finish;
        }
    }
    if(!*o.port) {fail("请用 --port COM3 指定串口；--list 可列出串口"); goto finish;}
    if(!serial_open(&d,&o) || !check_identity(&d)) goto finish;
    ok=o.identify?1:run_sweep(&d,&o,&p);
finish:
    if(d.handle!=INVALID_HANDLE_VALUE) CloseHandle(d.handle);
    free(p.points);
    if(argv) {for(int i=0;i<argc;++i) free(argv[i]); free(argv);}
    if(!ok) fprintf(stderr,"运行失败: %s\n",*error_text?error_text:"未知错误");
    return interrupted?130:ok?0:1;
}
