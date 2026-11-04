/* Run with tests/run_tests.bat. Serial calls are replaced at the OS boundary;
 * no physical port is opened and no hardware receives commands.
 */
#define _CRT_SECURE_NO_WARNINGS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
static BOOL WINAPI mock_write(HANDLE,LPCVOID,DWORD,LPDWORD,LPOVERLAPPED);
static BOOL WINAPI mock_read(HANDLE,LPVOID,DWORD,LPDWORD,LPOVERLAPPED);
#define WriteFile mock_write
#define ReadFile mock_read
#define wmain application_main
#include "../gs610_sweep.c"
#undef wmain
#undef ReadFile
#undef WriteFile

static char pending[4096], level[128]="0";
static int output_on, off_count, on_count, reads, overflow_read, timeout_read, clamp_level;
static int fail_config, cancel_read, fail_second_read, partial_read;
static BOOL WINAPI mock_write(HANDLE h,LPCVOID data,DWORD len,LPDWORD wrote,LPOVERLAPPED ov) {
    char command[512]; (void)h; (void)ov;
    assert(len<sizeof(command)); memcpy(command,data,len); command[len]=0;
    char *end=strpbrk(command,"\r\n"); if(end) *end=0;
    *wrote=len; pending[0]=0;
    if(!strcmp(command,":OUTP OFF")) {output_on=0; ++off_count;}
    else if(!strcmp(command,":OUTP ON")) {output_on=1; ++on_count;}
    else if(!strcmp(command,":OUTP?")) snprintf(pending,sizeof(pending),"%d\n",output_on);
    else if(!strncmp(command,":SOUR:CURR:LEV ",15)) strcpy(level,command+15);
    else if(!strcmp(command,":SOUR:CURR:LEV?")) snprintf(pending,sizeof(pending),"%s\n",clamp_level?"0.00123":level);
    else if(!strcmp(command,":SOUR:VOLT:PROT:ULIM?")) strcpy(pending,"12\n");
    else if(!strcmp(command,":SOUR:VOLT:PROT:LLIM?")) strcpy(pending,"-12\n");
    else if(!strcmp(command,":SOUR:VOLT:PROT:STAT?")) strcpy(pending,"1\n");
    else if(!strcmp(command,":SENS:FUNC?")) strcpy(pending,"VOLT\n");
    else if(!strcmp(command,":READ?")) {
        ++reads;
        if(cancel_read) InterlockedExchange(&interrupted,1);
        if(partial_read) strcpy(pending,"0.25");
        else if(!timeout_read && !(fail_second_read && reads==2)) snprintf(pending,sizeof(pending),"%.6f\n",reads*0.25);
    }
    else if(!strcmp(command,":STAT:SENS:COND?")) snprintf(pending,sizeof(pending),"%d\n",overflow_read?32:0);
    else if(!strcmp(command,":SYST:ERR?")) strcpy(pending,fail_config?"-100,\"simulated error\"\n":"0,\"No error\"\n");
    else if(!strcmp(command,"*IDN?")) strcpy(pending,"YOKOGAWA,765501,SIM,1.0\n");
    else {
        static const char *allowed[]={":SYST:REM",":SOUR:FUNC CURR",":SOUR:SHAP DC",":SOUR:MODE FIX",
            ":TRIG:SOUR IMM",":SOUR:DEL MIN",":SOUR:VOLT:PROT:STAT ON",":SOUR:VOLT:PROT:LINK ON",
            ":SENS:ACH OFF",":SENS:FUNC VOLT",":SENS:STAT ON",":SENS:RANG:AUTO ON",
            ":SENS:ITIM PLC",":SENS:DEL MIN",":SENS:AVER:STAT OFF",":CALC:MATH:STAT OFF",":CALC:NULL:STAT OFF"};
        int known=0;
        for(size_t i=0;i<sizeof(allowed)/sizeof(allowed[0]);++i) if(!strcmp(command,allowed[i])) known=1;
        if(!strncmp(command,":SOUR:CURR:RANG ",16) || !strncmp(command,":SOUR:VOLT:PROT:ULIM ",21)) known=1;
        if(!known) {fprintf(stderr,"Unknown SCPI: %s\n",command); assert(known);}
    }
    return TRUE;
}
static BOOL WINAPI mock_read(HANDLE h,LPVOID buffer,DWORD size,LPDWORD got,LPOVERLAPPED ov) {
    (void)h; (void)size; (void)ov;
    if(*pending) {*(char*)buffer=pending[0]; memmove(pending,pending+1,strlen(pending)); *got=1;}
    else {*got=0; Sleep(1);}
    return TRUE;
}
static Options options(void) {
    Options o={0}; o.round_trip=1; o.dwell=0;
    strcpy(o.start,"0"); strcpy(o.stop,"0.08"); strcpy(o.step,"0.08"); strcpy(o.limit,"12");
    return o;
}
static void reset_mock(void) {
    output_on=off_count=on_count=reads=overflow_read=timeout_read=clamp_level=0;
    fail_config=cancel_read=fail_second_read=partial_read=0; interrupted=0; error_text[0]=pending[0]=0;
}
static void planner_tests(void) {
    Options o=options(); Plan p; int64_t v;
    assert(ticks("-8.0000e-2",&v) && v==-800000000);
    assert(ticks("1e-10",&v) && v==1);
    assert(!ticks("1e-11",&v)); assert(!ticks("nan",&v)); assert(!ticks("1.2.3",&v));
    assert(make_plan(&o,&p)); assert(p.count==4 && p.turn==2);
    assert(p.points[0]==0 && p.points[1]==800000000 && p.points[2]==800000000 && p.points[3]==0); free(p.points);
    strcpy(o.start,"0.2"); strcpy(o.stop,"-0.2"); strcpy(o.step,"0.15");
    assert(make_plan(&o,&p)); assert(p.count==8 && p.points[3]==-2000000000 && p.points[7]==2000000000); free(p.points);
    strcpy(o.start,"3.2"); strcpy(o.stop,"3.2"); strcpy(o.step,"0.08");
    assert(make_plan(&o,&p)); assert(p.count==2 && p.points[0]==p.points[1]); free(p.points);
    strcpy(o.limit,"12.01"); assert(!make_plan(&o,&p)); strcpy(o.limit,"12");
    strcpy(o.stop,"3.20001"); assert(!make_plan(&o,&p));
    strcpy(o.start,"0"); strcpy(o.stop,"1"); strcpy(o.step,"0.000001"); assert(!make_plan(&o,&p));
    strcpy(o.stop,"0.1"); strcpy(o.step,"0.0000001"); assert(!make_plan(&o,&p));
    strcpy(o.stop,"0.0000205"); strcpy(o.step,"0.0000205");
    assert(make_plan(&o,&p)); assert(p.range==&ranges[0]); free(p.points);
    strcpy(o.stop,"0.0000205001"); assert(!make_plan(&o,&p));
}
static void sweep_tests(void) {
    Options o=options(); Plan p; Instrument d={(HANDLE)1,1,"\n",10}; wchar_t temp[PATH_CAP],folder[PATH_CAP];
    assert(GetTempPathW(PATH_CAP,temp)); assert(GetTempFileNameW(temp,L"gs6",0,folder));
    assert(DeleteFileW(folder)); assert(CreateDirectoryW(folder,NULL)); wcscpy(app_dir,folder);
    assert(make_plan(&o,&p));
    for(int scenario=0;scenario<12;++scenario) {
        wchar_t csv[PATH_CAP]; reset_mock(); d.synced=1; o.keep=scenario==1 || scenario==7;
        snprintf(o.csv,sizeof(o.csv),"case%d.csv",scenario);
        assert(absolute_path(o.csv,csv));
        if(scenario==2) overflow_read=1;
        if(scenario==3) timeout_read=1;
        if(scenario==4) clamp_level=1;
        if(scenario==5) fail_config=1;
        if(scenario==6 || scenario==7) cancel_read=1;
        if(scenario==9) fail_second_read=1;
        if(scenario==10) partial_read=1;
        if(scenario==11) {o.round_trip=0; free(p.points); assert(make_plan(&o,&p));}
        if(scenario==8) { FILE *f=exclusive_file(csv); assert(f); fputs("existing data",f); fclose(f); }
        int ok=run_sweep(&d,&o,&p);
        if(scenario<=1 || scenario==11) {
            assert(ok && reads==(scenario==11?2:4) && on_count==1);
            assert(output_on==(scenario==1));
            FILE *f=_wfopen(csv,L"rb"); assert(f); char data[4096]={0}; assert(fread(data,1,sizeof(data)-1,f)>0); fclose(f);
            if(scenario!=11) {
                assert(strstr(data,"return_measured_voltage_V"));
                assert(strstr(data,",0.2500,3,")); assert(strstr(data,",0.5000,4,"));
            } else assert(!strstr(data,"return_"));
        } else {
            assert(!ok && !output_on);
            if(scenario!=8) assert(off_count>=2);
            else {assert(off_count==0 && on_count==0); FILE *f=_wfopen(csv,L"rb"); assert(f); char data[64]={0}; fread(data,1,63,f); fclose(f); assert(!strcmp(data,"existing data"));}
            if(scenario==3) assert(!d.synced);
            if(scenario==9) {
                FILE *f=_wfopen(csv,L"rb"); assert(f); char data[4096]={0};
                fread(data,1,sizeof(data)-1,f); fclose(f);
                assert(strstr(data,",0.2500,,,,\r\n")); assert(!strstr(data,",0.5000,"));
            }
            if(scenario==10) assert(!d.synced);
        }
        assert(DeleteFileW(csv));
    }
    free(p.points); interrupted=0; assert(RemoveDirectoryW(folder));
}
int main(void) {
    SetConsoleOutputCP(CP_UTF8); planner_tests(); sweep_tests();
    /* Formatting rounds only at output; exact source-current digits are retained. */
    FILE *f=tmpfile(); assert(f);
    Record r={1,0.123456,1.234567,1};
    assert(csv_record(f,&r)); rewind(f);
    char text[128]={0}; assert(fread(text,1,sizeof(text)-1,f)>0); fclose(f);
    assert(!strcmp(text,"1,0.1235,0.0000000001,1.2346"));
    puts("All planner, protocol, CSV, cleanup and interruption tests passed."); return 0;
}
