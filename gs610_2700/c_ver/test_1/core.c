#include "app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <stdarg.h>
#include <io.h>
#include <fcntl.h>
#include <sys/stat.h>
volatile LONG cancelled;
static const double ranges[]={.00002,.0002,.002,.02,.2,.5,1,2,3};
static const double capacity[]={.0000205,.000205,.00205,.0205,.205,.5,1,2,3.2};
static const int64_t resolution[]={1,10,100,1000,10000,100000,100000,100000,100000};
static const double maxv[]={110,110,110,110,110,110,60,30,12};
void defaults(Config *c){ memset(c,0,sizeof(*c));strcpy(c->term610,"LF");strcpy(c->flow610,"none");strcpy(c->range2700,"AUTO");c->bits610=8;c->stop610=1;c->parity610='N';c->baud2700=c->baud610=9600;c->samples=1;c->start=-3.2;c->stop=3.2;c->step=.08;c->limit=12;c->period=2;c->settle=c->interval=c->nplc=1;c->lower=-2;c->upper=2; }
static int fail(char *e,size_t n,const char *s){snprintf(e,n,"%s",s);return 0;}
int make_plan(const Config *c,Plan *p,char *e,size_t n){
 double vals[]={c->start,c->stop,c->step,c->limit,c->period,c->settle,c->interval,c->nplc,c->lower,c->upper};int i;int64_t a,b,s,d,k,peak;
 for(i=0;i<10;i++)if(!isfinite(vals[i]))return fail(e,n,"All parameters must be finite.");
 if(fabs(c->start)>3.2||fabs(c->stop)>3.2||c->step<=0||c->step>6.4)return fail(e,n,"Current must be within +/-3.2 A; step must be positive and <=6.4 A.");
 if(c->samples<1||c->settle<0||c->interval<0||c->period<=0||c->period>3600||c->nplc<.01||c->nplc>10||c->lower>=0||c->upper<=0)return fail(e,n,"Invalid samples, timing, NPLC or thresholds.");
 a=llround(c->start*SCALE);b=llround(c->stop*SCALE);s=llround(c->step*SCALE);
 if(s<1||fabs(c->start-a/(double)SCALE)>1e-12||fabs(c->stop-b/(double)SCALE)>1e-12||fabs(c->step-s/(double)SCALE)>1e-12)return fail(e,n,"Current precision exceeds 0.1 nA.");
 d=llabs(b-a);k=d/s+1+(d%s!=0);if(k*(c->round_trip?2:1)>MAX_POINTS)return fail(e,n,"More than 100000 points.");
 p->n=0;for(i=0;i<(int)(d/s+1);i++)p->p[p->n++]=a+(b>=a?1:-1)*s*i;if(p->p[p->n-1]!=b)p->p[p->n++]=b;
 peak=llabs(a)>llabs(b)?llabs(a):llabs(b);for(p->range=0;p->range<8&&peak>llround(capacity[p->range]*SCALE);p->range++);
 for(i=0;i<p->n;i++)if(p->p[i]%resolution[p->range])return fail(e,n,"Point violates source range resolution.");
 if(c->limit<.001||c->limit>maxv[p->range])return fail(e,n,"Voltage limit outside selected range capability.");
 if(c->limits&&c->limit>12)return fail(e,n,"Limit search uses 3 A range: voltage limit must be <=12 V.");
 return 1;
}
typedef struct{HANDLE h;const char *term;int sync,meter;} Serial;
typedef struct{Serial src,meter;LogFn log;RecordFn record;Config c;FILE *file;char error[512];} Session;
static double now(void){LARGE_INTEGER f,t;QueryPerformanceFrequency(&f);QueryPerformanceCounter(&t);return (double)t.QuadPart/f.QuadPart;}
static void say(Session *s,const char *fmt,...){char b[1024];va_list a;va_start(a,fmt);vsnprintf(b,sizeof(b),fmt,a);va_end(a);if(s->log)s->log(b);}
static int error(Session *s,const char *fmt,...){va_list a;va_start(a,fmt);vsnprintf(s->error,sizeof(s->error),fmt,a);va_end(a);return 0;}
static int wait_until(double target){while(now()<target){if(cancelled)return 0;Sleep(5);}return !cancelled;}
static int validport(const char *p){char *end;long v;if(_strnicmp(p,"COM",3))return 0;v=strtol(p+3,&end,10);return v>0&&!*end;}
static int open_serial(Session *s,Serial *p,const char *name,int baud,const char *flow,const char *term,int meter){
 char path[64];DCB d={0};COMMTIMEOUTS t={0};if(!validport(name))return error(s,"Invalid serial port: %s",name);snprintf(path,sizeof(path),"\\\\.\\%s",name);
 p->h=CreateFileA(path,GENERIC_READ|GENERIC_WRITE,0,NULL,OPEN_EXISTING,0,NULL);if(p->h==INVALID_HANDLE_VALUE)return error(s,"Cannot open %s (Windows %lu)",name,GetLastError());
 d.DCBlength=sizeof(d);if(!GetCommState(p->h,&d))return error(s,"GetCommState failed");d.BaudRate=baud;d.ByteSize=(BYTE)(meter?8:s->c.bits610);d.Parity=meter?NOPARITY:s->c.parity610=='E'?EVENPARITY:s->c.parity610=='O'?ODDPARITY:NOPARITY;d.StopBits=!meter&&s->c.stop610==2?TWOSTOPBITS:ONESTOPBIT;d.fBinary=1;d.fParity=d.Parity!=NOPARITY;d.fOutxDsrFlow=0;d.fDsrSensitivity=0;d.fDtrControl=DTR_CONTROL_ENABLE;d.fOutxCtsFlow=!strcmp(flow,"rtscts");d.fRtsControl=d.fOutxCtsFlow?RTS_CONTROL_HANDSHAKE:RTS_CONTROL_ENABLE;d.fInX=d.fOutX=!strcmp(flow,"xon");d.XonChar=17;d.XoffChar=19;d.XonLim=d.XoffLim=128;d.fAbortOnError=0;
 t.ReadIntervalTimeout=MAXDWORD;t.ReadTotalTimeoutConstant=50;t.WriteTotalTimeoutConstant=1000;
 if(!SetCommState(p->h,&d)||!SetCommTimeouts(p->h,&t))return error(s,"Cannot configure serial port");PurgeComm(p->h,PURGE_RXCLEAR|PURGE_TXCLEAR);p->term=term;p->sync=1;p->meter=meter;return 1;
}
static int write_cmd(Session *s,Serial *p,const char *cmd){char b[512];DWORD n;int len=snprintf(b,sizeof(b),"%s%s",cmd,p->term);if(len<=0||len>=(int)sizeof(b)||!WriteFile(p->h,b,len,&n,NULL)||n!=(DWORD)len){p->sync=0;return error(s,"Serial write failed: %s",cmd);}return 1;}
static int query(Session *s,Serial *p,const char *cmd,char *out,size_t cap){double deadline=now()+(p->meter?10:5);size_t n=0;DWORD got;char ch;int matched=0;if(!p->sync)return error(s,"Serial response lost synchronization; reconnect required.");if(!write_cmd(s,p,cmd))return 0;
 while(now()<deadline){if(cancelled)break;if(!ReadFile(p->h,&ch,1,&got,NULL))break;if(!got)continue;
 if(p->meter){if(ch=='\r'||ch=='\n'){if(n){out[n]=0;return 1;}continue;}}
 else {if(ch==p->term[matched]){matched++;if(!p->term[matched]){out[n]=0;return n?1:error(s,"Empty response: %s",cmd);}continue;}if(matched){if(n+1>=cap)break;out[n++]=p->term[0];matched=0;}}
 if(n+1>=cap||(unsigned char)ch>127)break;out[n++]=ch;
 }p->sync=0;return error(s,"Timeout/incomplete response: %s",cmd);
}
static int numeric(Session *s,const char *b,double *v){char *end;*v=strtod(b,&end);while(*end==' '||*end=='\r'||*end=='\n')end++;return end!=b&&!*end&&isfinite(*v)?1:error(s,"Invalid numeric response: %s",b);}
static int qnum(Session *s,Serial *p,const char *cmd,double *v){char b[4096];return query(s,p,cmd,b,sizeof(b))&&numeric(s,b,v);}
static int expect(Session *s,Serial *p,const char *cmd,const char *expected){char b[4096];return query(s,p,cmd,b,sizeof(b))&&(!_stricmp(b,expected)||error(s,"%s returned %s (expected %s)",cmd,b,expected));}
static int errors(Session *s,Serial *p,int baseline){char b[4096],*end;long code;int i;for(i=0;i<32;i++){if(!query(s,p,p->meter?"SYST:ERR?":":SYST:ERR?",b,sizeof(b)))return 0;code=strtol(b,&end,10);if(end==b||*end!=',')return error(s,"Invalid error response: %s",b);if(!code)return 1;if(baseline||(!p->meter&&code==301)){say(s,"Instrument warning: %s",b);continue;}return error(s,"Instrument error: %s",b);}return error(s,"Error queue did not clear");}
static int checked(Session *s,Serial *p,const char *cmd){return write_cmd(s,p,cmd)&&(!p->meter||expect(s,p,"*OPC?","1"))&&errors(s,p,0);}
static int setnum(Session *s,Serial *p,const char *cmd,double v){char b[256];snprintf(b,sizeof(b),"%s %.10g",cmd,v);return checked(s,p,b);}
static int verify_num(Session *s,Serial *p,const char *cmd,double expected,double tolerance){double v;return qnum(s,p,cmd,&v)&&(fabs(v-expected)<=tolerance||error(s,"Readback mismatch: %s",cmd));}
static int current(Session *s,double v){return setnum(s,&s->src,":SOUR:CURR:LEV",v)&&verify_num(s,&s->src,":SOUR:CURR:LEV?",v,1e-12);}
static int output(Session *s,int on){return checked(s,&s->src,on?":OUTP ON":":OUTP OFF")&&expect(s,&s->src,":OUTP?",on?"1":"0");}
static int identity(Session *s,Serial *p,int meter){char b[4096],copy[4096],*model,*end;if(!query(s,p,"*IDN?",b,sizeof(b)))return 0;say(s,"%s: %s",meter?"2700":"GS610",b);strcpy(copy,b);model=strchr(copy,',');if(!model)return error(s,"Invalid identity");model++;while(*model==' ')model++;end=strchr(model,',');if(end)*end=0;end=model+strlen(model);while(end>model&&end[-1]==' ')*--end=0;return (meter?(!_stricmp(model,"2700")||!_stricmp(model,"MODEL 2700")):(!_stricmp(model,"GS610")||!_stricmp(model,"765501")))||error(s,"Unexpected instrument model");}
static int configure(Session *s,int range){
 const char *mc[]={"ABOR","*RST","*CLS","INIT:CONT OFF","CONF:VOLT:DC",NULL};
 const char *sc[]={":SOUR:FUNC CURR",":SOUR:SHAP DC",":SOUR:MODE FIX",":TRIG:SOUR TIM",":SOUR:DEL MIN",":SOUR:VOLT:PROT:STAT ON",":SOUR:VOLT:PROT:LINK ON",":SENS:ACH OFF",":SENS:FUNC VOLT",":SENS:RSEN OFF",":SENS:STAT ON",":SENS:RANG:AUTO ON",":SENS:ITIM PLC",":SENS:DEL MIN",":SENS:AVER:STAT OFF",":CALC:MATH:STAT OFF",":CALC:NULL:STAT OFF",NULL};int i;double upper,lower;
 if(!write_cmd(s,&s->meter,"SYST:REM")||!identity(s,&s->meter,1)||!errors(s,&s->meter,1)||!expect(s,&s->meter,"SYST:FRSW?","1"))return 0;
 for(i=0;mc[i];i++)if(!checked(s,&s->meter,mc[i]))return 0;
 if(!checked(s,&s->meter,!_stricmp(s->c.range2700,"AUTO")?"SENS:VOLT:DC:RANG:AUTO ON":"SENS:VOLT:DC:RANG:AUTO OFF"))return 0;
 if(_stricmp(s->c.range2700,"AUTO")&&!setnum(s,&s->meter,"SENS:VOLT:DC:RANG",atof(s->c.range2700)))return 0;
 if(!setnum(s,&s->meter,"SENS:VOLT:DC:NPLC",s->c.nplc))return 0;
 {const char *tail[]={"TRIG:SOUR IMM","TRIG:COUN 1","SAMP:COUN 1","FORM:DATA ASC","FORM:ELEM READ",NULL};for(i=0;tail[i];i++)if(!checked(s,&s->meter,tail[i]))return 0;}
 if(!identity(s,&s->src,0)||!write_cmd(s,&s->src,":SYST:REM")||!output(s,0))return 0;
 for(i=0;sc[i];i++)if(!checked(s,&s->src,sc[i]))return 0;
 if(!setnum(s,&s->src,":SOUR:CURR:RANG",ranges[range])||!setnum(s,&s->src,":SOUR:VOLT:PROT:ULIM",s->c.limit)||!expect(s,&s->src,":SENS:RSEN?","0")||!expect(s,&s->src,":SOUR:VOLT:PROT:STAT?","1")||!qnum(s,&s->src,":SOUR:VOLT:PROT:ULIM?",&upper)||!qnum(s,&s->src,":SOUR:VOLT:PROT:LLIM?",&lower))return 0;
 if(upper<=0||lower>=0||fmax(upper,fabs(lower))>s->c.limit+1e-12)return error(s,"Actual voltage protection exceeds requested limit");
 return expect(s,&s->src,":SENS:FUNC?","VOLT");
}
static int measure(Session *s,double *v,double *drive){double status;if(!expect(s,&s->meter,"SYST:FRSW?","1")||!qnum(s,&s->meter,"READ?",v)||!errors(s,&s->meter,0))return 0;if(fabs(*v)>=9e37)return error(s,"2700 overload");if(!qnum(s,&s->src,":FETC?",drive)||!errors(s,&s->src,0)||!qnum(s,&s->src,":STAT:SENS:COND?",&status))return 0;return (!( (int)status&32)&&fabs(*drive)<9e37)||error(s,"GS610 voltage overload");}
static int record_row(Session *s,int leg,int point,double current_v,double v,double drive){SYSTEMTIME t;GetLocalTime(&t);if(fprintf(s->file,"%04u-%02u-%02u %02u:%02u:%02u,%s,%d,%.10g,%.12g,%.12g\n",t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,leg==0?"outbound":leg==1?"return":leg==2?"negative_limit":"positive_limit",point,current_v,drive,v)<0||fflush(s->file)||_commit(_fileno(s->file)))return error(s,"CSV write/flush failed");say(s,"[%d:%d] %.10g A | drive %.9g V | meter %.9g V",leg,point,current_v,drive,v);if(s->record)s->record(leg,point,current_v,v,drive);return 1;}
static int linear_config(Session *s,int64_t *p,int n){double step=n>1?llabs(p[1]-p[0])/(double)SCALE:1e-10,status,deadline;const char *cmd[]={":SOUR:MODE SWE",":SOUR:CURR:SWE:SPAC LIN",":SWE:COUN 1",":SWE:LAST KEEP",":TRAC:STAT OFF",":TRAC:AUTO ON",":TRAC:MONL ON",":SENS:DEL MIN",NULL};int i;
 if(n>65535)return error(s,"Linear segment exceeds 65535 points");for(i=0;cmd[i];i++)if(!checked(s,&s->src,cmd[i]))return 0;
 if(!setnum(s,&s->src,":SOUR:CURR:SWE:STAR",p[0]/(double)SCALE)||!setnum(s,&s->src,":SOUR:CURR:SWE:STOP",p[n-1]/(double)SCALE)||!setnum(s,&s->src,":SOUR:CURR:SWE:STEP",step)||!setnum(s,&s->src,":TRIG:TIM",s->c.period))return 0;
 if(!expect(s,&s->src,":SOUR:MODE?","SWE")||!expect(s,&s->src,":SOUR:CURR:SWE:SPAC?","LIN")||!expect(s,&s->src,":TRIG:SOUR?","TIM")||!verify_num(s,&s->src,":TRIG:TIM?",s->c.period,1e-6)||!verify_num(s,&s->src,":SOUR:CURR:SWE:STAR?",p[0]/(double)SCALE,1e-12)||!verify_num(s,&s->src,":SOUR:CURR:SWE:STOP?",p[n-1]/(double)SCALE,1e-12)||!verify_num(s,&s->src,":SOUR:CURR:SWE:STEP?",step,1e-12))return 0;
 deadline=now()+5;do{if(!qnum(s,&s->src,":STAT:SOUR:COND?",&status))return 0;if((int)status&32)return 1;if(!errors(s,&s->src,0)||!wait_until(now()+.02))return 0;}while(now()<deadline);return error(s,"Linear ready timeout");}
