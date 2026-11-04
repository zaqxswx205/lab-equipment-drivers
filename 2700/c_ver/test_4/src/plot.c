#include "plot.h"
#include <stdio.h>
#include <math.h>
#include <string.h>
static int index_for(int channel) { return channel?channel_index(channel):MAX_CHANNELS; }
static int unit_for(MeasureMode mode) { return mode==MODE_RES || mode==MODE_FRES?0:mode==MODE_DCV || mode==MODE_ACV?1:2; }
void plot_reset(PlotData *d) { memset(d,0,sizeof(*d)); }
void plot_add(PlotData *d,const Sample *s) {
    int i=index_for(s->channel); unsigned at;
    if(i<0) return;
    at=d->head[i]; d->points[i][at]=(PlotPoint){s->elapsed,s->value,s->valid};
    d->head[i]=(at+1)%HISTORY_POINTS; if(d->size[i]<HISTORY_POINTS) d->size[i]++;
    d->modes[i]=s->mode; d->used[i]=1;
}
COLORREF plot_color(int channel) {
    int idx=index_for(channel); double h=fmod((idx+1)*0.61803398875,1.0)*6;
    double x=1-fabs(fmod(h,2.0)-1); int r=0,g=0,b=0,k=(int)h;
    if(k==0) {r=210;g=(int)(210*x);} else if(k==1) {r=(int)(210*x);g=210;} else if(k==2) {g=210;b=(int)(210*x);} else if(k==3) {g=(int)(210*x);b=210;} else if(k==4) {r=(int)(210*x);b=210;} else {r=210;b=(int)(210*x);}
    return RGB(55+r*3/4,55+g*3/4,55+b*3/4);
}
static void paint_graph(HDC dc,RECT rect,PlotView *view) {
    int i,tick,left=95,top=26,right=rect.right-20,bottom=rect.bottom-34,found=0;
    double lo=0,hi=1,xlo=0,xhi=10,pad; wchar_t text[128];
    HBRUSH bg=CreateSolidBrush(RGB(16,28,48)); HPEN grid=CreatePen(PS_SOLID,1,RGB(42,57,80)); HGDIOBJ oldpen;
    FillRect(dc,&rect,bg); DeleteObject(bg); SetBkMode(dc,TRANSPARENT); SetTextColor(dc,RGB(188,203,224));
    if(right<=left || bottom<=top) { DeleteObject(grid); return; }
    for(i=0;i<=MAX_CHANNELS;i++) if(view->data->used[i] && unit_for(view->data->modes[i])==view->unit) {
        unsigned j,n=view->data->size[i],start=(view->data->head[i]+HISTORY_POINTS-n)%HISTORY_POINTS;
        for(j=0;j<n;j++) { PlotPoint *p=&view->data->points[i][(start+j)%HISTORY_POINTS];
            if(!p->valid) continue;
            if(!found) { lo=hi=p->value; xlo=xhi=p->time; found=1; }
            else { if(p->value<lo) lo=p->value; if(p->value>hi) hi=p->value; if(p->time<xlo) xlo=p->time; if(p->time>xhi) xhi=p->time; }
        }
    }
    pad=fmax(fmax((hi-lo)*0.1,fabs(hi)*1e-6),1e-12); lo-=pad;hi+=pad; if(xhi<xlo+1) xhi=xlo+1;
    oldpen=SelectObject(dc,grid);
    for(tick=0;tick<=5;tick++) {
        int y=top+(bottom-top)*tick/5,x=left+(right-left)*tick/5;
        RECT label={0,y-8,left-8,y+12}; MoveToEx(dc,left,y,NULL);LineTo(dc,right,y);
        swprintf_s(text,128,L"%.6g",hi-(hi-lo)*tick/5);DrawTextW(dc,text,-1,&label,DT_RIGHT|DT_SINGLELINE);
        label=(RECT){x-45,bottom+8,x+45,bottom+28};swprintf_s(text,128,L"%.1fs",xlo+(xhi-xlo)*tick/5);DrawTextW(dc,text,-1,&label,DT_CENTER|DT_SINGLELINE);
    }
    SelectObject(dc,oldpen);DeleteObject(grid);
    TextOutW(dc,left,5,view->unit==0?L"Ω":view->unit==1?L"V":L"A",1);
    for(i=0;i<=MAX_CHANNELS;i++) if(view->data->used[i] && unit_for(view->data->modes[i])==view->unit) {
        unsigned j,n=view->data->size[i],start=(view->data->head[i]+HISTORY_POINTS-n)%HISTORY_POINTS; int segment=0;
        HPEN pen=CreatePen(PS_SOLID,2,plot_color(i==MAX_CHANNELS?0:channel_number(i)));oldpen=SelectObject(dc,pen);
        for(j=0;j<n;j++) { PlotPoint *p=&view->data->points[i][(start+j)%HISTORY_POINTS]; int x,y;
            if(!p->valid) {segment=0;continue;}
            x=left+(int)((p->time-xlo)/(xhi-xlo)*(right-left));y=bottom-(int)((p->value-lo)/(hi-lo)*(bottom-top));
            if(!segment) {MoveToEx(dc,x,y,NULL);SetPixelV(dc,x,y,plot_color(i==MAX_CHANNELS?0:channel_number(i)));segment=1;} else LineTo(dc,x,y);
        }
        SelectObject(dc,oldpen);DeleteObject(pen);
    }
    if(!found) { RECT message={left,top,right,bottom}; DrawTextW(dc,L"等待该单位的通道数据",-1,&message,DT_CENTER|DT_VCENTER|DT_SINGLELINE); }
}
static LRESULT CALLBACK plot_proc(HWND window,UINT message,WPARAM w,LPARAM l) {
    PlotView *view=(PlotView *)GetWindowLongPtrW(window,GWLP_USERDATA);
    if(message==WM_CREATE) { CREATESTRUCTW *create=(CREATESTRUCTW *)l;SetWindowLongPtrW(window,GWLP_USERDATA,(LONG_PTR)create->lpCreateParams);return 0; }
    if(message==WM_ERASEBKGND) return 1;
    if(message==WM_PRINTCLIENT && view) { RECT rect;GetClientRect(window,&rect);paint_graph((HDC)w,rect,view);return 0; }
    if(message==WM_PAINT) {
        PAINTSTRUCT ps; RECT r; HDC dc=BeginPaint(window,&ps); GetClientRect(window,&r);
        if(view && r.right>0 && r.bottom>0) {
            HDC buffer=CreateCompatibleDC(dc);HBITMAP bitmap=CreateCompatibleBitmap(dc,r.right,r.bottom);HGDIOBJ old=SelectObject(buffer,bitmap);
            HFONT font=(HFONT)GetStockObject(DEFAULT_GUI_FONT);HGDIOBJ oldfont=SelectObject(buffer,font);
            paint_graph(buffer,r,view);BitBlt(dc,0,0,r.right,r.bottom,buffer,0,0,SRCCOPY);
            SelectObject(buffer,oldfont);SelectObject(buffer,old);DeleteObject(bitmap);DeleteDC(buffer);
        } EndPaint(window,&ps);return 0;
    }
    return DefWindowProcW(window,message,w,l);
}
int plot_register(HINSTANCE instance) { WNDCLASSW c={0};c.hInstance=instance;c.lpszClassName=L"KeithleyPlot";c.lpfnWndProc=plot_proc;c.hCursor=LoadCursor(NULL,IDC_ARROW);return RegisterClassW(&c)!=0; }
