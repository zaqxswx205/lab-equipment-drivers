#include "instrument.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#define TRY(call) do { int status_=(call); if(status_!=K_OK) return status_; } while(0)
void instrument_init(Instrument *d,const Settings *s,HANDLE stop) { memset(d,0,sizeof(*d)); d->settings=s; serial_init(&d->port,stop,s->timeout); }
static int error_queue(Instrument *d,int history,char *e,size_t cap) {
    int i; char reply[512];
    for(i=0;i<32;i++) {
        char *end; long code;
        TRY(serial_query(&d->port,"SYST:ERR?",reply,sizeof(reply),e,cap));
        code=strtol(reply,&end,10);
        if(end==reply || *end!=',') { snprintf(e,cap,"错误队列响应异常：%.200s",reply); return 0; }
        if(!code) return 1;
        if(!history) { snprintf(e,cap,"仪器报告错误：%.400s",reply); return 0; }
        serial_trace(&d->port,"EXISTING_ERROR",reply);
        if(strlen(d->history)+strlen(reply)+4<sizeof(d->history)) { if(d->history[0]) strcat_s(d->history,sizeof(d->history),"; "); strcat_s(d->history,sizeof(d->history),reply); }
    }
    snprintf(e,cap,"错误队列持续产生错误，无法建立通信基线"); return 0;
}
static int checked(Instrument *d,const char *command,char *e,size_t cap) {
    char reply[64],detail[ERROR_CAP]; int status;
    if(stop_requested(d->port.stop)) return K_CANCEL;
    TRY(serial_write(&d->port,command,e,cap));
    TRY(serial_query(&d->port,"*OPC?",reply,sizeof(reply),e,cap));
    if(strcmp(reply,"1")) { snprintf(e,cap,"仪器未确认完成：%s",command); return 0; }
    status=error_queue(d,0,detail,sizeof(detail));
    if(status==0) snprintf(e,cap,"执行 %s 后：%s",command,detail);
    return status;
}
static int check_input(Instrument *d,char *e,size_t cap) {
    char reply[64]; double position;
    TRY(serial_query(&d->port,"SYST:FRSW?",reply,sizeof(reply),e,cap));
    if(!parse_number(reply,&position) || position!=(d->settings->model?0:1)) {
        snprintf(e,cap,"请将 FRONT/REAR 实体输入开关切至 %s，再重新开始",d->settings->model?"R（模块/后面板）":"F（前面板）"); return 0;
    } return 1;
}
int instrument_open(Instrument *d,char *e,size_t cap) {
    char identity[256],reply[256],models[2][32]={{0}}; char *ctx,*token; int n=0,i;
    TRY(serial_open(&d->port,d->settings,e,cap));
    TRY(serial_write(&d->port,"SYST:REM",e,cap));
    TRY(serial_query(&d->port,"*IDN?",identity,sizeof(identity),e,cap));
    strcpy_s(d->identity,sizeof(d->identity),identity);
    token=strtok_s(identity,",",&ctx); token=token?strtok_s(NULL,",",&ctx):NULL;
    if(token) { while(*token==' ') token++; }
    if(!token || (_stricmp(token,"MODEL 2700") && _stricmp(token,"2700"))) { snprintf(e,cap,"连接设备不是 Keithley 2700：%.240s",d->identity); return 0; }
    TRY(error_queue(d,1,e,cap)); TRY(check_input(d,e,cap));
    if(d->settings->model) {
        TRY(serial_query(&d->port,"*OPT?",reply,sizeof(reply),e,cap));
        token=strtok_s(reply,",",&ctx);
        while(token && n<2) { size_t j=0,k; for(k=0;token[k] && j<31;k++) if(!isspace((unsigned char)token[k]) && token[k]!='"') models[n][j++]=token[k]; models[n++][j]=0; token=strtok_s(NULL,",",&ctx); }
        snprintf(d->modules,sizeof(d->modules),"槽位1=%s；槽位2=%s",models[0][0]?models[0]:"未知",models[1][0]?models[1]:"未知");
        for(i=0;i<MAX_CHANNELS;i++) if(d->settings->channels[i].enabled) {
            int slot=d->settings->channels[i].channel/100; char expected[16]; snprintf(expected,sizeof(expected),"%d",d->settings->model);
            if(strcmp(models[slot-1],expected)) { snprintf(e,cap,"所选槽位 %d 未识别到 %d。%s。请按实际型号、槽位选择通道；若均为 NONE，请检查模块安装。",slot,d->settings->model,d->modules); return 0; }
        }
    }
    return error_queue(d,0,e,cap);
}
int instrument_configure(Instrument *d,char *e,size_t cap) {
    const char *commands[]={"ABOR","*RST","*CLS","INIT:CONT OFF","ROUT:SCAN:LSEL NONE","ROUT:CLOS:ACON OFF","ROUT:OPEN:ALL","TRIG:SOUR IMM","TRIG:COUN 1","SAMP:COUN 1","FORM:DATA ASC"}; size_t i;
    for(i=0;i<sizeof(commands)/sizeof(commands[0]);i++) {
        if(!d->settings->model && i>=4 && i<=6) continue;
        TRY(checked(d,commands[i],e,cap));
    }
    d->has_cache=0;
    return checked(d,d->settings->model?"FORM:ELEM READ,CHAN":"FORM:ELEM READ",e,cap);
}
static int apply(Instrument *d,const ChannelConfig *c,char *e,size_t cap) {
    char command[256]; const char *function=mode_scpi(c->mode);
    if(d->has_cache && d->cache.mode==c->mode && d->cache.auto_range==c->auto_range && (c->auto_range || d->cache.range==c->range) && d->cache.nplc==c->nplc) return 1;
    snprintf(command,sizeof(command),"SENS:FUNC '%s'",function); TRY(checked(d,command,e,cap));
    snprintf(command,sizeof(command),"SENS:%s:RANG:AUTO %s",function,c->auto_range?"ON":"OFF"); TRY(checked(d,command,e,cap));
    if(!c->auto_range) { snprintf(command,sizeof(command),"SENS:%s:RANG %.12g",function,c->range); TRY(checked(d,command,e,cap)); }
    if(c->mode==MODE_ACV || c->mode==MODE_ACI) { snprintf(command,sizeof(command),"SENS:%s:DET:BAND 300",function); TRY(checked(d,command,e,cap)); }
    snprintf(command,sizeof(command),"SENS:%s:NPLC %.12g",function,c->nplc); TRY(checked(d,command,e,cap));
    d->cache=*c; d->has_cache=1; return 1;
}
int instrument_measure(Instrument *d,const ChannelConfig *c,Sample *sample,char *e,size_t cap) {
    char command[64],reply[512];
    TRY(check_input(d,e,cap));
    if(d->settings->model) TRY(checked(d,"ROUT:OPEN:ALL",e,cap));
    TRY(apply(d,c,e,cap));
    if(d->settings->model) {
        snprintf(command,sizeof(command),"ROUT:CLOS (@%d)",c->channel); TRY(checked(d,command,e,cap));
        TRY(wait_seconds(d->port.stop,c->settle));
    }
    TRY(serial_query(&d->port,"READ?",reply,sizeof(reply),e,cap));
    if(!parse_reading(reply,d->settings->model?c->channel:0,&sample->value,&sample->valid,e,cap)) return 0;
    return error_queue(d,0,e,cap);
}
int instrument_close(Instrument *d,char *e,size_t cap) {
    int ok=1; char detail[ERROR_CAP]; const char *commands[]={"ABOR","ROUT:SCAN:LSEL NONE","ROUT:OPEN:ALL","SYST:LOC"}; int i;
    if(!d->port.attached) return 1;
    if(d->port.pending) { if(!serial_raw(&d->port,"\x03",1,detail,sizeof(detail))) { ok=0; snprintf(e,cap,"中断查询失败：%s",detail); } Sleep(100); }
    for(i=0;i<4;i++) {
        if(!d->settings->model && (i==1 || i==2)) continue;
        if(!serial_write(&d->port,commands[i],detail,sizeof(detail))) { ok=0; snprintf(e,cap,"关闭仪器通信时：%s",detail); }
    }
    serial_close(&d->port); return ok;
}
