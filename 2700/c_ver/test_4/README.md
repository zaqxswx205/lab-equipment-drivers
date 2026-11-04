# Keithley 2700 逐通道采集 C语言项目（test_4）

将 test_3 的当前功能改写为 **C17 + 原生 Win32 API**。没有嵌入 Python，也不需要 Python 运行时。test_1、test_2、test_3 保持独立。

## 直接使用

运行 **dist/Keithley2700.exe**。目标系统为 Windows 10/11 x64，程序使用静态 C 运行库（MSVC `/MT`），无需安装 Python、pyserial 或额外 VC 运行库。USB转串口适配器驱动仍由其厂家提供。

将 dist 中的 EXE 与 README.txt 复制到其他电脑的可写目录即可使用。默认数据目录为 EXE 旁的 data/，也可通过界面选择。详细操作见 dist/README.txt。

## 保留的功能

- RS-232：COM口枚举及手动输入、300–19200波特率、8N1、可选XON/XOFF、固定CR发送、自动识别CR/LF回复。
- 2700型号核对、FRONT/REAR实体开关检查、7702/7700两个槽位查询、历史错误与新增错误处理。
- 每通道独立功能、量程、NPLC、稳定时间；默认101测两线电阻，102测直流电压，稳定时间0.5秒。
- 支持六种功能：DCV、ACV、DCI、ACI、两线电阻、四线电阻；电流通道和四线配对冲突校验。
- 跨两个同型号模块采集，以及原前面板模式；不支持同轮混用7700和7702。
- 工作线程处理串口、磁盘与导出；主界面接收独立消息快照，停止/关窗时请求取消并等待清理。
- 实时表格、按Ω/V/A选择的多通道曲线，每通道最多保留2000点，超量程曲线断开。
- 逐点UTF-8 TXT持久化：WriteFile + FlushFileBuffers，完成后才通知界面；独占创建，避免覆盖已有文件。
- TXT转CSV（UTF-8 BOM），兼容test_3历史扩展字段、test_2及旧前面板TXT。输出仅保留六列。
- 模拟模式、通信日志、串口取消与清理。

## 文件格式

```text
timestamp channel mode unit value status
```

实际TXT以制表符分隔；CSV以逗号分隔并进行字段引号转义。时间是电脑接收处理后的本地时间，精确到毫秒。OVERLOAD时value留空，status为OVERLOAD。

raw、source、range_setting、nplc_setting、settle_s不进入测量数据文件。历史TXT转换时移除多余列，原文件不变。模拟数据通过界面状态和SIM文件名前缀识别。

## 源码按模块组织

| 文件 | 职责 |
| --- | --- |
| src/main.c | Windows程序入口 |
| src/model.c + include/model.h | 参数类型、默认值、功能和通道规则、数字/读数解析 |
| src/util.c + include/util.h | UTF-8/UTF-16、时间、路径和平台错误辅助 |
| src/serial_port.c + include/serial_port.h | Windows串口、终止符、超时/取消、可注入测试传输层 |
| src/instrument.c + include/instrument.h | 2700 SCPI指令、型号/槽位检查、错误队列、逐通道参数和清理 |
| src/storage.c + include/storage.h | 六列TXT保存与旧格式CSV转换 |
| src/acquisition.c + include/acquisition.h | 采集/导出工作线程、模拟源、事件传递 |
| src/plot.c + include/plot.h | 环形历史缓存、按单位绘图、曲线颜色 |
| src/ui.c + include/ui.h | 原生中文界面、逐通道编辑、窗口消息和线程生命周期 |
| tests/test_core.c | C语言离线测试与模拟串口 |
| app.manifest | 系统控件样式、DPI和执行权限声明 |
| build.bat / test.bat | 本机编译、核心测试和界面模拟测试 |
| CMakeLists.txt | 可选MSVC/CMake工程配置 |

采集过程沿用软件逐通道轮询：检查输入 → 打开通道 → 应用目标功能和参数 → 接通系统通道 → 等待其稳定时间 → READ? → 校验返回通道 → 保存 → 刷新。参数完全相同时复用已确认参数，仍进行通道切换。交流带宽固定300Hz，以支持NPLC设置。

每轮间隔是目标轮询周期，不保证实际采样率。前面板没有通道切换，稳定时间仅用于模块通道。

## 从源码编译

运行成品不需要编译环境。只有修改源码时，才需要 Visual Studio 2022 的“使用C++的桌面开发”组件及Windows SDK；该工具链同时支持C语言。

```bat
build.bat
```

脚本通过vswhere查找MSVC，配置x64环境，使用 `/std:c17 /utf-8 /W4 /O2 /MT` 编译。输出为 dist/Keithley2700.exe；中间产物在build/。

可选CMake方式（配置已提供，主要交付构建以实际运行过的build.bat为准）：

```bat
cmake -S . -B build-cmake -A x64
cmake --build build-cmake --config Release
ctest --test-dir build-cmake -C Release --output-on-failure
```

## 测试与验证

```bat
test.bat
```

脚本执行C核心测试、重建EXE、再运行隐藏的原生界面模拟测试。GUI测试仅使用模拟源，不打开真实串口，结果保存在dist/smoke_output/smoke_result.txt。

核心测试覆盖：默认值/参数校验、7702/7700通道限制、四线冲突、读数解析、超量程、CR/LF回复、取消/超时、历史错误、模块缺失、参数错误时禁止接通、混合六功能及独立量程/NPLC、先落盘后通知、六列输出、CSV BOM/引号/旧格式、拒绝覆盖、异常时移除不完整CSV、曲线环形缓存。

GUI测试覆盖型号切换、逐通道编辑/应用、101电阻与102电压模拟采集、工作线程完成、TXT保存及CSV转换。

实际完成：本机MSVC Release编译无警告；核心测试0失败；原生界面模拟测试PASS。dumpbin依赖检查仅包含 USER32、GDI32、SHELL32、ole32、COMCTL32、COMDLG32、KERNEL32 等Windows系统DLL，没有Python或动态VC运行库依赖。

未在其他实体电脑或真实2700/7702上验证C版本。首次实机运行建议从两个已知信号开始，核对功能、通道和文件，再增加通道。

## 官方依据

沿用test_3已核对的指令流程：[Keithley 2700用户手册](https://download.tek.com/manual/2700-900-01K_Feb_2016.pdf)、[7702模块说明](https://download.tek.com/manual/PA-695E_7702_for_2750_July_2023.pdf)、[7700模块说明](https://download.tek.com/manual/077182400_7700_for_2750_July_2023.pdf)。主机SCPI行为以2700手册为准。
