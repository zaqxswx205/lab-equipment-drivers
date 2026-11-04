#ifndef K_MODEL_H
#define K_MODEL_H
#include <stddef.h>
#include <stdint.h>
#include <wchar.h>
#define MAX_CHANNELS 84
#define HISTORY_POINTS 2000
#define PATH_CAP 1024
#define ERROR_CAP 1024
#define K_OK 1
#define K_ERROR 0
#define K_CANCEL (-1)
typedef enum { MODE_DCV, MODE_ACV, MODE_DCI, MODE_ACI, MODE_RES, MODE_FRES, MODE_COUNT } MeasureMode;
typedef struct { int channel, enabled; MeasureMode mode; int auto_range; double range, nplc, settle; } ChannelConfig;
typedef struct {
    wchar_t port[64], folder[PATH_CAP];
    int baud, flow, simulation, model;
    double timeout, interval;
    ChannelConfig channels[MAX_CHANNELS], front;
} Settings;
typedef struct {
    char timestamp[32]; int channel; MeasureMode mode; double value, elapsed;
    int valid; uint64_t index;
} Sample;
const char *mode_name(MeasureMode mode);
const wchar_t *mode_name_w(MeasureMode mode);
const char *mode_scpi(MeasureMode mode);
const char *mode_unit(MeasureMode mode);
void settings_defaults(Settings *s);
int channel_number(int index);
int channel_index(int number);
int channel_valid(int model, const ChannelConfig *c);
int config_validate(int model, const ChannelConfig *c, char *error, size_t cap);
int settings_validate(const Settings *s, char *error, size_t cap);
int parse_number(const char *text, double *value);
int parse_reading(const char *text, int expected_channel, double *value, int *valid, char *error, size_t cap);
#endif
