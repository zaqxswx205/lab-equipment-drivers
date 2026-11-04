#include "model.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <math.h>
#include <ctype.h>
static const char *names[] = {"直流电压 DCV","交流电压 ACV","直流电流 DCI","交流电流 ACI","两线电阻 Ω2","四线电阻 Ω4"};
static const wchar_t *wide_names[] = {L"直流电压 DCV",L"交流电压 ACV",L"直流电流 DCI",L"交流电流 ACI",L"两线电阻 Ω2",L"四线电阻 Ω4"};
static const char *commands[] = {"VOLT:DC","VOLT:AC","CURR:DC","CURR:AC","RES","FRES"};
static const char *units[] = {"V","V","A","A","Ohm","Ohm"};
const char *mode_name(MeasureMode m) { return m>=0 && m<MODE_COUNT ? names[m]:"?"; }
const wchar_t *mode_name_w(MeasureMode m) { return m>=0 && m<MODE_COUNT ? wide_names[m]:L"?"; }
const char *mode_scpi(MeasureMode m) { return m>=0 && m<MODE_COUNT ? commands[m]:""; }
const char *mode_unit(MeasureMode m) { return m>=0 && m<MODE_COUNT ? units[m]:"?"; }
int channel_number(int i) { return i>=0 && i<MAX_CHANNELS ? (i/42+1)*100+i%42+1:-1; }
int channel_index(int n) { int slot=n/100, ch=n%100; return (slot==1 || slot==2) && ch>=1 && ch<=42 ? (slot-1)*42+ch-1:-1; }
void settings_defaults(Settings *s) {
    int i; memset(s,0,sizeof(*s)); wcscpy_s(s->port,64,L"COM1");
    s->baud=9600; s->timeout=10; s->interval=1; s->model=7702;
    for(i=0;i<MAX_CHANNELS;i++) {
        ChannelConfig *c=&s->channels[i]; c->channel=channel_number(i); c->enabled=(i==0 || i==1);
        c->mode=i%42>=40 ? MODE_DCI : MODE_DCV; c->auto_range=1; c->nplc=1; c->settle=0.5;
    }
    s->channels[0].mode=MODE_RES;
    s->front=s->channels[1]; s->front.channel=0; s->front.enabled=1;
}
int channel_valid(int model,const ChannelConfig *c) {
    int count=model==7702 ? 40:20, local=c->channel%100;
    if(model!=7700 && model!=7702) return 0;
    if(channel_index(c->channel)<0 || c->mode<0 || c->mode>=MODE_COUNT) return 0;
    if(c->mode==MODE_DCI || c->mode==MODE_ACI) return local==count+1 || local==count+2;
    return local>=1 && local<=(c->mode==MODE_FRES ? count/2:count);
}
int config_validate(int model,const ChannelConfig *c,char *e,size_t cap) {
    if(c->mode<0 || c->mode>=MODE_COUNT) { snprintf(e,cap,"测量功能无效"); return 0; }
    if(model && !channel_valid(model,c)) { snprintf(e,cap,"通道 %d 不支持 %d 的 %s",c->channel,model,mode_name(c->mode)); return 0; }
    if(!isfinite(c->nplc) || c->nplc<0.01 || c->nplc>10) { snprintf(e,cap,"通道 %d 的 NPLC 必须为 0.01–10",c->channel); return 0; }
    if(!isfinite(c->settle) || c->settle<0 || c->settle>60) { snprintf(e,cap,"通道 %d 的稳定时间必须为 0–60 秒",c->channel); return 0; }
    if(!c->auto_range && (!isfinite(c->range) || c->range<=0)) { snprintf(e,cap,"量程必须为 AUTO 或正数"); return 0; }
    return 1;
}
int settings_validate(const Settings *s,char *e,size_t cap) {
    int i,count=0,baud_ok=0; const int baud[]={300,600,1200,2400,4800,9600,19200};
    if(s->model!=0 && s->model!=7700 && s->model!=7702) { snprintf(e,cap,"模块型号无效"); return 0; }
    for(i=0;i<7;i++) if(s->baud==baud[i]) baud_ok=1;
    if(!baud_ok) { snprintf(e,cap,"波特率不在仪器支持的范围内"); return 0; }
    if(!s->simulation && !s->port[0]) { snprintf(e,cap,"请输入串口名称"); return 0; }
    if(!isfinite(s->timeout) || s->timeout<1 || s->timeout>3600) { snprintf(e,cap,"超时必须为 1–3600 秒"); return 0; }
    if(!isfinite(s->interval) || s->interval<0.05 || s->interval>86400) { snprintf(e,cap,"每轮间隔必须为 0.05–86400 秒"); return 0; }
    if(!s->model) return config_validate(0,&s->front,e,cap);
    for(i=0;i<MAX_CHANNELS;i++) if(s->channels[i].enabled) {
        const ChannelConfig *c=&s->channels[i]; int pair;
        count++;
        if(c->channel!=channel_number(i)) { snprintf(e,cap,"通道编号不一致"); return 0; }
        if(!config_validate(s->model,c,e,cap)) return 0;
        pair=channel_index(c->channel+(s->model==7702?20:10));
        if(c->mode==MODE_FRES && pair>=0 && s->channels[pair].enabled) {
            snprintf(e,cap,"四线通道 %d 占用感测通道 %d，请禁用后者",c->channel,s->channels[pair].channel); return 0;
        }
    }
    if(!count) { snprintf(e,cap,"请至少启用一个通道"); return 0; }
    return 1;
}
int parse_number(const char *text,double *v) {
    char *end; double result;
    if(!text) return 0;
    result=strtod(text,&end); if(end==text) return 0;
    while(isspace((unsigned char)*end)) end++;
    if(*end || !isfinite(result)) return 0;
    *v=result; return 1;
}
int parse_reading(const char *text,int expected,double *value,int *valid,char *e,size_t cap) {
    char *end; double v=strtod(text,&end); long ch;
    if(end==text) goto malformed;
    while(isspace((unsigned char)*end)) end++;
    if(expected) {
        char *finish;
        if(*end++!=',') goto malformed;
        ch=strtol(end,&finish,10); if(finish==end) goto malformed;
        while(isspace((unsigned char)*finish)) finish++;
        if(*finish) goto malformed;
        if(ch!=expected) { snprintf(e,cap,"通道不匹配：期望 %d，返回 %ld",expected,ch); return 0; }
    } else if(*end) goto malformed;
    *valid=isfinite(v) && fabs(v)<9e37; *value=*valid?v:0; return 1;
malformed: snprintf(e,cap,"仪器读数格式异常：%.160s",text); return 0;
}
