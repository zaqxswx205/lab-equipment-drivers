#include "app.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
static void console_log(const char *s){puts(s);fflush(stdout);}
static BOOL WINAPI stop_handler(DWORD type){if(type==CTRL_C_EVENT||type==CTRL_BREAK_EVENT){InterlockedExchange(&cancelled,1);return TRUE;}return FALSE;}
static int parse_num(const char *s,double *v){char *e;*v=strtod(s,&e);return e!=s&&!*e&&isfinite(*v);}
int selftest(void){Config c;Plan *p=malloc(sizeof(*p));char e[256];int ok=1;if(!p)return 1;
#define CHECK(x) do{if(!(x)){printf("FAIL line %d: %s\n",__LINE__,#x);ok=0;}}while(0)
 defaults(&c);CHECK(make_plan(&c,p,e,sizeof(e))&&p->n==81&&p->range==8);c.start=-.5;c.stop=.5;CHECK(make_plan(&c,p,e,sizeof(e))&&p->n==14&&p->p[12]==4600000000LL&&p->p[13]==5000000000LL);c.start=.5;c.stop=-.5;CHECK(make_plan(&c,p,e,sizeof(e))&&p->p[12]==-4600000000LL&&p->p[13]==-5000000000LL);c.start=c.stop=0;CHECK(make_plan(&c,p,e,sizeof(e))&&p->n==1);c.step=0;CHECK(!make_plan(&c,p,e,sizeof(e)));defaults(&c);c.start=NAN;CHECK(!make_plan(&c,p,e,sizeof(e)));defaults(&c);c.limit=13;CHECK(!make_plan(&c,p,e,sizeof(e)));defaults(&c);c.start=.2000001;c.stop=.2;c.step=.0000001;CHECK(!make_plan(&c,p,e,sizeof(e)));defaults(&c);c.step=.00001;CHECK(!make_plan(&c,p,e,sizeof(e)));defaults(&c);c.lower=0;CHECK(!make_plan(&c,p,e,sizeof(e)));free(p);puts(ok?"All planning tests passed":"Tests failed");return ok?0:1;
}
static void help(void){puts("GS610 current source + Keithley 2700 FRONT DC voltage (native C)\nNo arguments: graphical interface. Options:\n --port2700 COM3 --port610 COM4\n --start A --stop A --step A --voltage-limit V\n --linear (GS610 Linear/INT) --period seconds (default 2)\n --round-trip --samples N --settle seconds --interval seconds\n --limits --lower-voltage V --upper-voltage V\n --baud2700 N --baud610 N --flow2700\n --flow610 none|xon|rtscts --term610 LF|CR|CRLF\n --nplc N --range2700 AUTO|V --csv path\n --scan --dry-run --self-test --help\nCSV is exclusive creation. Ctrl+C requests stop and output OFF.");}
int main(int argc,char **argv){Config c;int i;double v;defaults(&c);SetConsoleCtrlHandler(stop_handler,TRUE);if(argc==1)return gui();
 for(i=1;i<argc;i++){const char *k=argv[i],*a;if(!strcmp(k,"--help")){help();return 0;}if(!strcmp(k,"--self-test"))return selftest();
#define FLAG(key,field) if(!strcmp(k,key)){c.field=1;continue;}
 FLAG("--round-trip",round_trip) FLAG("--dry-run",dry_run) FLAG("--linear",linear) FLAG("--limits",limits) FLAG("--scan",scan) FLAG("--flow2700",flow2700)
 if(i+1>=argc){fprintf(stderr,"Missing value: %s\n",k);return 2;}a=argv[++i];
#define STR(key,field) if(!strcmp(k,key)){if(strlen(a)>=sizeof(c.field)){fprintf(stderr,"Value too long: %s\n",k);return 2;}strcpy(c.field,a);continue;}
 STR("--port2700",port2700) STR("--port610",port610) STR("--csv",csv) STR("--term610",term610) STR("--flow610",flow610) STR("--range2700",range2700)
 if(!parse_num(a,&v)){fprintf(stderr,"Invalid number: %s\n",a);return 2;}
#define NUM(key,field) if(!strcmp(k,key)){c.field=v;continue;}
 NUM("--start",start) NUM("--stop",stop) NUM("--step",step) NUM("--voltage-limit",limit) NUM("--period",period) NUM("--settle",settle) NUM("--interval",interval) NUM("--nplc",nplc) NUM("--lower-voltage",lower) NUM("--upper-voltage",upper)
 if(!strcmp(k,"--samples")||!strcmp(k,"--baud2700")||!strcmp(k,"--baud610")){if(v<1||v>10000000||v!=floor(v)){fputs("Invalid integer\n",stderr);return 2;}if(!strcmp(k,"--samples"))c.samples=(int)v;else if(!strcmp(k,"--baud2700"))c.baud2700=(int)v;else c.baud610=(int)v;continue;}
 fprintf(stderr,"Unknown option: %s\n",k);return 2;
 }
 if(strcmp(c.term610,"LF")&&strcmp(c.term610,"CR")&&strcmp(c.term610,"CRLF")){fputs("Invalid term610\n",stderr);return 2;}if(strcmp(c.flow610,"none")&&strcmp(c.flow610,"xon")&&strcmp(c.flow610,"rtscts")){fputs("Invalid flow610\n",stderr);return 2;}
 if(_stricmp(c.range2700,"AUTO")&&(!parse_num(c.range2700,&v)||v<=0)){fputs("Invalid meter range\n",stderr);return 2;}
 return run(&c,console_log,NULL);
}