static int segment(Session *s,int64_t *p,int n,int leg,int offset){double anchor,target,v,drive,status,count,deadline;int i;
 if(!current(s,p[0]/(double)SCALE)||!linear_config(s,p,n)||!output(s,1)||!qnum(s,&s->src,":STAT:SOUR:EVEN?",&status))return 0;
 anchor=now();if(!write_cmd(s,&s->src,":SWE:TRIG"))return 0;
 for(i=0;i<n;i++){target=anchor+(i+.5)*s->c.period;if(!wait_until(target))return 0;if(now()-target>.2)return error(s,"Sampling scheduling delay exceeded 0.2 s");if(!measure(s,&v,&drive)||!record_row(s,leg,offset+i+1,p[i]/(double)SCALE,v,drive))return 0;if(now()>=anchor+(i+1)*s->c.period)return error(s,"Reading crossed next current point: increase period");}
 if(!wait_until(anchor+n*s->c.period)||!output(s,0))return 0;deadline=now()+3;
 do{if(!errors(s,&s->src,0)||!qnum(s,&s->src,":STAT:SOUR:EVEN?",&status))return 0;if((int)status&128)return error(s,"GS610 sampling error");if(!qnum(s,&s->src,":TRAC:ACT?",&count))return 0;if(count>=n)return checked(s,&s->src,":TRAC:STAT OFF");if(!wait_until(now()+.05))return 0;}while(now()<deadline);return error(s,"GS610 stored point count timeout");}
