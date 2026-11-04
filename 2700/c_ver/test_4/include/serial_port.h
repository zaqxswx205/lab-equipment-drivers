#ifndef K_SERIAL_H
#define K_SERIAL_H
#include "util.h"
#include <stdio.h>
typedef struct {
    int (*write)(void *context,const char *data,size_t length,char *error,size_t cap);
    int (*read)(void *context,char *byte,char *error,size_t cap);
    void (*close)(void *context);
} SerialOps;
typedef struct {
    HANDLE handle,stop; SerialOps ops; void *context;
    double timeout; int attached,pending; FILE *trace;
} SerialPort;
void serial_init(SerialPort *p,HANDLE stop,double timeout);
void serial_attach(SerialPort *p,SerialOps ops,void *context);
int serial_open(SerialPort *p,const Settings *s,char *error,size_t cap);
int serial_write(SerialPort *p,const char *command,char *error,size_t cap);
int serial_raw(SerialPort *p,const char *data,size_t length,char *error,size_t cap);
int serial_query(SerialPort *p,const char *command,char *reply,size_t reply_cap,char *error,size_t cap);
void serial_trace(SerialPort *p,const char *direction,const char *text);
void serial_close(SerialPort *p);
int serial_list(wchar_t names[][64],int capacity);
#endif
