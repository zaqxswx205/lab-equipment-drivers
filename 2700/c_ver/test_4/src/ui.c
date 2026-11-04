#include "ui.h"
#include "acquisition.h"
#include "plot.h"
#include <commctrl.h>
#include <commdlg.h>
#include <shlobj.h>
#include <process.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define WM_EVENT (WM_APP+1)
enum {
    TITLE=100,PORT,REFRESH,MODEL,BAUD,TIMEOUT,INTERVAL,FLOW,SIM,FOLDER,BROWSE,HINT,CONFIG,
    MODE,RANGE,NPLC,SETTLE,APPLY,DISABLE,CLEAR,MODULES,PROBE,START,STOP,EXPORT,READOUT,
    LATEST,CURVE,STATS,GRAPH,FILE_LABEL,STATUS
};
typedef struct {
    HWND window,h[256]; HFONT font,title_font;
    Settings settings,run_settings; PlotData *plot; PlotView view;
    HANDLE stop,worker; AcquisitionJob acquisition; ExportJob export_job;
    int loading,dirty,active,exporting,closing,had_error,done,smoke,smoke_stage,exit_code;
    uint64_t counts[MAX_CHANNELS+1],total; int rows[MAX_CHANNELS+1];
    wchar_t current_file[PATH_CAP],base_folder[PATH_CAP];
} App;
static App app;
#define H(id) app.h[(id)]
static void set_utf8(int id,const char *text) {
    wchar_t w[2048]; to_wide(text,w,2048); SetWindowTextW(H(id),w);
}
static void failure(const char *text) {
    wchar_t w[2048]; to_wide(text,w,2048); SetWindowTextW(H(STATUS),w);
    if(app.smoke) { app.exit_code=1; app.had_error=1; }
    else MessageBoxW(app.window,w,L"操作失败",MB_OK|MB_ICONERROR);
}
static HWND make(int id,const wchar_t *cls,const wchar_t *text,DWORD style) {
    H(id)=CreateWindowExW(!wcscmp(cls,L"EDIT")?WS_EX_CLIENTEDGE:0,cls,text,
        WS_CHILD|WS_VISIBLE|style,0,0,10,24,app.window,(HMENU)(INT_PTR)id,GetModuleHandle(NULL),NULL);
    SendMessageW(H(id),WM_SETFONT,(WPARAM)app.font,TRUE); return H(id);
}
static void add_combo(int id,const wchar_t *text) { SendMessageW(H(id),CB_ADDSTRING,0,(LPARAM)text); }
static void column(int id,int at,const wchar_t *text,int width) {
    LVCOLUMNW c={0};c.mask=LVCF_TEXT|LVCF_WIDTH|LVCF_SUBITEM;c.pszText=(LPWSTR)text;c.cx=width;c.iSubItem=at;
    ListView_InsertColumn(H(id),at,&c);
}
static void cell(int id,int row,int col,const wchar_t *text) { ListView_SetItemText(H(id),row,col,(LPWSTR)text); }
static int row_data(int id,int row) {
    LVITEMW item={0};item.mask=LVIF_PARAM;item.iItem=row;ListView_GetItem(H(id),&item);return (int)item.lParam;
}
static void range_text(const ChannelConfig *c,wchar_t *text) {
    if(c->auto_range) wcscpy_s(text,64,L"AUTO");else swprintf_s(text,64,L"%.12g",c->range);
}
static void editor_load(void) {
    ChannelConfig *c;wchar_t text[64];int row=ListView_GetNextItem(H(CONFIG),-1,LVNI_SELECTED);
    if(app.active) return;
    if(!app.settings.model) c=&app.settings.front;
    else {if(row<0) return;c=&app.settings.channels[row_data(CONFIG,row)];}
    app.loading=1;
    SendMessageW(H(MODE),CB_SETCURSEL,c->mode,0);range_text(c,text);SetWindowTextW(H(RANGE),text);
    swprintf_s(text,64,L"%.12g",c->nplc);SetWindowTextW(H(NPLC),text);
    swprintf_s(text,64,L"%.12g",c->settle);SetWindowTextW(H(SETTLE),text);
    app.loading=0;app.dirty=0;
}
static void config_row(int row,const ChannelConfig *c) {
    wchar_t text[64];cell(CONFIG,row,1,mode_name_w(c->mode));range_text(c,text);cell(CONFIG,row,2,text);
    swprintf_s(text,64,L"%.12g",c->nplc);cell(CONFIG,row,3,text);
    swprintf_s(text,64,L"%.12g",c->settle);cell(CONFIG,row,4,text);ListView_SetCheckState(H(CONFIG),row,c->enabled);
}
static void rebuild_config(void) {
    int i,row=0,limit=app.settings.model==7700?22:42;wchar_t text[64];
    app.loading=1;ListView_DeleteAllItems(H(CONFIG));
    for(i=0;i<MAX_CHANNELS;i++) {
        ChannelConfig *c=&app.settings.channels[i];LVITEMW item={0};
        if(c->channel%100>limit) {c->enabled=0;continue;}
        if(app.settings.model && !channel_valid(app.settings.model,c)) {
            c->enabled=0;c->mode=c->channel%100>limit-2?MODE_DCI:MODE_DCV;
            c->auto_range=1;c->nplc=1;c->settle=0.5;
        }
        swprintf_s(text,64,L"%d",c->channel);item.mask=LVIF_TEXT|LVIF_PARAM;item.iItem=row;item.pszText=text;item.lParam=i;
        ListView_InsertItem(H(CONFIG),&item);config_row(row++,c);
    }
    app.loading=0;
    if(row) ListView_SetItemState(H(CONFIG),0,LVIS_SELECTED|LVIS_FOCUSED,LVIS_SELECTED|LVIS_FOCUSED);
    EnableWindow(H(CONFIG),!app.active && app.settings.model!=0);
    SetWindowTextW(H(APPLY),app.settings.model?L"应用并启用选中":L"应用前面板参数");
    SetWindowTextW(H(HINT),app.settings.model?
        L"R 输入 · 勾选启用，选中行编辑；Ctrl/Shift 可多选。编辑后点击应用；四线感测通道不可重复启用。":
        L"F 输入 · 编辑区设置前面板参数，点击应用；模块通道不参与采集。");
    editor_load();
}
static int get_number(int id,double *out) {
    wchar_t w[128];char u[512];GetWindowTextW(H(id),w,128);to_utf8(w,u,sizeof(u));return parse_number(u,out);
}
static int editor_read(ChannelConfig *c,char *error,size_t cap) {
    wchar_t w[128];char u[512],*start,*end;
    c->mode=(MeasureMode)SendMessageW(H(MODE),CB_GETCURSEL,0,0);
    GetWindowTextW(H(RANGE),w,128);to_utf8(w,u,sizeof(u));start=u;while(*start==' ') start++;
    end=start+strlen(start);while(end>start && end[-1]==' ') *--end=0;c->auto_range=!_stricmp(start,"AUTO");
    if(!c->auto_range && !parse_number(start,&c->range)) {snprintf(error,cap,"量程必须为 AUTO 或正数");return 0;}
    if(!get_number(NPLC,&c->nplc) || !get_number(SETTLE,&c->settle)) {snprintf(error,cap,"NPLC 和稳定时间必须为有效数字");return 0;}
    return config_validate(app.settings.model,c,error,cap);
}
static void editor_apply(void) {
    char error[ERROR_CAP];ChannelConfig proposed[MAX_CHANNELS];int rows[MAX_CHANNELS],indices[MAX_CHANNELS],row=-1,n=0;
    if(app.active) return;
    if(!app.settings.model) {
        ChannelConfig c=app.settings.front;if(!editor_read(&c,error,sizeof(error))) {failure(error);return;}
        app.settings.front=c;app.dirty=0;return;
    }
    while((row=ListView_GetNextItem(H(CONFIG),row,LVNI_SELECTED))>=0) {
        int idx=row_data(CONFIG,row);proposed[n]=app.settings.channels[idx];
        if(!editor_read(&proposed[n],error,sizeof(error))) {failure(error);return;}
        proposed[n].enabled=1;rows[n]=row;indices[n]=idx;n++;
    }
    if(!n) {failure("请先选中需要配置的通道行");return;}
    app.loading=1;
    for(row=0;row<n;row++) {app.settings.channels[indices[row]]=proposed[row];config_row(rows[row],&proposed[row]);}
    app.loading=0;app.dirty=0;SetWindowTextW(H(STATUS),L"通道参数已应用");
}
static void refresh_ports(void) {
    wchar_t names[256][64],current[64];int i,count;GetWindowTextW(H(PORT),current,64);
    SendMessageW(H(PORT),CB_RESETCONTENT,0,0);count=serial_list(names,256);for(i=0;i<count;i++) add_combo(PORT,names[i]);
    SetWindowTextW(H(PORT),count && (!current[0] || !wcscmp(current,L"COM1"))?names[0]:current[0]?current:L"COM1");
}
static void set_busy(int active) {
    int ids[]={PORT,REFRESH,MODEL,BAUD,TIMEOUT,INTERVAL,FLOW,SIM,FOLDER,BROWSE,MODE,RANGE,NPLC,SETTLE,APPLY,DISABLE,CLEAR,START,PROBE,EXPORT};size_t i;
    app.active=active;for(i=0;i<sizeof(ids)/sizeof(ids[0]);i++) EnableWindow(H(ids[i]),!active);
    EnableWindow(H(CONFIG),!active && app.settings.model!=0);EnableWindow(H(STOP),active);
}
static int emit_ui(void *ctx,Event *event) {return PostMessageW((HWND)ctx,WM_EVENT,0,(LPARAM)event)!=0;}
static int settings_read(void) {
    char error[ERROR_CAP];double n;wchar_t baud[64];
    if(app.dirty) {failure("编辑区参数尚未应用，请先点击应用按钮");return 0;}
    GetWindowTextW(H(PORT),app.settings.port,64);GetWindowTextW(H(FOLDER),app.settings.folder,PATH_CAP);
    GetWindowTextW(H(BAUD),baud,64);app.settings.baud=_wtoi(baud);
    if(!get_number(TIMEOUT,&n)) {failure("超时必须为数字");return 0;}app.settings.timeout=n;
    if(!get_number(INTERVAL,&n)) {failure("每轮间隔必须为数字");return 0;}app.settings.interval=n;
    app.settings.flow=SendMessageW(H(FLOW),BM_GETCHECK,0,0)==BST_CHECKED;
    app.settings.simulation=SendMessageW(H(SIM),BM_GETCHECK,0,0)==BST_CHECKED;
    if(!app.settings.folder[0]) {failure("请选择保存目录");return 0;}
    if(!settings_validate(&app.settings,error,sizeof(error))) {failure(error);return 0;}return 1;
}
static void clear_samples(void) {
    int i;plot_reset(app.plot);memset(app.counts,0,sizeof(app.counts));app.total=0;
    for(i=0;i<=MAX_CHANNELS;i++) app.rows[i]=-1;
    ListView_DeleteAllItems(H(LATEST));app.current_file[0]=0;SetWindowTextW(H(READOUT),L"等待数据");
    SetWindowTextW(H(FILE_LABEL),L"TXT：配置成功后创建文件");SetWindowTextW(H(STATS),L"已保存 0 点");InvalidateRect(H(GRAPH),NULL,FALSE);
}
static void start_job(int probe) {
    uintptr_t thread;if(app.active || !settings_read()) return;
    ResetEvent(app.stop);app.run_settings=app.settings;app.had_error=0;app.done=0;app.exporting=0;if(!probe) clear_samples();
    memset(&app.acquisition,0,sizeof(app.acquisition));app.acquisition.settings=app.run_settings;
    app.acquisition.stop=app.stop;app.acquisition.emit=emit_ui;app.acquisition.context=app.window;app.acquisition.probe=probe;
    if(app.smoke) app.acquisition.max_rounds=3;
    set_busy(1);SetWindowTextW(H(STATUS),L"正在连接 / 配置…");thread=_beginthreadex(NULL,0,acquisition_thread,&app.acquisition,0,NULL);
    if(!thread) {set_busy(0);failure("无法创建采集线程");return;}app.worker=(HANDLE)thread;
}
static void stop_job(void) {
    if(app.active) {SetEvent(app.stop);EnableWindow(H(STOP),FALSE);SetWindowTextW(H(STATUS),L"正在停止并释放文件、串口…");}
}
static void browse_folder(void) {
    BROWSEINFOW info={0};PIDLIST_ABSOLUTE id;wchar_t folder[MAX_PATH];
    info.hwndOwner=app.window;info.lpszTitle=L"选择保存目录";info.ulFlags=BIF_RETURNONLYFSDIRS|BIF_NEWDIALOGSTYLE;
    id=SHBrowseForFolderW(&info);if(id) {if(SHGetPathFromIDListW(id,folder)) SetWindowTextW(H(FOLDER),folder);CoTaskMemFree(id);}
}
static void export_csv(void) {
    OPENFILENAMEW dialog={0};wchar_t source[PATH_CAP]=L"",destination[PATH_CAP]=L"";uintptr_t thread;
    if(app.active) return;
    dialog.lStructSize=sizeof(dialog);dialog.hwndOwner=app.window;dialog.lpstrFilter=L"采集 TXT\0*.txt\0所有文件\0*.*\0";
    dialog.lpstrFile=source;dialog.nMaxFile=PATH_CAP;dialog.Flags=OFN_FILEMUSTEXIST|OFN_PATHMUSTEXIST|OFN_EXPLORER;
    dialog.lpstrTitle=L"选择 TXT 数据文件";if(!GetOpenFileNameW(&dialog)) return;
    wcscpy_s(destination,PATH_CAP,source);{wchar_t *dot=wcsrchr(destination,L'.');if(dot) *dot=0;wcscat_s(destination,PATH_CAP,L".csv");}
    dialog.lpstrFile=destination;dialog.lpstrFilter=L"CSV\0*.csv\0";dialog.lpstrTitle=L"保存 CSV（使用新文件名）";
    dialog.lpstrDefExt=L"csv";dialog.Flags=OFN_PATHMUSTEXIST|OFN_EXPLORER;if(!GetSaveFileNameW(&dialog)) return;
    memset(&app.export_job,0,sizeof(app.export_job));wcscpy_s(app.export_job.source,PATH_CAP,source);wcscpy_s(app.export_job.destination,PATH_CAP,destination);
    app.export_job.stop=app.stop;app.export_job.emit=emit_ui;app.export_job.context=app.window;
    ResetEvent(app.stop);app.had_error=0;app.done=0;app.exporting=1;set_busy(1);SetWindowTextW(H(STATUS),L"正在转换 CSV…");
    thread=_beginthreadex(NULL,0,export_thread,&app.export_job,0,NULL);
    if(!thread) {set_busy(0);app.exporting=0;failure("无法创建导出线程");return;}app.worker=(HANDLE)thread;
}
static void sample_update(const Sample *s) {
    int idx=s->channel?channel_index(s->channel):MAX_CHANNELS,row;wchar_t text[256];LVITEMW item={0};if(idx<0) return;
    plot_add(app.plot,s);app.counts[idx]++;app.total=s->index;row=app.rows[idx];
    if(row<0) {row=ListView_GetItemCount(H(LATEST));item.mask=LVIF_TEXT|LVIF_PARAM;item.iItem=row;item.pszText=L"";item.lParam=s->channel;ListView_InsertItem(H(LATEST),&item);app.rows[idx]=row;}
    if(s->channel) swprintf_s(text,256,L"%d",s->channel);else wcscpy_s(text,256,L"FRONT");cell(LATEST,row,0,text);cell(LATEST,row,1,mode_name_w(s->mode));
    if(s->valid) swprintf_s(text,256,L"%.9g",s->value);else wcscpy_s(text,256,L"—");cell(LATEST,row,2,text);
    to_wide(mode_unit(s->mode),text,256);cell(LATEST,row,3,text);cell(LATEST,row,4,s->valid?L"OK":L"OVERLOAD");
    to_wide(s->timestamp,text,256);cell(LATEST,row,5,text);swprintf_s(text,256,L"%llu",(unsigned long long)app.counts[idx]);cell(LATEST,row,6,text);
    if(s->valid) {if(s->channel) swprintf_s(text,256,L"%d: %.9g %hs",s->channel,s->value,mode_unit(s->mode));else swprintf_s(text,256,L"FRONT: %.9g %hs",s->value,mode_unit(s->mode));}
    else wcscpy_s(text,256,L"OVERLOAD");SetWindowTextW(H(READOUT),text);
    swprintf_s(text,256,L"已保存 %llu 点 · %s",(unsigned long long)s->index,app.run_settings.simulation?L"模拟模式":L"实测");SetWindowTextW(H(STATS),text);
    if(!app.had_error) SetWindowTextW(H(STATUS),s->valid?L"正在采集，数据已同步到 TXT":L"超量程 / 无效测量：已记录状态，曲线断开");
}
static void receive(Event *event) {
    wchar_t text[PATH_CAP+160];switch(event->kind) {
    case EVENT_SAMPLE:sample_update(&event->sample);break;
    case EVENT_INFO:set_utf8(STATUS,event->text);break;
    case EVENT_WARNING:set_utf8(STATUS,event->text);break;
    case EVENT_MODULES:set_utf8(MODULES,event->text);break;
    case EVENT_FILE:wcscpy_s(app.current_file,PATH_CAP,event->path);_snwprintf_s(text,PATH_CAP+160,_TRUNCATE,L"TXT：%s",event->path);SetWindowTextW(H(FILE_LABEL),text);break;
    case EVENT_ERROR:app.had_error=1;failure(event->text);break;
    case EVENT_DONE:app.done=1;break;
    case EVENT_EXPORTED:_snwprintf_s(text,PATH_CAP+160,_TRUNCATE,L"已导出 %llu 点：%s",(unsigned long long)event->count,event->path);SetWindowTextW(H(FILE_LABEL),text);break;
    }free(event);
}
static void place(int id,int x,int y,int width,int height) {MoveWindow(H(id),x,y,width>0?width:1,height>0?height:1,TRUE);}
static void layout(void) {
    RECT r;int w,h;GetClientRect(app.window,&r);w=r.right;h=r.bottom;
    place(TITLE,12,8,w-24,32);
    place(1,12,50,38,24);place(PORT,52,46,100,200);place(REFRESH,158,46,80,28);
    place(2,250,50,40,24);place(MODEL,292,46,95,200);place(3,402,50,52,24);place(BAUD,456,46,80,200);
    place(4,550,50,70,24);place(TIMEOUT,622,46,55,27);place(5,694,50,106,24);place(INTERVAL,802,46,55,27);
    place(FLOW,866,46,125,28);place(SIM,1000,46,w-1012,28);
    place(6,12,86,70,24);place(FOLDER,86,82,w-194,28);place(BROWSE,w-100,82,88,28);place(HINT,12,119,w-24,23);
    place(CONFIG,12,146,w-24,164);
    place(7,12,324,38,24);place(MODE,52,320,165,200);place(8,230,324,38,24);place(RANGE,270,320,90,27);
    place(9,374,324,45,24);place(NPLC,421,320,60,27);place(10,496,324,76,24);place(SETTLE,574,320,64,27);
    place(APPLY,654,320,145,28);place(DISABLE,808,320,110,28);place(CLEAR,928,320,110,28);place(MODULES,12,358,w-24,24);
    place(PROBE,12,392,110,32);place(START,132,392,145,32);place(STOP,287,392,100,32);place(EXPORT,397,392,130,32);place(READOUT,550,394,w-562,30);
    place(LATEST,12,436,w-24,150);place(11,12,600,76,24);place(CURVE,90,596,125,200);place(STATS,235,600,w-247,24);
    place(GRAPH,12,630,w-24,h-710);place(FILE_LABEL,12,h-68,w-24,26);place(STATUS,12,h-35,w-24,25);
}
static void create_controls(void) {
    int i;wchar_t text[64];const wchar_t *labels[]={L"",L"串口",L"来源",L"波特率",L"超时 / 秒",L"每轮间隔 / 秒",L"保存目录",L"功能",L"量程",L"NPLC",L"稳定 / 秒",L"曲线单位"};
    for(i=1;i<=11;i++) make(i,L"STATIC",labels[i],SS_LEFT);
    make(TITLE,L"STATIC",L"Keithley 2700 · 7702 / 7700 逐通道采集（C 原生版）",SS_LEFT);SendMessageW(H(TITLE),WM_SETFONT,(WPARAM)app.title_font,TRUE);
    make(PORT,WC_COMBOBOXW,L"",CBS_DROPDOWN|WS_VSCROLL);SetWindowTextW(H(PORT),L"COM1");make(REFRESH,L"BUTTON",L"刷新",BS_PUSHBUTTON);
    make(MODEL,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST);add_combo(MODEL,L"7702");add_combo(MODEL,L"7700");add_combo(MODEL,L"FRONT");SendMessageW(H(MODEL),CB_SETCURSEL,0,0);
    make(BAUD,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST);{int rates[]={300,600,1200,2400,4800,9600,19200};for(i=0;i<7;i++) {swprintf_s(text,64,L"%d",rates[i]);add_combo(BAUD,text);}SendMessageW(H(BAUD),CB_SETCURSEL,5,0);}
    make(TIMEOUT,L"EDIT",L"10",ES_AUTOHSCROLL);make(INTERVAL,L"EDIT",L"1",ES_AUTOHSCROLL);make(FLOW,L"BUTTON",L"XON/XOFF",BS_AUTOCHECKBOX);make(SIM,L"BUTTON",L"模拟模式",BS_AUTOCHECKBOX);
    make(FOLDER,L"EDIT",app.settings.folder,ES_AUTOHSCROLL);make(BROWSE,L"BUTTON",L"选择目录",BS_PUSHBUTTON);make(HINT,L"STATIC",L"",SS_LEFT);
    make(CONFIG,WC_LISTVIEWW,L"",LVS_REPORT|LVS_SHOWSELALWAYS|WS_BORDER);ListView_SetExtendedListViewStyle(H(CONFIG),LVS_EX_CHECKBOXES|LVS_EX_FULLROWSELECT|LVS_EX_DOUBLEBUFFER|LVS_EX_GRIDLINES);
    column(CONFIG,0,L"启用 / 通道",130);column(CONFIG,1,L"测量功能",180);column(CONFIG,2,L"量程（基本单位）",170);column(CONFIG,3,L"NPLC",100);column(CONFIG,4,L"稳定 / 秒",110);
    make(MODE,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST);for(i=0;i<MODE_COUNT;i++) add_combo(MODE,mode_name_w((MeasureMode)i));
    make(RANGE,L"EDIT",L"AUTO",ES_AUTOHSCROLL);make(NPLC,L"EDIT",L"1",ES_AUTOHSCROLL);make(SETTLE,L"EDIT",L"0.5",ES_AUTOHSCROLL);
    make(APPLY,L"BUTTON",L"应用并启用选中",BS_PUSHBUTTON);make(DISABLE,L"BUTTON",L"禁用选中",BS_PUSHBUTTON);make(CLEAR,L"BUTTON",L"清空全部",BS_PUSHBUTTON);
    make(MODULES,L"STATIC",L"模块：尚未查询 · 8N1 / TX=CR / RX=自动",SS_LEFT);
    make(PROBE,L"BUTTON",L"测试连接",BS_PUSHBUTTON);make(START,L"BUTTON",L"连接并开始采集",BS_PUSHBUTTON);make(STOP,L"BUTTON",L"停止采集",BS_PUSHBUTTON);make(EXPORT,L"BUTTON",L"TXT → CSV…",BS_PUSHBUTTON);make(READOUT,L"STATIC",L"等待数据",SS_LEFT);
    make(LATEST,WC_LISTVIEWW,L"",LVS_REPORT|LVS_SHOWSELALWAYS|WS_BORDER);ListView_SetExtendedListViewStyle(H(LATEST),LVS_EX_FULLROWSELECT|LVS_EX_DOUBLEBUFFER|LVS_EX_GRIDLINES);
    column(LATEST,0,L"通道 / 颜色",110);column(LATEST,1,L"功能",155);column(LATEST,2,L"最新读数",150);column(LATEST,3,L"单位",65);column(LATEST,4,L"状态",100);column(LATEST,5,L"更新时间",225);column(LATEST,6,L"已保存点数",100);
    make(CURVE,WC_COMBOBOXW,L"",CBS_DROPDOWNLIST);add_combo(CURVE,L"电阻 / Ω");add_combo(CURVE,L"电压 / V");add_combo(CURVE,L"电流 / A");SendMessageW(H(CURVE),CB_SETCURSEL,0,0);
    make(STATS,L"STATIC",L"已保存 0 点 · 曲线每通道最多保留 2000 点",SS_LEFT);app.view.data=app.plot;
    H(GRAPH)=CreateWindowExW(0,L"KeithleyPlot",L"",WS_CHILD|WS_VISIBLE,0,0,10,10,app.window,NULL,GetModuleHandle(NULL),&app.view);
    make(FILE_LABEL,L"STATIC",L"TXT：尚未创建（只保存 timestamp、channel、mode、unit、value、status）",SS_LEFT|SS_PATHELLIPSIS);
    make(STATUS,L"STATIC",L"就绪 · 默认 101 测电阻、102 测直流电压，稳定时间 0.5 秒",SS_LEFT);
    rebuild_config();refresh_ports();set_busy(0);
}
static int smoke_configuration(void) {
    /* Drive the same editor callbacks as a user; all later I/O is simulation. */
    int row;
    if(ListView_GetItemCount(H(CONFIG))!=84 || app.settings.channels[0].settle!=0.5) return 0;
    if(SendMessageW(H(MODEL),CB_GETCURSEL,0,0)!=0 || SendMessageW(H(BAUD),CB_GETCURSEL,0,0)!=5 || SendMessageW(H(CURVE),CB_GETCURSEL,0,0)!=0) return 0;
    app.settings.model=7700;rebuild_config();if(ListView_GetItemCount(H(CONFIG))!=44) return 0;
    app.settings.model=7702;rebuild_config();if(ListView_GetItemCount(H(CONFIG))!=84) return 0;
    for(row=0;row<2;row++) {
        ListView_SetItemState(H(CONFIG),-1,0,LVIS_SELECTED);
        ListView_SetItemState(H(CONFIG),row,LVIS_SELECTED|LVIS_FOCUSED,LVIS_SELECTED|LVIS_FOCUSED);
        editor_load();SetWindowTextW(H(RANGE),row?L"10":L"10000");SetWindowTextW(H(NPLC),row?L"0.1":L"5");editor_apply();
    }
    return !app.dirty && app.settings.channels[0].nplc==5 && app.settings.channels[1].nplc==0.1;
}
static void smoke_result(void) {
    wchar_t out[PATH_CAP],csv[PATH_CAP],csv_name[256];char error[ERROR_CAP];uint64_t count=0;FILE *f=NULL;int ok;
    path_join(out,PATH_CAP,app.base_folder,L"smoke_result.txt");unique_name(csv_name,256,L"smoke",L".csv");path_join(csv,PATH_CAP,app.base_folder,csv_name);
    ok=!app.had_error && app.total==6 && app.counts[0]==3 && app.counts[1]==3 && app.current_file[0];
    if(ok) ok=csv_convert(app.current_file,csv,NULL,&count,error,sizeof(error))==1 && count==6;
    _wfopen_s(&f,out,L"wb");if(f) {fprintf(f,"%s\nsamples=%llu\n101=%llu\n102=%llu\neditor=checked\nmodel_switch=checked\n",ok?"PASS":"FAIL",(unsigned long long)app.total,(unsigned long long)app.counts[0],(unsigned long long)app.counts[1]);fclose(f);}
    app.exit_code=ok?0:1;app.smoke_stage=2;PostMessageW(app.window,WM_CLOSE,0,0);
}
static void tick(void) {
    if(app.worker && app.done && WaitForSingleObject(app.worker,0)==WAIT_OBJECT_0) {
        CloseHandle(app.worker);app.worker=NULL;app.done=0;set_busy(0);
        if(!app.had_error) SetWindowTextW(H(STATUS),app.exporting?L"CSV 转换已结束":app.current_file[0]?L"已停止，TXT 已保存，可导出 CSV":L"连接测试结束");
        app.exporting=0;if(app.smoke && app.smoke_stage==1) {smoke_result();return;}
    }
    InvalidateRect(H(GRAPH),NULL,FALSE);if(app.closing && !app.worker) DestroyWindow(app.window);
    if(app.smoke && app.smoke_stage==0) {
        app.smoke_stage=1;
        if(!smoke_configuration()) {app.had_error=1;smoke_result();return;}
        SendMessageW(H(SIM),BM_SETCHECK,BST_CHECKED,0);SetWindowTextW(H(INTERVAL),L"0.05");start_job(0);if(!app.worker) smoke_result();
    }
}
static LRESULT CALLBACK proc(HWND window,UINT message,WPARAM w,LPARAM l) {
    switch(message) {
    case WM_CREATE:app.window=window;create_controls();SetTimer(window,1,100,NULL);return 0;
    case WM_SIZE:layout();return 0;
    case WM_GETMINMAXINFO:{MINMAXINFO *m=(MINMAXINFO *)l;m->ptMinTrackSize.x=1150;m->ptMinTrackSize.y=850;return 0;}
    case WM_COMMAND:{int id=LOWORD(w),code=HIWORD(w);if(app.loading) return 0;
        if(((id==RANGE || id==NPLC || id==SETTLE) && code==EN_CHANGE) || (id==MODE && code==CBN_SELCHANGE)) {app.dirty=1;return 0;}
        if(id==MODEL && code==CBN_SELCHANGE && !app.active) {int selected=(int)SendMessageW(H(MODEL),CB_GETCURSEL,0,0);app.settings.model=selected==0?7702:selected==1?7700:0;rebuild_config();return 0;}
        if(id==CURVE && code==CBN_SELCHANGE) {app.view.unit=(int)SendMessageW(H(CURVE),CB_GETCURSEL,0,0);InvalidateRect(H(GRAPH),NULL,FALSE);return 0;}
        if(code==BN_CLICKED) switch(id) {
        case REFRESH:refresh_ports();break;case BROWSE:browse_folder();break;case APPLY:editor_apply();break;
        case DISABLE:case CLEAR:if(!app.active) {int row=-1;app.loading=1;while((row=ListView_GetNextItem(H(CONFIG),row,id==CLEAR?LVNI_ALL:LVNI_SELECTED))>=0) {app.settings.channels[row_data(CONFIG,row)].enabled=0;ListView_SetCheckState(H(CONFIG),row,FALSE);}app.loading=0;}break;
        case START:start_job(0);break;case PROBE:start_job(1);break;case STOP:stop_job();break;case EXPORT:export_csv();break;
        }return 0;}
    case WM_NOTIFY:{NMHDR *header=(NMHDR *)l;
        if(header->hwndFrom==H(CONFIG) && header->code==LVN_ITEMCHANGED && !app.loading && !app.active) {
            NMLISTVIEW *v=(NMLISTVIEW *)l;if(v->iItem>=0 && (v->uChanged&LVIF_STATE)) {
                if((v->uOldState^v->uNewState)&LVIS_STATEIMAGEMASK) app.settings.channels[row_data(CONFIG,v->iItem)].enabled=ListView_GetCheckState(H(CONFIG),v->iItem)!=0;
                if((v->uOldState^v->uNewState)&LVIS_SELECTED) editor_load();
            }
        }
        if(header->hwndFrom==H(LATEST) && header->code==NM_CUSTOMDRAW) {
            NMLVCUSTOMDRAW *d=(NMLVCUSTOMDRAW *)l;if(d->nmcd.dwDrawStage==CDDS_PREPAINT) return CDRF_NOTIFYITEMDRAW;
            if(d->nmcd.dwDrawStage==CDDS_ITEMPREPAINT) return CDRF_NOTIFYSUBITEMDRAW;
            if(d->nmcd.dwDrawStage==(CDDS_ITEMPREPAINT|CDDS_SUBITEM) && d->iSubItem==0) {
                COLORREF color=plot_color(row_data(LATEST,(int)d->nmcd.dwItemSpec));
                d->clrText=RGB(GetRValue(color)*3/5,GetGValue(color)*3/5,GetBValue(color)*3/5);
            }
        }return 0;}
    case WM_EVENT:receive((Event *)l);return 0;
    case WM_TIMER:tick();return 0;
    case WM_CLOSE:if(app.worker) {app.closing=1;stop_job();EnableWindow(window,FALSE);}else DestroyWindow(window);return 0;
    case WM_DESTROY:KillTimer(window,1);PostQuitMessage(app.exit_code);return 0;
    }return DefWindowProcW(window,message,w,l);
}
int ui_run(HINSTANCE instance,int show,int smoke) {
    INITCOMMONCONTROLSEX controls={sizeof(controls),ICC_LISTVIEW_CLASSES|ICC_STANDARD_CLASSES};WNDCLASSEXW cls={0};
    MSG message;RECT area;wchar_t executable[PATH_CAP],*slash;char error[ERROR_CAP];int i;
    memset(&app,0,sizeof(app));app.smoke=smoke;settings_defaults(&app.settings);
    CoInitializeEx(NULL,COINIT_APARTMENTTHREADED);InitCommonControlsEx(&controls);
    GetModuleFileNameW(NULL,executable,PATH_CAP);slash=wcsrchr(executable,L'\\');if(slash) *slash=0;
    path_join(app.base_folder,PATH_CAP,executable,smoke?L"smoke_output":L"data");wcscpy_s(app.settings.folder,PATH_CAP,app.base_folder);
    app.plot=calloc(1,sizeof(*app.plot));app.stop=CreateEventW(NULL,TRUE,FALSE,NULL);
    if(!app.plot || !app.stop) {MessageBoxW(NULL,L"无法分配运行资源",L"错误",MB_ICONERROR);free(app.plot);if(app.stop) CloseHandle(app.stop);CoUninitialize();return 1;}
    for(i=0;i<=MAX_CHANNELS;i++) app.rows[i]=-1;
    app.font=CreateFontW(-16,0,0,0,FW_NORMAL,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    app.title_font=CreateFontW(-24,0,0,0,FW_SEMIBOLD,FALSE,FALSE,FALSE,DEFAULT_CHARSET,0,0,CLEARTYPE_QUALITY,0,L"Microsoft YaHei UI");
    if(!plot_register(instance)) {win_error(error,sizeof(error),"无法注册绘图窗口");goto failed;}
    cls.cbSize=sizeof(cls);cls.hInstance=instance;cls.lpszClassName=L"Keithley2700C";cls.lpfnWndProc=proc;cls.hCursor=LoadCursor(NULL,IDC_ARROW);
    cls.hIcon=LoadIcon(NULL,IDI_APPLICATION);cls.hbrBackground=(HBRUSH)(COLOR_BTNFACE+1);if(!RegisterClassExW(&cls)) {win_error(error,sizeof(error),"无法注册主窗口");goto failed;}
    SystemParametersInfoW(SPI_GETWORKAREA,0,&area,0);
    app.window=CreateWindowExW(0,cls.lpszClassName,L"Keithley 2700 · 逐通道采集 · C语言版 test_4",WS_OVERLAPPEDWINDOW,CW_USEDEFAULT,CW_USEDEFAULT,1260,min(960,area.bottom-area.top),NULL,NULL,instance,NULL);
    if(!app.window) {win_error(error,sizeof(error),"无法创建主窗口");goto failed;}
    layout();ShowWindow(app.window,smoke?SW_HIDE:show);UpdateWindow(app.window);
    while(GetMessageW(&message,NULL,0,0)>0) {TranslateMessage(&message);DispatchMessageW(&message);}
    CloseHandle(app.stop);DeleteObject(app.font);DeleteObject(app.title_font);free(app.plot);CoUninitialize();return (int)message.wParam;
failed:
    {wchar_t wide[2048];to_wide(error,wide,2048);MessageBoxW(NULL,wide,L"启动失败",MB_ICONERROR);}
    CloseHandle(app.stop);DeleteObject(app.font);DeleteObject(app.title_font);free(app.plot);CoUninitialize();return 1;
}
