#ifndef APP_H
#define APP_H
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#define MAX_POINTS 100000
#define SCALE 10000000000LL
typedef struct { char port2700[32],port610[32],term610[8],flow610[16],csv[MAX_PATH],range2700[32]; char parity610; int bits610,stop610; int baud2700,baud610,flow2700,round_trip,samples,dry_run,linear,limits,scan; double start,stop,step,limit,period,settle,interval,nplc,lower,upper; } Config;
typedef struct { int64_t p[MAX_POINTS]; int n,range; } Plan;
typedef void (*LogFn)(const char *);
typedef void (*RecordFn)(int,int,double,double,double);
extern volatile LONG cancelled;
void defaults(Config *c);
int make_plan(const Config *c,Plan *p,char *err,size_t size);
int run(const Config *c,LogFn log,RecordFn record);
int gui(void);
int selftest(void);
#endif
