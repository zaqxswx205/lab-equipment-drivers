/* Native Windows front end. The CLI and GUI share the same instrument engine. */
#define UNICODE
#define _UNICODE
#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
static void publish_record(const void *, const void *);
#define GS610_RECORD(r,p) publish_record(r,p)
#define wmain gs610_cli_main
#include "gs610_sweep.c"
#undef wmain
#include <commctrl.h>
#include <commdlg.h>
#include <setupapi.h>
#include <devguid.h>
#ifdef _MSC_VER
#pragma comment(linker,"/manifestdependency:\"type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'\"")
#endif

#define WM_SAMPLE (WM_APP+1)
#define WM_FINISHED (WM_APP+2)
enum { ID_PORT=100, ID_REFRESH, ID_PROBE, ID_ADVANCED, ID_DEVICE,
       ID_START, ID_STOP, ID_STEP, ID_LIMIT, ID_DWELL, ID_MODE, ID_CSV,
       ID_BROWSE, ID_RUN, ID_CANCEL, ID_DEMO, ID_APPLY=200, ID_DISCARD };
typedef struct {
    HWND window, plot, status, identity, latest, progress, controls[32], advanced;
    HFONT font, title_font;
    HANDLE thread;
    Options settings, job;
    Record *records;
    size_t count, total, turn;
    int busy, task, demo, close_pending, scale, successful;
    wchar_t result[2048];
} Gui;
static Gui ui;
static HWND advanced_controls[8];
static const int baud_values[]={9600,14400,19200,38400,57600,115200};
static const wchar_t *flow_values[]={L"none",L"xon",L"rtscts"};
static const wchar_t *term_values[]={L"LF",L"CR",L"CRLF"};
static int px(int n) { return MulDiv(n,ui.scale,96); }
static HWND control(int id) { return ui.controls[id-ID_PORT]; }
static void wide_text(const char *s,wchar_t *out,int cap) {
    if(!MultiByteToWideChar(CP_UTF8,0,s,-1,out,cap)) lstrcpynW(out,L"文本过长",cap);
}
static LRESULT static_color(WPARAM wp) {
    SetTextColor((HDC)wp,RGB(32,45,62)); SetBkColor((HDC)wp,RGB(255,255,255));
    return (LRESULT)GetStockObject(WHITE_BRUSH);
}
static void show_error(HWND owner) {
    wchar_t text[2048]; wide_text(error_text,text,2048);
    MessageBoxW(owner,text,L"请检查设置",MB_OK|MB_ICONWARNING);
}
static HWND widget(HWND parent,const wchar_t *cls,const wchar_t *text,DWORD style,
                   int id,int x,int y,int w,int h) {
    HWND c=CreateWindowExW(!wcscmp(cls,L"EDIT")?WS_EX_CLIENTEDGE:0,cls,text,
        WS_CHILD|WS_VISIBLE|style,px(x),px(y),px(w),px(h),parent,(HMENU)(INT_PTR)id,
        GetModuleHandleW(NULL),NULL);
    SendMessageW(c,WM_SETFONT,(WPARAM)ui.font,TRUE); return c;
}
static HWND field(int id,const wchar_t *label,const wchar_t *value,int y) {
    widget(ui.window,L"STATIC",label,0,0,24,y,128,22);
    HWND c=widget(ui.window,L"EDIT",value,ES_AUTOHSCROLL|WS_TABSTOP,id,162,y-3,176,27);
    SendMessageW(c,EM_SETLIMITTEXT,120,0); ui.controls[id-ID_PORT]=c; return c;
}
static HWND combo(HWND parent,int id,int x,int y,int w,const wchar_t **items,int count,int selected) {
    HWND c=widget(parent,L"COMBOBOX",L"",CBS_DROPDOWNLIST|WS_VSCROLL|WS_TABSTOP,id,x,y,w,230);
    for(int i=0;i<count;++i) SendMessageW(c,CB_ADDSTRING,0,(LPARAM)items[i]);
    SendMessageW(c,CB_SETCURSEL,(WPARAM)selected,0); return c;
}
static int read_utf8(HWND c,char *out,int cap) {
    wchar_t value[PATH_CAP]; GetWindowTextW(c,value,PATH_CAP);
    return from_wide(value,out,cap);
}
static void publish_record(const void *record,const void *plan) {
    /* Synchronous delivery bounds queued data and keeps ownership on the UI thread. */
    SendMessageW(ui.window,WM_SAMPLE,(WPARAM)plan,(LPARAM)record);
}
static int is_gs610(const char *identity) {
    char text[RESPONSE_CAP],*model,*end;
    strcpy(text,identity); model=strchr(text,','); if(!model) return 0;
    ++model; end=strchr(model,','); if(end) *end=0; model=trim(model);
    return !_stricmp(model,"GS610") || !_stricmp(model,"765501");
}
static int run_demo(const Options *o,const Plan *p) {
    wchar_t path[PATH_CAP]; FILE *f; int ok; LARGE_INTEGER start,now,freq;
    Record *records=calloc(p->count,sizeof(*records));
    if(!records) return fail("演示记录内存分配失败");
    if(!absolute_path(o->csv,path)) {free(records); return 0;}
    f=exclusive_file(path); if(!f) {free(records); return 0;}
    ok=close_csv(f,csv_header(f,p->count>p->turn));
    QueryPerformanceFrequency(&freq); QueryPerformanceCounter(&start);
    for(size_t i=0;ok && i<p->count;++i) {
        if(!wait_dwell(o->dwell>0.03?o->dwell:0.03)) {ok=0; break;}
        QueryPerformanceCounter(&now);
        records[i].point=i+1; records[i].current=p->points[i];
        records[i].elapsed=(double)(now.QuadPart-start.QuadPart)/(double)freq.QuadPart;
        records[i].voltage=2.0*(double)p->points[i]/(double)SCALE;
        if(records[i].voltage>p->limit) records[i].voltage=p->limit;
        if(records[i].voltage< -p->limit) records[i].voltage= -p->limit;
        ok=csv_save(path,p,records,i+1);
        if(ok) publish_record(&records[i],p);
    }
    free(records); return ok;
}
static DWORD WINAPI worker(void *unused) {
    Instrument d={INVALID_HANDLE_VALUE,0,NULL,0}; Plan p={0};
    char identity[RESPONSE_CAP]={0}; int ok=0; (void)unused;
    error_text[0]=0;
    if(ui.task==1) {
        if(serial_open(&d,&ui.job) && query(&d,"*IDN?",identity)) {
            wide_text(identity,ui.result,2048); ok=1;
        }
    } else if(make_plan(&ui.job,&p) && create_result_dir()) {
        if(ui.demo) ok=run_demo(&ui.job,&p);
        else if(serial_open(&d,&ui.job) && check_identity(&d)) ok=run_sweep(&d,&ui.job,&p);
        if(ok) lstrcpyW(ui.result,ui.demo?L"演示完成（模拟数据），CSV 已保存。":L"扫描完成，已确认仪器输出关闭，CSV 已保存。");
    }
    if(!ok) {
        wchar_t reason[1200]; wide_text(error_text,reason,1200);
        _snwprintf_s(ui.result,2048,_TRUNCATE,L"%ls%ls%ls",interrupted?L"已停止：":L"失败：",reason,
            ui.task==2 && !ui.demo?L"\r\n如通信中断或停止，须在仪器面板确认输出已关闭。":L"");
    }
    if(d.handle!=INVALID_HANDLE_VALUE) CloseHandle(d.handle);
    free(p.points); ui.successful=ok;
    PostMessageW(ui.window,WM_FINISHED,0,0); return 0;
}
static void busy_controls(int busy) {
    ui.busy=busy;
    for(int id=ID_PORT;id<=ID_DEMO;++id) if(control(id)) EnableWindow(control(id),!busy);
    EnableWindow(control(ID_CANCEL),busy);
}
static void start_worker(int task) {
    ui.task=task; InterlockedExchange(&interrupted,0); ui.result[0]=0;
    busy_controls(1);
    SetWindowTextW(ui.status,task==1?L"正在查询 *IDN?，请稍候…":ui.demo?L"正在演示 · 模拟电压 / 时间曲线":L"正在采集 · 实时电压 / 时间曲线");
    ui.thread=CreateThread(NULL,0,worker,NULL,0,NULL);
    if(!ui.thread) {busy_controls(0); SetWindowTextW(ui.status,L"无法创建通信线程");}
}
static void selected_port(char out[256]) {
    wchar_t text[512]={0}; GetWindowTextW(control(ID_PORT),text,512);
    wchar_t *space=wcschr(text,L' '); if(space) *space=0;
    from_wide(text,out,256);
}
static void probe(void) {
    if(ui.busy || ui.advanced) return;
    selected_port(ui.settings.port);
    SetWindowTextW(ui.identity,L"尚未识别设备");
    if(!ui.settings.port[0]) {SetWindowTextW(ui.status,L"未找到串口，请连接适配器后刷新。"); return;}
    ui.job=ui.settings; start_worker(1);
}
static void refresh_ports(void) {
    wchar_t previous[512]={0},target[2048],port[32],friendly[512];
    HWND c=control(ID_PORT); int selected=0;
    GetWindowTextW(c,previous,512); SendMessageW(c,CB_RESETCONTENT,0,0);
    HDEVINFO devices=SetupDiGetClassDevsW(&GUID_DEVCLASS_PORTS,NULL,NULL,DIGCF_PRESENT);
    for(int n=1;n<=4096;++n) {
        swprintf(port,32,L"COM%d",n); if(!QueryDosDeviceW(port,target,2048)) continue;
        friendly[0]=0;
        if(devices!=INVALID_HANDLE_VALUE) {
            SP_DEVINFO_DATA info={0}; info.cbSize=sizeof(info);
            for(DWORD j=0;SetupDiEnumDeviceInfo(devices,j,&info);++j) {
                HKEY key=SetupDiOpenDevRegKey(devices,&info,DICS_FLAG_GLOBAL,0,DIREG_DEV,KEY_READ);
                wchar_t name[64]={0}; DWORD bytes=sizeof(name),type=0;
                if(key!=INVALID_HANDLE_VALUE) {
                    LSTATUS result=RegQueryValueExW(key,L"PortName",NULL,&type,(BYTE*)name,&bytes);
                    RegCloseKey(key);
                    if(result!=ERROR_SUCCESS || type!=REG_SZ || bytes>=sizeof(name)) continue;
                } else continue;
                if(!_wcsicmp(port,name)) {
                    SetupDiGetDeviceRegistryPropertyW(devices,&info,SPDRP_FRIENDLYNAME,NULL,
                        (BYTE*)friendly,sizeof(friendly),NULL); break;
                }
            }
        }
        wchar_t entry[640]; _snwprintf_s(entry,640,_TRUNCATE,L"%ls  %ls",port,friendly[0]?friendly:L"串行端口");
        LRESULT index=SendMessageW(c,CB_ADDSTRING,0,(LPARAM)entry);
        if(!wcscmp(entry,previous)) selected=(int)index;
    }
    if(devices!=INVALID_HANDLE_VALUE) SetupDiDestroyDeviceInfoList(devices);
    SendMessageW(c,CB_SETCURSEL,(WPARAM)selected,0);
    SetWindowTextW(ui.identity,L"选择串口后自动查询设备名称；也可点击“识别设备”。");
    SetWindowTextW(ui.status,SendMessageW(c,CB_GETCOUNT,0,0)>0?L"串口列表已刷新。选择串口或点击识别设备。":L"未找到串口，请检查适配器与驱动。可勾选演示模式预览。");
}
static void draw_text(HDC dc,int x,int y,const wchar_t *text) {
    TextOutW(dc,px(x),px(y),text,(int)wcslen(text));
}
static LRESULT CALLBACK plot_proc(HWND window,UINT message,WPARAM wp,LPARAM lp) {
    if(message!=WM_PAINT) return DefWindowProcW(window,message,wp,lp);
    PAINTSTRUCT paint; RECT client; HDC screen=BeginPaint(window,&paint);
    GetClientRect(window,&client);
    HDC dc=CreateCompatibleDC(screen); HBITMAP bitmap=CreateCompatibleBitmap(screen,client.right,client.bottom);
    HGDIOBJ old_bitmap=SelectObject(dc,bitmap),old_font=SelectObject(dc,ui.font);
    FillRect(dc,&client,(HBRUSH)GetStockObject(WHITE_BRUSH)); SetBkMode(dc,TRANSPARENT);
    int left=px(86),right=client.right-px(26),top=px(60),bottom=client.bottom-px(55);
    SetTextColor(dc,RGB(40,55,70)); draw_text(dc,24,18,ui.demo?L"模拟电压 (V) · 演示数据":L"测量电压 (V)");
    if(right>left && bottom>top) {
        double low=-1,high=1,max_time=1;
        if(ui.count) {
            low=high=ui.records[0].voltage;
            for(size_t i=1;i<ui.count;++i) {double v=ui.records[i].voltage; if(v<low)low=v; if(v>high)high=v;}
            double margin=fmax((high-low)*0.12,fmax(fabs(high)*0.02,0.0001)); low-=margin; high+=margin;
            max_time=fmax(1,ui.records[ui.count-1].elapsed);
        }
        HPEN grid=CreatePen(PS_SOLID,1,RGB(225,231,238)); HGDIOBJ old_pen=SelectObject(dc,grid);
        for(int i=0;i<=5;++i) {
            int y=top+MulDiv(bottom-top,i,5),x=left+MulDiv(right-left,i,5); wchar_t label[64];
            MoveToEx(dc,left,y,NULL); LineTo(dc,right,y);
            MoveToEx(dc,x,top,NULL); LineTo(dc,x,bottom);
            swprintf(label,64,L"%.4g",high-(high-low)*i/5); TextOutW(dc,px(8),y-px(9),label,(int)wcslen(label));
            swprintf(label,64,L"%.2f",max_time*i/5); TextOutW(dc,x-px(12),bottom+px(10),label,(int)wcslen(label));
        }
        SelectObject(dc,old_pen); DeleteObject(grid);
        HPEN forward=CreatePen(PS_SOLID,px(2),RGB(25,111,215));
        HPEN returning=CreatePen(PS_SOLID,px(2),RGB(223,130,30));
        POINT previous={0}; size_t last=0; int have=0;
        /* Preserve each screen column's extrema so a short voltage spike remains visible. */
        for(size_t i=0;i<ui.count;) {
            int x=left+(int)((ui.records[i].elapsed/max_time)*(right-left));
            size_t end=i+1,min_i=i,max_i=i; int phase=i>=ui.turn;
            while(end<ui.count && (end>=ui.turn)==phase &&
                  left+(int)((ui.records[end].elapsed/max_time)*(right-left))==x) {
                if(ui.records[end].voltage<ui.records[min_i].voltage) min_i=end;
                if(ui.records[end].voltage>ui.records[max_i].voltage) max_i=end;
                ++end;
            }
            SelectObject(dc,phase?returning:forward);
            int first_y=bottom-(int)((ui.records[i].voltage-low)/(high-low)*(bottom-top));
            int min_y=bottom-(int)((ui.records[min_i].voltage-low)/(high-low)*(bottom-top));
            int max_y=bottom-(int)((ui.records[max_i].voltage-low)/(high-low)*(bottom-top));
            int final_y=bottom-(int)((ui.records[end-1].voltage-low)/(high-low)*(bottom-top));
            if(have && (last>=ui.turn)==phase) {MoveToEx(dc,previous.x,previous.y,NULL); LineTo(dc,x,first_y);}
            MoveToEx(dc,x,min_y,NULL); LineTo(dc,x,max_y==min_y?max_y+1:max_y);
            previous.x=x; previous.y=final_y; last=end-1; have=1; i=end;
        }
        SelectObject(dc,old_pen); DeleteObject(forward); DeleteObject(returning);
        if(!ui.count) {
            const wchar_t *hint=L"点击运行后，电压曲线将逐点显示";
            TextOutW(dc,left+px(25),(top+bottom)/2,hint,(int)wcslen(hint));
        }
        TextOutW(dc,right-px(82),bottom+px(32),L"时间 (s)",6);
    }
    BitBlt(screen,0,0,client.right,client.bottom,dc,0,0,SRCCOPY);
    SelectObject(dc,old_font); SelectObject(dc,old_bitmap); DeleteObject(bitmap); DeleteDC(dc);
    EndPaint(window,&paint); return 0;
}
static void advanced_summary(void) {
    wchar_t summary[200]; swprintf(summary,200,L"%d / %d%lc%d / %hs / %hs · %.3g s",
        ui.settings.baud,ui.settings.bits,ui.settings.parity,ui.settings.stops,
        ui.settings.flow,ui.settings.term,ui.settings.timeout);
    SetWindowTextW(control(ID_ADVANCED),summary);
}
static LRESULT CALLBACK advanced_proc(HWND window,UINT message,WPARAM wp,LPARAM lp) {
    static const wchar_t *bits[]={L"7",L"8"},*parity[]={L"N",L"E",L"O"},*stops[]={L"1",L"2"};
    if(message==WM_CREATE) {
        const wchar_t *baud[]={L"9600",L"14400",L"19200",L"38400",L"57600",L"115200"};
        const wchar_t *labels[]={L"波特率",L"数据位",L"校验位",L"停止位",L"流控",L"结束符",L"超时（秒）"};
        int bi=0,fi=0,ti=0; wchar_t timeout[64];
        for(int i=0;i<6;++i) if(baud_values[i]==ui.settings.baud) bi=i;
        for(int i=0;i<3;++i) {if(!_stricmp(ui.settings.flow,i==0?"none":i==1?"xon":"rtscts")) fi=i;
            if(!_stricmp(ui.settings.term,i==0?"LF":i==1?"CR":"CRLF")) ti=i;}
        for(int i=0;i<7;++i) widget(window,L"STATIC",labels[i],0,0,24,28+i*42,120,24);
        advanced_controls[0]=combo(window,10,160,24,220,baud,6,bi);
        advanced_controls[1]=combo(window,11,160,66,220,bits,2,ui.settings.bits-7);
        advanced_controls[2]=combo(window,12,160,108,220,parity,3,ui.settings.parity=='N'?0:ui.settings.parity=='E'?1:2);
        advanced_controls[3]=combo(window,13,160,150,220,stops,2,ui.settings.stops-1);
        advanced_controls[4]=combo(window,14,160,192,220,flow_values,3,fi);
        advanced_controls[5]=combo(window,15,160,234,220,term_values,3,ti);
        swprintf(timeout,64,L"%.6g",ui.settings.timeout);
        advanced_controls[6]=widget(window,L"EDIT",timeout,ES_AUTOHSCROLL|WS_TABSTOP,16,160,276,220,27);
        widget(window,L"STATIC",L"请与仪器面板 RS-232 设置保持一致。\r\n这里只配置电脑端；不会修改仪器面板设置。",0,0,24,324,362,48);
        widget(window,L"BUTTON",L"应用",BS_DEFPUSHBUTTON|WS_TABSTOP,ID_APPLY,164,388,100,32);
        widget(window,L"BUTTON",L"取消",WS_TABSTOP,ID_DISCARD,280,388,100,32); return 0;
    }
    if(message==WM_CTLCOLORSTATIC) return static_color(wp);
    if(message==WM_COMMAND && (LOWORD(wp)==ID_APPLY || LOWORD(wp)==IDOK)) {
        Options candidate=ui.settings; char timeout[128];
        candidate.baud=baud_values[SendMessageW(advanced_controls[0],CB_GETCURSEL,0,0)];
        candidate.bits=7+(int)SendMessageW(advanced_controls[1],CB_GETCURSEL,0,0);
        candidate.parity="NEO"[SendMessageW(advanced_controls[2],CB_GETCURSEL,0,0)];
        candidate.stops=1+(int)SendMessageW(advanced_controls[3],CB_GETCURSEL,0,0);
        read_utf8(advanced_controls[4],candidate.flow,sizeof(candidate.flow));
        read_utf8(advanced_controls[5],candidate.term,sizeof(candidate.term));
        if(!read_utf8(advanced_controls[6],timeout,sizeof(timeout)) || !finite_number(timeout,&candidate.timeout) || !valid_settings(&candidate)) {show_error(window); return 0;}
        ui.settings=candidate; advanced_summary(); DestroyWindow(window); return 0;
    }
    if(message==WM_COMMAND && (LOWORD(wp)==ID_DISCARD || LOWORD(wp)==IDCANCEL)) {DestroyWindow(window); return 0;}
    if(message==WM_DESTROY) {
        ui.advanced=NULL; EnableWindow(ui.window,TRUE); SetActiveWindow(ui.window);
        SetWindowTextW(ui.identity,L"串口设置已关闭；点击识别设备重新查询。"); return 0;
    }
    return DefWindowProcW(window,message,wp,lp);
}
static void open_advanced(void) {
    RECT r; GetWindowRect(ui.window,&r); EnableWindow(ui.window,FALSE);
    ui.advanced=CreateWindowExW(WS_EX_DLGMODALFRAME,L"GS610Advanced",L"高级设置 · 串口通信",
        WS_OVERLAPPED|WS_CAPTION|WS_SYSMENU,r.left+px(100),r.top+px(70),px(420),px(480),
        ui.window,NULL,GetModuleHandleW(NULL),NULL);
    if(!ui.advanced) {EnableWindow(ui.window,TRUE); return;}
    ShowWindow(ui.advanced,SW_SHOW);
}
static void browse_csv(void) {
    wchar_t path[PATH_CAP]={0}; OPENFILENAMEW picker={0};
    GetWindowTextW(control(ID_CSV),path,PATH_CAP);
    picker.lStructSize=sizeof(picker); picker.hwndOwner=ui.window; picker.lpstrFile=path;
    picker.nMaxFile=PATH_CAP; picker.lpstrFilter=L"CSV 数据 (*.csv)\0*.csv\0\0";
    picker.lpstrDefExt=L"csv"; picker.Flags=OFN_PATHMUSTEXIST|OFN_NOCHANGEDIR;
    if(GetSaveFileNameW(&picker)) SetWindowTextW(control(ID_CSV),path);
}
static void start_scan(void) {
    Options o=ui.settings; Plan p={0}; char value[128];
    ui.demo=SendMessageW(control(ID_DEMO),BM_GETCHECK,0,0)==BST_CHECKED;
    selected_port(o.port);
    if(!ui.demo && !o.port[0]) {fail("请选择 COM 口，或勾选演示模式。"); show_error(ui.window); return;}
    if(!read_utf8(control(ID_START),o.start,sizeof(o.start)) || !read_utf8(control(ID_STOP),o.stop,sizeof(o.stop)) ||
       !read_utf8(control(ID_STEP),o.step,sizeof(o.step)) || !read_utf8(control(ID_LIMIT),o.limit,sizeof(o.limit)) ||
       !read_utf8(control(ID_DWELL),value,sizeof(value)) || !finite_number(value,&o.dwell)) {show_error(ui.window); return;}
    o.round_trip=SendMessageW(control(ID_MODE),CB_GETCURSEL,0,0)==1; o.keep=0;
    if(!valid_settings(&o) || !make_plan(&o,&p)) {show_error(ui.window); return;}
    if(!init_paths(&o)) {free(p.points); show_error(ui.window); return;}
    wchar_t csv[PATH_CAP]; GetWindowTextW(control(ID_CSV),csv,PATH_CAP);
    if(csv[0] && !from_wide(csv,o.csv,sizeof(o.csv))) {free(p.points); show_error(ui.window); return;}
    if(ui.demo && !csv[0]) {
        char name[PATH_CAP]; strcpy(name,o.csv);
        snprintf(o.csv,sizeof(o.csv),"result/demo-%s",name+7);
    }
    wchar_t resolved[PATH_CAP];
    if(!absolute_path(o.csv,resolved)) {free(p.points); show_error(ui.window); return;}
    if(GetFileAttributesW(resolved)!=INVALID_FILE_ATTRIBUTES) {
        free(p.points); fail("CSV 文件已存在，请选择新文件名；不会覆盖已有数据。"); show_error(ui.window); return;
    }
    Record *records=calloc(p.count,sizeof(*records));
    if(!records) {free(p.points); fail("曲线内存分配失败"); show_error(ui.window); return;}
    free(ui.records); ui.records=records; ui.count=0; ui.total=p.count; ui.turn=p.turn; free(p.points);
    ui.job=o; ui.settings=o;
    SetWindowTextW(ui.latest,ui.demo?L"演示 · 等待模拟数据":L"等待设备完成配置…");
    SetWindowTextW(ui.progress,L"0 点 · 蓝色：往程  /  橙色：返程");
    InvalidateRect(ui.plot,NULL,FALSE); start_worker(2);
}
static LRESULT CALLBACK main_proc(HWND window,UINT message,WPARAM wp,LPARAM lp) {
    if(message==WM_CTLCOLORSTATIC) return static_color(wp);
    if(message==WM_CREATE) {
        ui.window=window;
        HWND title=widget(window,L"STATIC",L"GS610  电流扫描",0,0,24,18,500,36);
        SendMessageW(title,WM_SETFONT,(WPARAM)ui.title_font,TRUE);
        widget(window,L"STATIC",L"RS-232  /  恒流输出 · 电压测量",0,0,24,59,500,24);
        widget(window,L"STATIC",L"连接设备",0,0,24,100,300,24);
        ui.controls[ID_PORT-ID_PORT]=combo(window,ID_PORT,24,132,314,NULL,0,0);
        ui.controls[ID_REFRESH-ID_PORT]=widget(window,L"BUTTON",L"刷新串口",WS_TABSTOP,ID_REFRESH,24,170,150,30);
        ui.controls[ID_PROBE-ID_PORT]=widget(window,L"BUTTON",L"识别设备",WS_TABSTOP,ID_PROBE,188,170,150,30);
        ui.identity=widget(window,L"STATIC",L"尚未识别设备",0,0,24,212,314,66);
        widget(window,L"STATIC",L"高级设置（点击修改串口参数）",0,0,24,281,314,22);
        ui.controls[ID_ADVANCED-ID_PORT]=widget(window,L"BUTTON",L"",WS_TABSTOP,ID_ADVANCED,24,307,314,32);
        widget(window,L"STATIC",L"设备参数",0,0,24,355,124,22);
        const wchar_t *device[]={L"GS610 / 765501 · 电流扫描"};
        ui.controls[ID_DEVICE-ID_PORT]=combo(window,ID_DEVICE,24,383,314,device,1,0);
        field(ID_START,L"初始电流 (A)",L"-3.2",431);
        field(ID_STOP,L"截止电流 (A)",L"3.2",467);
        field(ID_STEP,L"步进电流 (A)",L"0.08",503);
        field(ID_LIMIT,L"对称限压 (V)",L"12",539);
        field(ID_DWELL,L"每点等待 (s)",L"0.1",575);
        widget(window,L"STATIC",L"扫描方式",0,0,24,611,120,24);
        const wchar_t *modes[]={L"单程",L"往返（双程）"};
        ui.controls[ID_MODE-ID_PORT]=combo(window,ID_MODE,162,607,176,modes,2,1);
        ui.controls[ID_DEMO-ID_PORT]=widget(window,L"BUTTON",L"演示模式（不连接仪器）",BS_AUTOCHECKBOX|WS_TABSTOP,ID_DEMO,24,649,314,28);
        ui.latest=widget(window,L"STATIC",L"— V",0,0,376,100,700,44);
        SendMessageW(ui.latest,WM_SETFONT,(WPARAM)ui.title_font,TRUE);
        ui.progress=widget(window,L"STATIC",L"0 点 · 蓝色：往程  /  橙色：返程",0,0,376,151,700,24);
        ui.plot=widget(window,L"GS610Plot",L"",0,0,376,190,740,398);
        widget(window,L"STATIC",L"CSV 保存路径（留空自动命名，保存在程序目录 result 下）",0,0,376,603,650,24);
        ui.controls[ID_CSV-ID_PORT]=widget(window,L"EDIT",L"",ES_AUTOHSCROLL|WS_TABSTOP,ID_CSV,376,632,620,28);
        SendMessageW(control(ID_CSV),EM_SETLIMITTEXT,PATH_CAP/4,0);
        ui.controls[ID_BROWSE-ID_PORT]=widget(window,L"BUTTON",L"浏览…",WS_TABSTOP,ID_BROWSE,1010,631,106,30);
        ui.controls[ID_RUN-ID_PORT]=widget(window,L"BUTTON",L"运行",BS_DEFPUSHBUTTON|WS_TABSTOP,ID_RUN,376,683,150,38);
        ui.controls[ID_CANCEL-ID_PORT]=widget(window,L"BUTTON",L"停止",WS_TABSTOP,ID_CANCEL,542,683,130,38);
        ui.status=widget(window,L"STATIC",L"就绪",0,0,376,737,740,60);
        EnableWindow(control(ID_CANCEL),FALSE); advanced_summary(); refresh_ports(); return 0;
    }
    if(message==WM_GETMINMAXINFO) {MINMAXINFO *m=(MINMAXINFO*)lp; m->ptMinTrackSize.x=px(1100); m->ptMinTrackSize.y=px(850); return 0;}
    if(message==WM_SIZE && ui.plot) {
        int width=LOWORD(lp),height=HIWORD(lp),left=px(376),bottom=height-px(225);
        MoveWindow(ui.plot,left,px(190),width-left-px(24),bottom-px(190),TRUE);
        /* Bottom controls follow the window; the parameter column remains stationary. */
        HWND label=GetWindow(control(ID_CSV),GW_HWNDPREV);
        MoveWindow(label,left,bottom+px(15),width-left-px(24),px(24),TRUE);
        MoveWindow(control(ID_CSV),left,bottom+px(44),width-left-px(144),px(28),TRUE);
        MoveWindow(control(ID_BROWSE),width-px(130),bottom+px(43),px(106),px(30),TRUE);
        MoveWindow(control(ID_RUN),left,bottom+px(95),px(150),px(38),TRUE);
        MoveWindow(control(ID_CANCEL),left+px(166),bottom+px(95),px(130),px(38),TRUE);
        MoveWindow(ui.status,left,bottom+px(149),width-left-px(24),px(68),TRUE);
        MoveWindow(ui.latest,left,px(100),width-left-px(24),px(44),TRUE);
        return 0;
    }
    if(message==WM_SAMPLE) {
        const Record *r=(const Record*)lp; const Plan *p=(const Plan*)wp;
        if(ui.count<ui.total) ui.records[ui.count++]=*r;
        wchar_t text[256]; char current[64]; format_ticks(r->current,current);
        swprintf(text,256,L"%ls%.4f V",ui.demo?L"演示  ":L"",r->voltage); SetWindowTextW(ui.latest,text);
        swprintf(text,256,L"%zu / %zu 点 · %ls · %hs A · %.2f s   |   蓝：往程 / 橙：返程",
            r->point,p->count,r->point>p->turn?L"返程":L"往程",current,r->elapsed);
        SetWindowTextW(ui.progress,text); InvalidateRect(ui.plot,NULL,FALSE); return 0;
    }
    if(message==WM_FINISHED) {
        CloseHandle(ui.thread); ui.thread=NULL; busy_controls(0);
        SetWindowTextW(ui.status,ui.result);
        if(ui.task==1) {
            SetWindowTextW(ui.identity,ui.result);
            if(ui.successful) {
                char identity[RESPONSE_CAP];
                if(from_wide(ui.result,identity,sizeof(identity)) && !is_gs610(identity))
                    SetWindowTextW(ui.status,L"已识别设备，但型号不是 GS610 / 765501，不能运行扫描。");
            }
        }
        if(ui.close_pending) {ui.close_pending=0; SetWindowTextW(ui.status,ui.result);
            if(ui.task==2 && !ui.demo) MessageBoxW(window,ui.result,L"任务结束",MB_OK|MB_ICONINFORMATION);
            DestroyWindow(window);}
        return 0;
    }
    if(message==WM_COMMAND) {
        int id=LOWORD(wp); if(ui.busy && id!=ID_CANCEL) return 0;
        if(id==ID_REFRESH) refresh_ports();
        else if(id==ID_PROBE || (id==ID_PORT && HIWORD(wp)==CBN_SELCHANGE)) probe();
        else if(id==ID_ADVANCED) open_advanced();
        else if(id==ID_BROWSE) browse_csv();
        else if(id==ID_RUN) start_scan();
        else if(id==ID_CANCEL && ui.busy) {InterlockedExchange(&interrupted,1); EnableWindow(control(ID_CANCEL),FALSE);
            SetWindowTextW(ui.status,L"正在停止，等待通信结束并尝试关闭输出…");}
        return 0;
    }
    if(message==WM_CLOSE && ui.busy) {
        ui.close_pending=1; InterlockedExchange(&interrupted,1);
        SetWindowTextW(ui.status,L"正在结束任务，关闭输出后退出…"); return 0;
    }
    if(message==WM_DESTROY) {free(ui.records); ui.records=NULL; PostQuitMessage(0); return 0;}
    return DefWindowProcW(window,message,wp,lp);
}
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,PWSTR command,int show) {
    (void)previous; (void)command;
    SetProcessDPIAware(); HDC dc=GetDC(NULL); ui.scale=GetDeviceCaps(dc,LOGPIXELSX); ReleaseDC(NULL,dc);
    ui.font=CreateFontW(-px(15),0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    ui.title_font=CreateFontW(-px(26),0,0,0,FW_SEMIBOLD,0,0,0,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    ui.settings.baud=9600; ui.settings.bits=8; ui.settings.parity='N'; ui.settings.stops=1;
    ui.settings.timeout=5; ui.settings.dwell=0.1; strcpy(ui.settings.flow,"none"); strcpy(ui.settings.term,"LF");
    if(!init_paths(&ui.settings)) return 1;
    WNDCLASSW cls={0}; cls.hInstance=instance; cls.hCursor=LoadCursorW(NULL,IDC_ARROW);
    cls.hbrBackground=(HBRUSH)(COLOR_WINDOW+1); cls.lpfnWndProc=plot_proc; cls.lpszClassName=L"GS610Plot";
    if(!RegisterClassW(&cls)) return 1;
    cls.lpfnWndProc=advanced_proc; cls.lpszClassName=L"GS610Advanced"; if(!RegisterClassW(&cls)) return 1;
    cls.lpfnWndProc=main_proc; cls.lpszClassName=L"GS610Main"; cls.hIcon=LoadIconW(NULL,IDI_APPLICATION);
    if(!RegisterClassW(&cls)) return 1;
    HWND window=CreateWindowExW(WS_EX_CONTROLPARENT,cls.lpszClassName,L"GS610 · 串口扫描与实时电压",
        WS_OVERLAPPEDWINDOW,CW_USEDEFAULT,CW_USEDEFAULT,px(1180),px(870),NULL,NULL,instance,NULL);
    if(!window) return 1;
    ShowWindow(window,show); UpdateWindow(window);
    MSG message; int result;
    while((result=(int)GetMessageW(&message,NULL,0,0))>0) {
        HWND target=ui.advanced?ui.advanced:window;
        if(!IsDialogMessageW(target,&message)) {TranslateMessage(&message); DispatchMessageW(&message);}
    }
    DeleteObject(ui.font); DeleteObject(ui.title_font);
    return result<0?1:0;
}
