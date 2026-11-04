#include "acquisition.h"
#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
static void publish(EmitEvent emit,void *ctx,EventKind kind,const char *text,const wchar_t *path,const Sample *sample,uint64_t count) {
    Event *event=calloc(1,sizeof(*event)); if(!event) return;
    event->kind=kind; if(text) strncpy_s(event->text,sizeof(event->text),text,_TRUNCATE);
    if(path) wcsncpy_s(event->path,PATH_CAP,path,_TRUNCATE); if(sample) event->sample=*sample; event->count=count;
    if(!emit || !emit(ctx,event)) free(event);
}
unsigned __stdcall acquisition_thread(void *arg) {
    AcquisitionJob *job=arg; const Settings *s=&job->settings; Instrument device;
    DataFile data={INVALID_HANDLE_VALUE}; FILE *trace=NULL; wchar_t name[256],path[PATH_CAP],logs[PATH_CAP];
    char error[ERROR_CAP]="",cleanup[ERROR_CAP]=""; double start; uint64_t index=0; unsigned rounds=0; int status=1,i;
    instrument_init(&device,s,job->stop);
    if(job->serial_ops.write) serial_attach(&device.port,job->serial_ops,job->serial_context);
    if(!settings_validate(s,error,sizeof(error))) { status=0; goto end; }
    if(s->simulation) publish(job->emit,job->context,EVENT_INFO,"模拟设备（无真实测量）",NULL,NULL,0);
    else {
        if(!directory_create(s->folder,error,sizeof(error))) { status=0; goto end; }
        if(!path_join(logs,PATH_CAP,s->folder,L"logs") || !directory_create(logs,error,sizeof(error))) { status=0; goto end; }
        unique_name(name,256,L"serial",L".log"); if(!path_join(path,PATH_CAP,logs,name)) { snprintf(error,sizeof(error),"日志路径过长"); status=0; goto end; }
        _wfopen_s(&trace,path,L"wb");
        if(!trace) { snprintf(error,sizeof(error),"无法创建通信日志"); status=0; goto end; } device.port.trace=trace;
        status=instrument_open(&device,error,sizeof(error));
        if(device.modules[0]) publish(job->emit,job->context,EVENT_MODULES,device.modules,NULL,NULL,0);
        if(device.history[0]) publish(job->emit,job->context,EVENT_WARNING,device.history,NULL,NULL,0);
        if(status!=1) goto end;
        publish(job->emit,job->context,EVENT_INFO,device.identity,NULL,NULL,0);
    }
    if(job->probe) { publish(job->emit,job->context,EVENT_INFO,s->simulation?"模拟模式测试通过":"连接成功，输入开关及所选模块检查通过",NULL,NULL,0); goto end; }
    if(!s->simulation) { status=instrument_configure(&device,error,sizeof(error)); if(status!=1) goto end; }
    if(!directory_create(s->folder,error,sizeof(error))) { status=0; goto end; }
    unique_name(name,256,s->simulation?L"SIM":L"K2700",L".txt");
    if(!path_join(path,PATH_CAP,s->folder,name)) { snprintf(error,sizeof(error),"数据路径过长"); status=0; goto end; }
    if(!data_open(&data,path,error,sizeof(error))) { status=0; goto end; }
    publish(job->emit,job->context,EVENT_FILE,NULL,path,NULL,0);
    start=monotonic_seconds();
    while(!stop_requested(job->stop)) {
        double tick=monotonic_seconds();
        for(i=0;i<(s->model?MAX_CHANNELS:1);i++) {
            const ChannelConfig *c=s->model?&s->channels[i]:&s->front; Sample sample={0};
            if(!c->enabled) continue;
            if(stop_requested(job->stop)) { status=K_CANCEL; goto end; }
            if(s->simulation) {
                double base=(c->mode==MODE_RES || c->mode==MODE_FRES)?1000:(c->mode==MODE_DCI || c->mode==MODE_ACI)?0.01:1;
                sample.value=base*(1+(c->channel%100)*0.02+0.04*sin((monotonic_seconds()-start)/3+c->channel)); sample.valid=1;
            } else { status=instrument_measure(&device,c,&sample,error,sizeof(error)); if(status!=1) goto end; }
            sample.channel=c->channel; sample.mode=c->mode; sample.index=++index;
            sample.elapsed=monotonic_seconds()-start; local_timestamp(sample.timestamp,sizeof(sample.timestamp));
            if(!data_append(&data,&sample,error,sizeof(error))) { status=0; goto end; }
            publish(job->emit,job->context,EVENT_SAMPLE,NULL,NULL,&sample,0);
        }
        rounds++; if(job->max_rounds && rounds>=job->max_rounds) break;
        { double delay=s->interval-(monotonic_seconds()-tick); if(delay>0 && wait_seconds(job->stop,delay)==K_CANCEL) break; }
    }
end:
    data_close(&data);
    if(!instrument_close(&device,cleanup,sizeof(cleanup))) { if(status!=0) { status=0; strcpy_s(error,sizeof(error),cleanup); } }
    if(trace) fclose(trace);
    if(status==0) publish(job->emit,job->context,EVENT_ERROR,error,NULL,NULL,0);
    publish(job->emit,job->context,EVENT_DONE,NULL,NULL,NULL,index);
    return status==0?1:0;
}
unsigned __stdcall export_thread(void *arg) {
    ExportJob *job=arg; char error[ERROR_CAP]; uint64_t count=0;
    int result=csv_convert(job->source,job->destination,job->stop,&count,error,sizeof(error));
    if(result==1) publish(job->emit,job->context,EVENT_EXPORTED,NULL,job->destination,NULL,count);
    else if(result==0) publish(job->emit,job->context,EVENT_ERROR,error,NULL,NULL,0);
    publish(job->emit,job->context,EVENT_DONE,NULL,NULL,NULL,count); return result==0?1:0;
}