static int sweep(Session *s,Plan *p){int leg,i,j,n=p->n;double v,drive;for(leg=0;leg<(s->c.round_trip?2:1);leg++){if(leg)for(i=0;i<n/2;i++){int64_t t=p->p[i];p->p[i]=p->p[n-i-1];p->p[n-i-1]=t;}
 if(s->c.linear){int first=0,last=n;if(n>=3){int64_t d=p->p[1]-p->p[0];if(p->p[n-1]-p->p[n-2]!=d){if(leg)first=1;else last=n-1;}}if(first&&!segment(s,p->p,1,leg,0))return 0;if(!segment(s,p->p+first,last-first,leg,first))return 0;if(last<n&&!segment(s,p->p+last,1,leg,last))return 0;}
 else for(i=0;i<n;i++){if(cancelled||!current(s,p->p[i]/(double)SCALE)||(i==0&&leg==0&&!output(s,1))||!wait_until(now()+s->c.settle))return 0;for(j=0;j<s->c.samples;j++){if(!measure(s,&v,&drive)||!record_row(s,leg,i+1,p->p[i]/(double)SCALE,v,drive))return 0;if(j+1<s->c.samples&&!wait_until(now()+s->c.interval))return 0;}}}return 1;}
static int find_limits(Session *s){double limits[2],v,drive,anchor,current_value;int side,i,found;for(side=0;side<2;side++){found=0;for(i=0;i<=40;i++){current_value=(side?1:-1)*i*.08;if(cancelled||!current(s,current_value)||(i==0&&!output(s,1)))return 0;anchor=now();if(!wait_until(anchor+s->c.period/2)||!measure(s,&v,&drive)||!record_row(s,side+2,i+1,current_value,v,drive))return 0;if(i>0&&(side?v>s->c.upper:v<s->c.lower)){limits[side]=current_value;found=1;break;}if(!wait_until(anchor+s->c.period))return 0;}if(!found)return error(s,"No threshold crossing before +/-3.2 A");if(!current(s,0))return 0;}
 if(!output(s,0))return 0;{Config c=s->c;Plan *p=malloc(sizeof(*p));int ok;if(!p)return error(s,"Out of memory");c.start=limits[0];c.stop=limits[1];c.step=.08;c.round_trip=0;ok=make_plan(&c,p,s->error,sizeof(s->error))&&linear_config(s,p->p,p->n)&&current(s,0);free(p);if(!ok)return 0;}
 say(s,"LIMITS %.10g %.10g",limits[0],limits[1]);return 1;}
