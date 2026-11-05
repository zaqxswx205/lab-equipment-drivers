# GS610 + Keithley 2700 原生 C 版本

本项目学习 `python_ver/test_2` 的程序，在当前 `c_ver/test_1` 中独立实现。
Python 源目录只读，没有修改；`source_hashes.json` 保存迁移开始时十个源文件的 SHA-256，完成后已核对一致。

## 启动

直接运行 `gs610_2700.exe` 打开 Windows 原生界面，无需安装 Python、pyserial 或其他运行时包。
界面按原 Python 设计还原：左上采集设备/驱动设备、右上三列历史表格、下方完整实时曲线。通信参数收在两台设备的串口高级设置弹窗；默认勾选返程测试，采样延迟随保持时间自动更新，开始按钮运行时切换为停止并关闭输出。支持选择保存位置、打开数据文件。
默认 -3.2→3.2 A，步长 0.08 A，限压 12 V，保持 2 s。开始前按实际负载调整参数。2700 FRONT/REAR 实体开关置 F，610 本地 2W 感测。
界面扫描使用 GS610 Linear / INT，中点开始读取2700，再 FETCh 读取610最新内部电压；两者不构成硬件同步。读取晚于目标0.2 s或跨越下一电流点时停止，提示增大保持时间。
扫描和位移搜索、串口识别互斥。运行中修改输入框仅影响下一次运行。

## 命令行

```powershell
# 仅验证参数，不连接仪器
.\gs610_2700.exe --dry-run --start -0.5 --stop 0.5 --step 0.08 --voltage-limit 12 --round-trip

# 与原 main.py 相同类型的逐点软件设置扫描
.\gs610_2700.exe --port2700 COM3 --port610 COM4 --start 0 --stop 0.01 --step 0.001 --voltage-limit 1 --settle 1

# 与原图形界面相同类型的 Linear / INT 扫描
.\gs610_2700.exe --port2700 COM3 --port610 COM4 --linear --period 2 --start -0.5 --stop 0.5 --step 0.08 --voltage-limit 12 --round-trip

# 恒流采集
.\gs610_2700.exe --port2700 COM3 --port610 COM4 --start 0.001 --stop 0.001 --step 0.001 --voltage-limit 1 --samples 100 --interval 1

# 位移极限；成功后只配置扫描范围，不启动扫描
.\gs610_2700.exe --port2700 COM3 --port610 COM4 --limits --period 2 --voltage-limit 12 --lower-voltage -2 --upper-voltage 2

# 只发送身份查询；使用配置的两组通信参数
.\gs610_2700.exe --scan
.\gs610_2700.exe --help
```

串口8N1；默认9600，无流控；610 LF，2700发送CR、响应兼容CR/LF/CRLF。扫描设备发送 `*IDN?`，界面仅在同类设备唯一匹配时自动填入端口。

逐点模式的 `--samples` / `--interval` / `--settle` 仅影响逐点采集；Linear 和位移搜索由 `--period` 决定每点保持时间。
位移搜索从0开始，分别每步−0.08 A / +0.08 A，严格低于负阈值 / 高于正阈值才成功，等于阈值继续；最大±3.2 A。两侧成功才更新界面并配置Linear范围，归零关输出，不启动扫描。失败时保留过程CSV，不更新范围。

正常结束、异常及Ctrl+C均尝试关闭610输出并读回确认；若通信失步，仍发送关闭命令并明确报告“OUTPUT OFF UNCONFIRMED”，需要在仪器面板确认。关闭窗口会等待后台采集退出和输出关闭处理完成。

## 数据与迁移约定

默认 CSV 保存到可执行文件旁的 `result`。自定义目录须已存在。文件独占创建，绝不覆盖；默认文件名含毫秒时间戳。CSV采用UTF-8 BOM，每行刷新并提交磁盘。
统一列：`timestamp,direction,point,set_current_A,drive_voltage_V,voltage_V`。时间为读数完成后的本机时间、精确到秒；方向值为 outbound、return、negative_limit、positive_limit。电流是计划设定值，两个电压依次读取。
相对于 Python：命令行和界面统一CSV格式；命令行也读610电压；往返统一为相同点序反向、转折点重复。非整数步长末点包含在内，Linear拆为等步长段和独立端点，段间存在关输出/重新配置间隙。内部规划以0.1 nA整数单位计算并检查量程分辨率。
界面使用原生控件按Tkinter原版的分组、中文标签、控件顺序、三列表格和蓝红曲线还原，字体与边框由Windows原生控件绘制。这里移植的是 main.py / gui.py 使用的双设备采集流程；keithley2700.py 中独立的7700/7702模块扫描、其他测量功能和TXT转换工具不在此双设备前面板程序的迁移范围。

## 编译与验证

本机运行 `build.cmd`，使用 Visual Studio 2026 C++ Build Tools。也可用已配置Windows SDK的MSVC运行：

```text
cl /std:c11 /utf-8 /O2 /D_CRT_SECURE_NO_WARNINGS main.c core.c gui.c /Fe:gs610_2700.exe /link user32.lib gdi32.lib comdlg32.lib
```

提供 CMakeLists.txt，支持 Windows 上的MSVC/MinGW。本机缺少SDK头文件和库，已从NuGet下载 Microsoft.Windows.SDK.CPP 与 Microsoft.Windows.SDK.CPP.x64 10.0.28000.2957至当前目录 `.toolchain`，build.cmd自动引用。`fetch_sdk.py`仅用于重新下载这些编译依赖，程序运行不依赖它。

- `gs610_2700.exe --self-test`：规划边界、分辨率、点数、非法值检查，通过。
- `python -B verify_reference.py`：只读导入原Python规划器，44组扫描点序交叉比对及6组无效参数检查，通过。返程按界面相同点序反向规则比较。
- 原Python目录十个源文件SHA-256校验一致。

尚未连接真实仪器验证串口协议执行、输出时序或测量精度，界面已打开检查主窗口和高级设置弹窗。实机验证前请按实际负载设置电流/限压和足够保持时间；软件定时不提供硬件同步保证。
