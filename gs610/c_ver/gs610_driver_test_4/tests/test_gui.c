/* Native GUI integration test; all acquisition uses explicit demo mode. */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#define wWinMain gui_application_main
#include "../gs610_gui.c"
#undef wWinMain

static void pump_until_idle(void) {
    ULONGLONG started=GetTickCount64();
    while(ui.busy) {
        MSG msg;
        while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)) {TranslateMessage(&msg); DispatchMessageW(&msg);}
        assert(GetTickCount64()-started<10000); Sleep(1);
    }
}
static void capture_preview(void) {
    RECT r; GetWindowRect(ui.window,&r); int w=r.right-r.left,h=r.bottom-r.top;
    HDC dc=GetDC(ui.window),memory=CreateCompatibleDC(dc);
    HBITMAP image=CreateCompatibleBitmap(dc,w,h); HGDIOBJ old=SelectObject(memory,image);
    assert(PrintWindow(ui.window,memory,0)); SelectObject(memory,old);
    BITMAPINFO bmi={0}; bmi.bmiHeader.biSize=sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth=w; bmi.bmiHeader.biHeight= -h; bmi.bmiHeader.biPlanes=1; bmi.bmiHeader.biBitCount=32;
    DWORD size=(DWORD)(w*h*4); void *pixels=malloc(size); assert(pixels);
    assert(GetDIBits(memory,image,0,(UINT)h,pixels,&bmi,DIB_RGB_COLORS));
    BITMAPFILEHEADER header={0}; header.bfType=0x4d42;
    header.bfOffBits=sizeof(header)+sizeof(BITMAPINFOHEADER); header.bfSize=header.bfOffBits+size;
    FILE *file=fopen("build/gui-preview.bmp","wb"); assert(file);
    assert(fwrite(&header,sizeof(header),1,file)==1);
    assert(fwrite(&bmi.bmiHeader,sizeof(BITMAPINFOHEADER),1,file)==1);
    assert(fwrite(pixels,size,1,file)==1); assert(fclose(file)==0);
    free(pixels); DeleteObject(image); DeleteDC(memory); ReleaseDC(ui.window,dc);
}
int main(void) {
    HINSTANCE instance=GetModuleHandleW(NULL); ui.scale=96;
    ui.font=CreateFontW(-15,0,0,0,FW_NORMAL,0,0,0,DEFAULT_CHARSET,0,0,0,0,L"Microsoft YaHei UI");
    ui.title_font=CreateFontW(-26,0,0,0,FW_SEMIBOLD,0,0,0,DEFAULT_CHARSET,0,0,0,0,L"Microsoft YaHei UI");
    ui.settings.baud=9600; ui.settings.bits=8; ui.settings.parity='N'; ui.settings.stops=1;
    ui.settings.timeout=5; ui.settings.dwell=0.1; strcpy(ui.settings.flow,"none"); strcpy(ui.settings.term,"LF");
    WNDCLASSW cls={0}; cls.hInstance=instance; cls.hbrBackground=(HBRUSH)(COLOR_WINDOW+1);
    cls.lpfnWndProc=plot_proc; cls.lpszClassName=L"GS610Plot"; assert(RegisterClassW(&cls));
    cls.lpfnWndProc=advanced_proc; cls.lpszClassName=L"GS610Advanced"; assert(RegisterClassW(&cls));
    cls.lpfnWndProc=main_proc; cls.lpszClassName=L"GS610Main"; assert(RegisterClassW(&cls));
    HWND window=CreateWindowW(L"GS610Main",L"GS610 · 界面集成测试（模拟数据）",WS_OVERLAPPEDWINDOW,
        20,20,1180,870,NULL,NULL,instance,NULL); assert(window);
    ShowWindow(window,SW_SHOW); UpdateWindow(window);
    assert(is_gs610("Yokogawa Electric Co.,765501,SERIAL,1.0"));
    assert(!is_gs610("OTHER,GS610-OTHER,SERIAL,1.0"));
    open_advanced(); assert(ui.advanced && !IsWindowEnabled(window));
    SendMessageW(advanced_controls[0],CB_SETCURSEL,5,0);
    SendMessageW(advanced_controls[4],CB_SETCURSEL,2,0);
    SendMessageW(advanced_controls[5],CB_SETCURSEL,2,0);
    SetWindowTextW(advanced_controls[6],L"0.5");
    SendMessageW(ui.advanced,WM_COMMAND,ID_APPLY,0);
    assert(!ui.advanced && IsWindowEnabled(window));
    assert(ui.settings.baud==115200 && !strcmp(ui.settings.flow,"rtscts"));
    assert(!strcmp(ui.settings.term,"CRLF") && ui.settings.timeout==0.5);
    open_advanced(); SendMessageW(advanced_controls[0],CB_SETCURSEL,0,0);
    SendMessageW(ui.advanced,WM_COMMAND,ID_DISCARD,0); assert(ui.settings.baud==115200);
    wchar_t temp[PATH_CAP],folder[PATH_CAP],csv[PATH_CAP];
    assert(GetTempPathW(PATH_CAP,temp)); assert(GetTempFileNameW(temp,L"gui",0,folder));
    assert(DeleteFileW(folder)); assert(CreateDirectoryW(folder,NULL));
    swprintf(csv,PATH_CAP,L"%ls\\演示.csv",folder); SetWindowTextW(control(ID_CSV),csv);
    SendMessageW(control(ID_DEMO),BM_SETCHECK,BST_CHECKED,0);
    SetWindowTextW(control(ID_START),L"-0.01"); SetWindowTextW(control(ID_STOP),L"0.01");
    SetWindowTextW(control(ID_STEP),L"0.001"); SetWindowTextW(control(ID_LIMIT),L"5");
    SetWindowTextW(control(ID_DWELL),L"0");
    start_scan(); assert(ui.busy && !IsWindowEnabled(control(ID_RUN)));
    pump_until_idle(); assert(ui.successful && ui.count==42 && ui.turn==21);
    assert(ui.records[0].voltage== -0.02 && ui.records[20].voltage==0.02 && ui.records[41].voltage== -0.02);
    assert(!IsWindowEnabled(control(ID_CANCEL)) && IsWindowEnabled(control(ID_RUN)));
    FILE *f=_wfopen(csv,L"rb"); assert(f); char text[8192]={0};
    assert(fread(text,1,sizeof(text)-1,f)>0); assert(fclose(f)==0);
    assert(strstr(text,"return_measured_voltage_V") && strstr(text,"42,"));
    /* Let themed controls finish their enabled-state animation before the preview. */
    ULONGLONG painted=GetTickCount64();
    while(GetTickCount64()-painted<250) {
        MSG msg; while(PeekMessageW(&msg,NULL,0,0,PM_REMOVE)) {TranslateMessage(&msg); DispatchMessageW(&msg);}
        Sleep(10);
    }
    UpdateWindow(ui.plot); capture_preview(); assert(DeleteFileW(csv));
    swprintf(csv,PATH_CAP,L"%ls\\stopped.csv",folder); SetWindowTextW(control(ID_CSV),csv);
    SetWindowTextW(control(ID_DWELL),L"1"); start_scan(); assert(ui.busy);
    SendMessageW(window,WM_COMMAND,ID_CANCEL,0); pump_until_idle();
    assert(!ui.successful && ui.count==0 && GetFileAttributesW(csv)!=INVALID_FILE_ATTRIBUTES);
    assert(DeleteFileW(csv));
    swprintf(csv,PATH_CAP,L"%ls\\close.csv",folder); SetWindowTextW(control(ID_CSV),csv);
    start_scan(); assert(ui.busy); SendMessageW(window,WM_CLOSE,0,0);
    assert(ui.close_pending && IsWindow(window)); pump_until_idle(); assert(!IsWindow(window));
    assert(DeleteFileW(csv)); assert(RemoveDirectoryW(folder));
    DeleteObject(ui.font); DeleteObject(ui.title_font); puts("GUI integration tests passed (demo only, no serial I/O).");
    return 0;
}
