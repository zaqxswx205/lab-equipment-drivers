#ifndef K_INSTRUMENT_H
#define K_INSTRUMENT_H
#include "serial_port.h"
typedef struct {
    SerialPort port; const Settings *settings;
    char identity[256],modules[256],history[768]; int has_cache;
    ChannelConfig cache;
} Instrument;
void instrument_init(Instrument *d,const Settings *s,HANDLE stop);
int instrument_open(Instrument *d,char *error,size_t cap);
int instrument_configure(Instrument *d,char *error,size_t cap);
int instrument_measure(Instrument *d,const ChannelConfig *c,Sample *sample,char *error,size_t cap);
int instrument_close(Instrument *d,char *error,size_t cap);
#endif
