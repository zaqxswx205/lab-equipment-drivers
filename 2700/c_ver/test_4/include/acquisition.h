#ifndef K_ACQUISITION_H
#define K_ACQUISITION_H
#include "instrument.h"
#include "storage.h"
typedef enum { EVENT_SAMPLE, EVENT_INFO, EVENT_WARNING, EVENT_MODULES, EVENT_FILE, EVENT_ERROR, EVENT_DONE, EVENT_EXPORTED } EventKind;
typedef struct Event { EventKind kind; Sample sample; char text[ERROR_CAP]; wchar_t path[PATH_CAP]; uint64_t count; } Event;
/* Emit takes ownership only for a successful callback; all UI notifications are
   heap snapshots, never pointers to worker-local objects. */
typedef int (*EmitEvent)(void *context,Event *event);
typedef struct {
    Settings settings; HANDLE stop; EmitEvent emit; void *context;
    SerialOps serial_ops; void *serial_context; /* optional injected transport for tests */
    int probe; unsigned max_rounds; /* finite rounds are useful for offline tests */
} AcquisitionJob;
typedef struct { wchar_t source[PATH_CAP],destination[PATH_CAP]; HANDLE stop; EmitEvent emit; void *context; } ExportJob;
unsigned __stdcall acquisition_thread(void *arg);
unsigned __stdcall export_thread(void *arg);
#endif
