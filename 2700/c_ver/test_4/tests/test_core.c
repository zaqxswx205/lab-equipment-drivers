#include "acquisition.h"
#include "plot.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <share.h>
static int checks,failures;
#define CHECK(condition) do {checks++;if(!(condition)) {printf("FAIL line %d: %s\n",__LINE__,#condition);failures++;}} while(0)
typedef struct {
    char rx[4096],commands[1024][256],options[64];size_t length,offset;
    int count,closed,front,channel,wrong_channel,overload,pending_error,inject_error,has_input,unsafe_change;
    MeasureMode mode;double nplc[MODE_COUNT],range[MODE_COUNT];int auto_range[MODE_COUNT];
    const Settings *expected;int bad_parameters;
} Fake;
static void reply(Fake *f,const char *s) {
    size_t n=strlen(s);if(f->offset==f->length) f->offset=f->length=0;
    CHECK(f->length+n+2<sizeof(f->rx));memcpy(f->rx+f->length,s,n);f->length+=n;f->rx[f->length++]='\r';f->rx[f->length++]='\n';
}
static int fake_write(void *ctx,const char *data,size_t n,char *e,size_t cap) {
    Fake *f=ctx;char cmd[256],response[256];size_t i;int m;
    (void)e;(void)cap;
    if(n==1 && data[0]==3) {f->offset=f->length=0;return 1;}
    CHECK(n>0 && data[n-1]=='\r');if(!n || n>=sizeof(cmd)) return 0;
    memcpy(cmd,data,n-1);cmd[n-1]=0;for(i=0;i<n-1;i++) CHECK((unsigned char)cmd[i]>=32 && (unsigned char)cmd[i]<=126);
    if(f->count<1024) strcpy_s(f->commands[f->count++],256,cmd);
    if(!strcmp(cmd,"*IDN?")) reply(f,"KEITHLEY INSTRUMENTS INC.,MODEL 2700,12345,B10 /A02");
    else if(!strcmp(cmd,"*OPT?")) reply(f,f->options);
    else if(!strcmp(cmd,"*OPC?")) reply(f,"1");
    else if(!strcmp(cmd,"SYST:FRSW?")) {snprintf(response,sizeof(response),"%d",f->front);reply(f,response);}
    else if(!strcmp(cmd,"SYST:ERR?")) {reply(f,f->pending_error?"-221,\"Settings conflict\"":"0,\"No error\"");f->pending_error=0;}
    else if(!strcmp(cmd,"ROUT:OPEN:ALL")) {f->has_input=0;f->channel=0;}
    else if(!strncmp(cmd,"SENS:FUNC '",11)) {
        if(f->has_input) f->unsafe_change++;
        for(m=0;m<MODE_COUNT;m++) {char fn[64];snprintf(fn,sizeof(fn),"SENS:FUNC '%s'",mode_scpi((MeasureMode)m));if(!strcmp(cmd,fn)) f->mode=(MeasureMode)m;}
    } else if(sscanf_s(cmd,"ROUT:CLOS (@%d)",&m)==1) {f->channel=m;f->has_input=1;}
    else if(!strcmp(cmd,"READ?")) {
        double v=(f->mode==MODE_RES || f->mode==MODE_FRES)?1000:(f->mode==MODE_DCI || f->mode==MODE_ACI)?0.001:2;
        if(f->expected) {
            const ChannelConfig *c=f->expected->model?&f->expected->channels[channel_index(f->channel)]:&f->expected->front;
            if(c->mode!=f->mode || c->nplc!=f->nplc[c->mode] || c->auto_range!=f->auto_range[c->mode] || (!c->auto_range && c->range!=f->range[c->mode])) f->bad_parameters++;
        }
        if(f->overload) v=9.9e37;
        if(f->expected && !f->expected->model) snprintf(response,sizeof(response),"%.12g",v);
        else snprintf(response,sizeof(response),"%.12g,%d",v,f->channel+f->wrong_channel);
        reply(f,response);
    }
    for(m=0;m<MODE_COUNT;m++) {
        char prefix[64];double v;
        snprintf(prefix,sizeof(prefix),"SENS:%s:NPLC ",mode_scpi((MeasureMode)m));
        if(!strncmp(cmd,prefix,strlen(prefix)) && parse_number(cmd+strlen(prefix),&v)) {f->nplc[m]=v;if(f->inject_error) f->pending_error=1;}
        snprintf(prefix,sizeof(prefix),"SENS:%s:RANG ",mode_scpi((MeasureMode)m));if(!strncmp(cmd,prefix,strlen(prefix)) && parse_number(cmd+strlen(prefix),&v)) f->range[m]=v;
        snprintf(prefix,sizeof(prefix),"SENS:%s:RANG:AUTO ",mode_scpi((MeasureMode)m));if(!strncmp(cmd,prefix,strlen(prefix))) f->auto_range[m]=!strcmp(cmd+strlen(prefix),"ON");
    }
    return 1;
}
static int fake_read(void *ctx,char *byte,char *e,size_t cap) {Fake *f=ctx;(void)e;(void)cap;if(f->offset<f->length) {*byte=f->rx[f->offset++];return 1;}return 0;}
static void fake_close(void *ctx) {((Fake *)ctx)->closed=1;}
static SerialOps fake_ops(void) {SerialOps ops={fake_write,fake_read,fake_close};return ops;}
static void fake_init(Fake *f,const Settings *s) {memset(f,0,sizeof(*f));strcpy_s(f->options,64,"7702,NONE");f->expected=s;f->front=s && !s->model?1:0;}
static int contains(Fake *f,const char *cmd) {int i;for(i=0;i<f->count;i++) if(!strcmp(f->commands[i],cmd)) return 1;return 0;}
static wchar_t folder[PATH_CAP];
static void write_test_file(const wchar_t *path,const char *text) {FILE *f=NULL;CHECK(!_wfopen_s(&f,path,L"wb"));if(f) {fwrite(text,1,strlen(text),f);fclose(f);}}
static char *read_test_file(const wchar_t *path) {FILE *f=NULL;long size;char *text; f=_wfsopen(path,L"rb",_SH_DENYNO);if(!f) return NULL;fseek(f,0,SEEK_END);size=ftell(f);rewind(f);text=calloc((size_t)size+1,1);if(text) fread(text,1,(size_t)size,f);fclose(f);return text;}
static void test_model(void) {
    Settings s;char e[ERROR_CAP];double value;int valid;
    settings_defaults(&s);CHECK(settings_validate(&s,e,sizeof(e)));CHECK(s.channels[0].mode==MODE_RES);CHECK(s.channels[1].mode==MODE_DCV);CHECK(s.channels[0].settle==0.5);
    CHECK(channel_index(242)==83);CHECK(channel_number(42)==201);CHECK(channel_index(143)==-1);
    s.channels[0].mode=MODE_FRES;s.channels[20].enabled=1;CHECK(!settings_validate(&s,e,sizeof(e)));CHECK(strstr(e,"121")!=NULL);
    s.channels[20].enabled=0;CHECK(settings_validate(&s,e,sizeof(e)));
    s.channels[1].mode=MODE_DCI;CHECK(!settings_validate(&s,e,sizeof(e)));s.channels[1].mode=MODE_DCV;
    s.channels[1].nplc=NAN;CHECK(!settings_validate(&s,e,sizeof(e)));s.channels[1].nplc=1;
    s.channels[1].settle=-1;CHECK(!settings_validate(&s,e,sizeof(e)));s.channels[1].settle=0.5;
    CHECK(parse_reading("+1.23e-3,101",101,&value,&valid,e,sizeof(e)) && valid && fabs(value-0.00123)<1e-10);
    CHECK(!parse_reading("1,102",101,&value,&valid,e,sizeof(e)));
    CHECK(!parse_reading("bad,101",101,&value,&valid,e,sizeof(e)));
    CHECK(!parse_reading("1,101junk",101,&value,&valid,e,sizeof(e)));
    CHECK(parse_reading("9.9e37,101",101,&value,&valid,e,sizeof(e)) && !valid);
    CHECK(parse_reading("NAN",0,&value,&valid,e,sizeof(e)) && !valid);
    CHECK(!parse_number("1x",&value));CHECK(!parse_number("nan",&value));CHECK(parse_number(" 0.5 ",&value) && value==0.5);
    s.model=7700;s.channels[0].mode=MODE_RES;CHECK(settings_validate(&s,e,sizeof(e)));
}
static void test_protocol(void) {
    Settings s;Instrument d;Fake *f=calloc(1,sizeof(*f));char e[ERROR_CAP],response[64];Sample sample={0};HANDLE stop=CreateEvent(NULL,TRUE,FALSE,NULL);int i;
    settings_defaults(&s);for(i=0;i<MAX_CHANNELS;i++) s.channels[i].enabled=0;
    {int numbers[]={101,102,103,104,141,142};MeasureMode modes[]={MODE_RES,MODE_DCV,MODE_FRES,MODE_ACV,MODE_DCI,MODE_ACI};
     for(i=0;i<6;i++) {ChannelConfig *c=&s.channels[channel_index(numbers[i])];c->enabled=1;c->mode=modes[i];c->settle=0;c->auto_range=i==2;c->range=i+1;c->nplc=i%2?0.1:5;}}
    fake_init(f,&s);instrument_init(&d,&s,stop);serial_attach(&d.port,fake_ops(),f);
    CHECK(instrument_open(&d,e,sizeof(e))==1);CHECK(strstr(d.modules,"7702")!=NULL);CHECK(instrument_configure(&d,e,sizeof(e))==1);
    for(i=0;i<MAX_CHANNELS;i++) if(s.channels[i].enabled) {CHECK(instrument_measure(&d,&s.channels[i],&sample,e,sizeof(e))==1);CHECK(sample.valid);}
    CHECK(!f->bad_parameters);CHECK(!f->unsafe_change);CHECK(contains(f,"SENS:VOLT:AC:DET:BAND 300"));CHECK(contains(f,"SENS:CURR:AC:DET:BAND 300"));
    f->wrong_channel=1;CHECK(instrument_measure(&d,&s.channels[0],&sample,e,sizeof(e))==0);f->wrong_channel=0;
    f->overload=1;CHECK(instrument_measure(&d,&s.channels[0],&sample,e,sizeof(e))==1 && !sample.valid);f->overload=0;
    CHECK(!serial_write(&d.port,"BAD\n",e,sizeof(e)));
    d.port.timeout=0.02;CHECK(serial_query(&d.port,"NO_REPLY?",response,sizeof(response),e,sizeof(e))==0);CHECK(d.port.pending);
    SetEvent(stop);CHECK(serial_query(&d.port,"READ?",response,sizeof(response),e,sizeof(e))==K_CANCEL);
    CHECK(instrument_close(&d,e,sizeof(e))==1);CHECK(f->closed);CHECK(contains(f,"ROUT:OPEN:ALL"));CloseHandle(stop);free(f);
}
static void test_failures(void) {
    Settings s;Instrument d;Fake *f=calloc(1,sizeof(*f));char e[ERROR_CAP];Sample sample={0};
    settings_defaults(&s);fake_init(f,&s);strcpy_s(f->options,64,"NONE,NONE");instrument_init(&d,&s,NULL);serial_attach(&d.port,fake_ops(),f);
    CHECK(instrument_open(&d,e,sizeof(e))==0);CHECK(strstr(e,"NONE")!=NULL);CHECK(!contains(f,"*RST"));instrument_close(&d,e,sizeof(e));
    fake_init(f,&s);strcpy_s(f->options,64,"NONE,7702");instrument_init(&d,&s,NULL);serial_attach(&d.port,fake_ops(),f);CHECK(instrument_open(&d,e,sizeof(e))==0);instrument_close(&d,e,sizeof(e));
    fake_init(f,&s);f->front=1;instrument_init(&d,&s,NULL);serial_attach(&d.port,fake_ops(),f);CHECK(instrument_open(&d,e,sizeof(e))==0);CHECK(strstr(e,"FRONT/REAR")!=NULL);instrument_close(&d,e,sizeof(e));
    fake_init(f,&s);f->pending_error=1;instrument_init(&d,&s,NULL);serial_attach(&d.port,fake_ops(),f);CHECK(instrument_open(&d,e,sizeof(e))==1);CHECK(d.history[0]);CHECK(instrument_configure(&d,e,sizeof(e))==1);
    f->inject_error=1;CHECK(instrument_measure(&d,&s.channels[0],&sample,e,sizeof(e))==0);CHECK(!contains(f,"ROUT:CLOS (@101)"));instrument_close(&d,e,sizeof(e));free(f);
}
typedef struct {int samples,errors,done,saved_before_emit;wchar_t path[PATH_CAP];} Collector;
static int collect_event(void *ctx,Event *event) {
    Collector *c=ctx;
    if(event->kind==EVENT_FILE) wcscpy_s(c->path,PATH_CAP,event->path);
    if(event->kind==EVENT_SAMPLE) {char *text=read_test_file(c->path);int lines=0;char *p=text;c->samples++;if(text) {for(;*p;p++) if(*p=='\n') lines++;if(lines==c->samples+1) c->saved_before_emit++;else printf("readback lines=%d samples=%d text=%s\n",lines,c->samples,text);free(text);}}
    if(event->kind==EVENT_ERROR) {c->errors++;printf("Worker error: %s\n",event->text);}
    if(event->kind==EVENT_DONE) c->done++;free(event);return 1;
}
static void test_pipeline(void) {
    AcquisitionJob job={0};Collector c={0};Fake *f=calloc(1,sizeof(*f));char *text;
    settings_defaults(&job.settings);wcscpy_s(job.settings.folder,PATH_CAP,folder);job.settings.interval=0.05;
    job.settings.channels[0].settle=0;job.settings.channels[1].settle=0;
    job.settings.channels[0].auto_range=0;job.settings.channels[0].range=10000;job.settings.channels[0].nplc=5;
    job.settings.channels[1].auto_range=0;job.settings.channels[1].range=10;job.settings.channels[1].nplc=0.1;
    fake_init(f,&job.settings);job.serial_ops=fake_ops();job.serial_context=f;job.emit=collect_event;job.context=&c;job.max_rounds=2;
    CHECK(acquisition_thread(&job)==0);CHECK(c.samples==4 && c.errors==0 && c.done==1);CHECK(c.saved_before_emit==4);CHECK(f->closed && !f->bad_parameters);
    text=read_test_file(c.path);CHECK(text && strstr(text,"timestamp\tchannel\tmode\tunit\tvalue\tstatus\r\n"));CHECK(text && !strstr(text,"raw") && !strstr(text,"source") && !strstr(text,"settle_s"));if(text) free(text);
    memset(&c,0,sizeof(c));job.settings.simulation=1;job.serial_ops=(SerialOps){0};Sleep(2);CHECK(acquisition_thread(&job)==0);CHECK(c.samples==4 && !c.errors);free(f);
}
static void test_files(void) {
    wchar_t src[PATH_CAP],out[PATH_CAP],bad[PATH_CAP];char e[ERROR_CAP],*text;uint64_t n;HANDLE stop=CreateEvent(NULL,TRUE,FALSE,NULL);DataFile data={INVALID_HANDLE_VALUE};Sample sample={0};
    path_join(src,PATH_CAP,folder,L"数据_测试.txt");path_join(out,PATH_CAP,folder,L"数据_测试.csv");path_join(bad,PATH_CAP,folder,L"partial.csv");
    sample.channel=101;sample.mode=MODE_RES;sample.valid=1;sample.value=1234;strcpy_s(sample.timestamp,32,"2026-10-27T12:00:00.123");
    CHECK(data_open(&data,src,e,sizeof(e)));CHECK(data_append(&data,&sample,e,sizeof(e)));data_close(&data);CHECK(!data_open(&data,src,e,sizeof(e)));
    CHECK(csv_convert(src,out,NULL,&n,e,sizeof(e))==1 && n==1);text=read_test_file(out);CHECK(text && !memcmp(text,"\xef\xbb\xbf",3));CHECK(text && strstr(text,"status\r\n\"2026"));CHECK(text && !strstr(text,"raw"));if(text) free(text);
    CHECK(!csv_convert(src,out,NULL,&n,e,sizeof(e)));CHECK(!csv_convert(src,src,NULL,&n,e,sizeof(e)));
    path_join(src,PATH_CAP,folder,L"old.txt");path_join(out,PATH_CAP,folder,L"old.csv");
    write_test_file(src,"timestamp\tmode\tunit\tvalue\tstatus\traw\tsource\nnow\tDCV\tV\t1\tOK\t1\tSIMULATION\n");CHECK(csv_convert(src,out,NULL,&n,e,sizeof(e))==1 && n==1);text=read_test_file(out);CHECK(text && strstr(text,"\"FRONT\""));if(text) free(text);
    write_test_file(src,"timestamp\tchannel\tmode\tunit\tvalue\tstatus\traw\tsource\trange_setting\tnplc_setting\tsettle_s\nnow\t101\tRES\tOhm\t1\tOK\t1\tSIMULATION\tAUTO\t1\t0.5\n");CHECK(csv_convert(src,bad,NULL,&n,e,sizeof(e))==1 && n==1);text=read_test_file(bad);CHECK(text && !strstr(text,"SIMULATION"));if(text) free(text);DeleteFileW(bad);
    write_test_file(src,"timestamp\tchannel\tmode\tunit\tvalue\tstatus\nnow\t101\n");CHECK(!csv_convert(src,bad,NULL,&n,e,sizeof(e)));CHECK(GetFileAttributesW(bad)==INVALID_FILE_ATTRIBUTES);
    write_test_file(src,"timestamp\tchannel\tmode\tunit\tvalue\tstatus\nnow\t101\t\"a,b\"\tV\t1\tOK\n");CHECK(csv_convert(src,bad,NULL,&n,e,sizeof(e))==1);text=read_test_file(bad);CHECK(text && strstr(text,"\"a,b\""));if(text) free(text);DeleteFileW(bad);
    SetEvent(stop);CHECK(csv_convert(src,bad,stop,&n,e,sizeof(e))==K_CANCEL);CHECK(GetFileAttributesW(bad)==INVALID_FILE_ATTRIBUTES);CloseHandle(stop);
}
static void test_plot(void) {
    PlotData *data=calloc(1,sizeof(*data));Sample sample={0};int i;sample.channel=101;sample.mode=MODE_RES;sample.valid=1;
    for(i=0;i<2005;i++) {sample.elapsed=i;sample.value=i;plot_add(data,&sample);}
    CHECK(data->size[0]==2000);CHECK(data->head[0]==5);CHECK(data->points[0][5].value==5);plot_reset(data);CHECK(!data->used[0]);free(data);
}
int wmain(void) {
    wchar_t base[PATH_CAP],name[256];char e[ERROR_CAP];GetTempPathW(PATH_CAP,base);unique_name(name,256,L"KeithleyNativeTests",L"");path_join(folder,PATH_CAP,base,name);
    if(!directory_create(folder,e,sizeof(e))) {printf("%s\n",e);return 1;}
    test_model();test_protocol();test_failures();test_pipeline();test_files();test_plot();
    printf("%d checks, %d failures\n",checks,failures);return failures?1:0;
}
