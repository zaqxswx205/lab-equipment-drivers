# 实验室设备驱动程序

各类仪器上位机控制程序的集合。每台设备按语言分两条线：

```
<设备>/
├── c_ver/          C / C++ 版本
└── python_ver/     Python 版本
```

## 设备一览

| 目录 | 设备 | C 版 | Python 版 |
| --- | --- | --- | --- |
| [`2700/`](2700/) | Keithley 2700 数据采集 / 万用表 | [`c_ver/test_4`](2700/c_ver/test_4/) | 待开发 |
| [`7722/`](7722/) | ITECH IT-M7722 可编程交流电源 | 待开发 | [`python_ver/test_1`](7722/python_ver/test_1/) |
| [`7722_2700/`](7722_2700/) | 7722 + 2700 联调（加热片驱动 + Pt100 采集） | 待开发 | [`python_ver/加热片_test3`](7722_2700/python_ver/加热片_test3/) |
| [`gs610/`](gs610/) | Yokogawa GS610 源表 | [`c_ver/gs610_driver_test_4`](gs610/c_ver/gs610_driver_test_4/) | [`python_ver/test_5`](gs610/python_ver/test_5/) |

## 各项目

### 2700 — Keithley 2700 逐通道采集（C17 + Win32）

[`2700/c_ver/test_4/README.md`](2700/c_ver/test_4/README.md)

按通道采集 DCV / ACV / DCI / ACI / 两线电阻 / 四线电阻，支持 7700、7702 两种模块跨槽采集。静态链接 MSVC 运行库，目标机器不需要 Python。构建见同目录 `build.bat`。

### 7722 — ITECH IT-M7722 电源（Python / Tkinter）

[`7722/python_ver/test_1/README.md`](7722/python_ver/test_1/README.md)

串口 / VISA 访问，默认 `COM6 / 9600`。支持定值输出、序列执行、闭环功率控制与保护。测试：

```powershell
python -m unittest -v test_itech7722 test_regressions test_feedback test_recording_schema test_keypoints
```

### 7722_2700 — 加热片交流驱动 + Pt100 采集（Python / Tkinter）

[`7722_2700/python_ver/加热片_test3/heater_control/README.md`](7722_2700/python_ver/加热片_test3/heater_control/README.md)

7722 输出交流驱动加热片，2700 同步采集 Pt100 温度与阻值，按时间方案或温度方案执行，带越限保护。两个设备可以分别启停。

### gs610 — Yokogawa GS610 电流扫描

- C 版：[`gs610/c_ver/gs610_driver_test_4/README.md`](gs610/c_ver/gs610_driver_test_4/README.md) — Win32 + GDI 界面，无运行时依赖
- Python 版：[`gs610/python_ver/test_5/README.md`](gs610/python_ver/test_5/README.md) — Tkinter 界面，需要 Python 3.8+

恒流输出、电压测量，按步进扫描并逐点写 CSV。

## 环境要求

| | 需要 |
| --- | --- |
| Python 程序 | Python 3.8+，含 Tkinter；依赖见各目录 `requirements.txt`（主要是 `pyserial`） |
| C 程序 | Visual Studio 2022 或 Build Tools 的“使用 C++ 的桌面开发”组件 |
| 硬件 | 串口为 USB 转串口，需安装对应驱动（PL2303 等） |

## 目录说明

- `history/` — 各设备历史版本，**仅本地保留，不纳入版本管理**
- `support/` — 驱动安装包、旧软件备份，**仅本地保留**

历史版本请用 git 查看，不要再靠复制文件夹保存：

```bash
git log --oneline                  # 全部提交
git log --oneline -- <文件路径>     # 单个文件的演进
git tag                            # 已标记的发布版本
```
