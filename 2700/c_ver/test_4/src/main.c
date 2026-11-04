#include "ui.h"
#include <wchar.h>
#include <locale.h>
int WINAPI wWinMain(HINSTANCE instance,HINSTANCE previous,PWSTR command,int show) {
    (void)previous; setlocale(LC_NUMERIC,"C");
    return ui_run(instance,show,wcscmp(command,L"--smoke-test")==0);
}
