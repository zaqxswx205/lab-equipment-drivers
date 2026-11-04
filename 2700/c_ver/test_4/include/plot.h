#ifndef K_PLOT_H
#define K_PLOT_H
#include "util.h"
typedef struct { double time,value; int valid; } PlotPoint;
typedef struct {
    PlotPoint points[MAX_CHANNELS+1][HISTORY_POINTS];
    unsigned head[MAX_CHANNELS+1],size[MAX_CHANNELS+1];
    MeasureMode modes[MAX_CHANNELS+1]; int used[MAX_CHANNELS+1];
} PlotData;
typedef struct { PlotData *data; int unit; } PlotView;
void plot_reset(PlotData *data);
void plot_add(PlotData *data,const Sample *sample);
COLORREF plot_color(int channel);
int plot_register(HINSTANCE instance);
#endif