static int scan(Session *s){char name[32],target[512],b[4096];int i,k;for(i=1;i<=256&&!cancelled;i++){snprintf(name,sizeof(name),"COM%d",i);if(!QueryDosDeviceA(name,target,sizeof(target)))continue;for(k=0;k<2;k++){Serial p={INVALID_HANDLE_VALUE};if(open_serial(s,&p,name,k?s->c.baud610:s->c.baud2700,k?s->c.flow610:(s->c.flow2700?"xon":"none"),"\r\n",1)&&query(s,&p,"*IDN?",b,sizeof(b))){say(s,"%s | %s | baud %d",name,b,k?s->c.baud610:s->c.baud2700);CloseHandle(p.h);break;}if(p.h!=INVALID_HANDLE_VALUE)CloseHandle(p.h);} }return !cancelled;}
int run(const Config *c,LogFn log,RecordFn record){Session s={0};Plan *p=malloc(sizeof(*p));int ok=0,fd;char path[MAX_PATH],exe[MAX_PATH],*slash;SYSTEMTIME t;s.c=*c;s.log=log;s.record=record;s.src.h=s.meter.h=INVALID_HANDLE_VALUE;
 if(!p)return 1;if(c->scan){ok=scan(&s);goto cleanup;}if(!make_plan(c,p,s.error,sizeof(s.error)))goto cleanup;say(&s,"%d points per leg; source range %.10g A; limit +/-%.10g V",p->n,ranges[p->range],c->limit);
 if(c->dry_run){int i;for(i=0;i<p->n;i++)say(&s,"%.10g",p->p[i]/(double)SCALE);if(c->round_trip)for(i=p->n-1;i>=0;i--)say(&s,"%.10g",p->p[i]/(double)SCALE);ok=1;goto cleanup;}
 if(c->baud2700!=300&&c->baud2700!=600&&c->baud2700!=1200&&c->baud2700!=2400&&c->baud2700!=4800&&c->baud2700!=9600&&c->baud2700!=19200){error(&s,"Unsupported 2700 baud rate");goto cleanup;}
 if(!validport(c->port2700)||!validport(c->port610)||!_stricmp(c->port2700,c->port610)){error(&s,"Select two different COM ports");goto cleanup;}
 if(*c->csv)snprintf(path,sizeof(path),"%s",c->csv);else{GetModuleFileNameA(NULL,exe,sizeof(exe));slash=strrchr(exe,'\\');if(slash)*slash=0;snprintf(path,sizeof(path),"%s\\result",exe);CreateDirectoryA(path,NULL);GetLocalTime(&t);snprintf(path,sizeof(path),"%s\\result\\%04u%02u%02u-%02u%02u%02u-%03u.csv",exe,t.wYear,t.wMonth,t.wDay,t.wHour,t.wMinute,t.wSecond,t.wMilliseconds);}
 fd=_open(path,_O_WRONLY|_O_CREAT|_O_EXCL|_O_BINARY,_S_IREAD|_S_IWRITE);if(fd<0){error(&s,"Cannot create CSV (existing files are never overwritten): %s",path);goto cleanup;}s.file=_fdopen(fd,"wb");if(!s.file){_close(fd);error(&s,"Cannot open CSV stream");goto cleanup;}fputs("\xEF\xBB\xBFtimestamp,direction,point,set_current_A,drive_voltage_V,voltage_V\n",s.file);if(fflush(s.file)||_commit(fd)){error(&s,"Cannot write CSV header");goto cleanup;}say(&s,"CSV: %s",path);
 if(!open_serial(&s,&s.meter,c->port2700,c->baud2700,c->flow2700?"xon":"none","\r",1)||!open_serial(&s,&s.src,c->port610,c->baud610,c->flow610,!strcmp(c->term610,"CR")?"\r":!strcmp(c->term610,"CRLF")?"\r\n":"\n",0)||!configure(&s,c->limits?8:p->range))goto cleanup;
 ok=c->limits?find_limits(&s):sweep(&s,p);
cleanup:
 if(s.src.h!=INVALID_HANDLE_VALUE){char saved[512];LONG stop=cancelled;strcpy(saved,s.error);InterlockedExchange(&cancelled,0);if(!write_cmd(&s,&s.src,":OUTP OFF")||!expect(&s,&s.src,":OUTP?","0")){say(&s,"OUTPUT OFF UNCONFIRMED: immediately check GS610 front panel.");ok=0;}else say(&s,"GS610 output OFF confirmed");strcpy(s.error,saved);InterlockedExchange(&cancelled,stop);CloseHandle(s.src.h);}
 if(s.meter.h!=INVALID_HANDLE_VALUE){if(!s.meter.sync){DWORD n;WriteFile(s.meter.h,"\003",1,&n,NULL);}write_cmd(&s,&s.meter,"ABOR");write_cmd(&s,&s.meter,"SYST:LOC");CloseHandle(s.meter.h);}if(s.file&&fclose(s.file)){say(&s,"CSV close failed");ok=0;}if(*s.error&&!ok)say(&s,"ERROR: %s",s.error);say(&s,cancelled?"Cancelled":ok?"Completed":"Stopped with error");free(p);return ok?0:cancelled?130:1;
}
