# 实验室设备 Python 库

从 `7722/python_ver/test_1` 和 `2700/python_ver/test_3` 提取，原项目未修改。
Python 3.10+。两个库均为独立文件，可单独复制到其他项目；不依赖原目录、Tkinter 或 matplotlib。

## 安装

在目标项目使用的 Python 环境执行：

```powershell
python -m pip install "C:\Project\lab_equipment_drivers\modules[serial]"
# 需要 VISA / USBTMC 时：
python -m pip install "C:\Project\lab_equipment_drivers\modules[serial,visa]"
```

也可复制 `itech7722.py`、`keithley2700.py` 到项目中，串口安装 `pyserial`，VISA 安装 `pyvisa` 和相应后端。仿真仅需标准库。

## 功能范围

本库覆盖这两个项目已实现的非界面功能，不代表仪器手册的全部命令。
7722 默认 SCPI 映射沿用原项目的 IT-M7700 系列指令；不同型号/固件可以通过 `scpi_map` 或 INI 的 SCPI 段覆盖，需实际设备确认。
7700、7702 是 2700 的插卡，必须通过主机通信，不具备单独串口。

| 文件 | 已包含的功能 |
|---|---|
| itech7722.py | 串口、TCP、VISA、USBTMC、离线仿真；AC/DC/ACDC；电压/频率设定；电流限幅；OVP/OCP/OPP；输出开关；电压/电流/功率/频率/功率因数回读；错误队列、SCPI 映射与原始命令；INI 配置；CSV/TXT 时序解析；定值和循环时序；软件 CI/CP 电压闭环；关键点捕获；CSV 数据与关键点记录；后台运行与停止 |
| keithley2700.py | RS232；设备身份及前后面板开关检查；7700/7702 槽位识别；DCV/ACV/DCI/ACI/两线和四线电阻；自动/手动量程、NPLC、切换稳定时间；逐通道混合配置；四线感测通道冲突校验；过载和通道归属校验；错误队列；连续采集、仿真、TXT 持久化、CSV 转换、通信日志 |

温度、频率等未在原 2700 项目实现的测量功能没有虚构高层接口。可通过 `write/query/checked_write` 扩展 SCPI；调用者需自行保证参数、路由与解析一致。

## 7722：直接控制

```python
from itech7722 import ITECH7722

# 真机改为 {"type": "serial", "port": "COM6", "baudrate": 9600,
#            "timeout": 2, "terminator": r"\r\n"}
with ITECH7722({"type": "sim"}) as source:
    print(source.identify())
    source.prepare_run()  # 读出旧错误，进入远程并启用逐条错误检查
    source.set_function_mode("DC")  # AC / DC / ACDC
    source.set_protection_limits(5, 1, 5)  # V、A、W，按实验设置
    source.apply_setting("CV", 1, 0)  # 电压 V，频率 Hz（DC 为 0）
    source.output_on()
    print(source.measure())
# ITECH7722 在退出时尝试关闭输出并释放连接；通信故障会抛异常。
```

其他连接：`{"type":"tcp","host":"192.168.1.10","tcp_port":9999}`；
`{"type":"visa","resource":"..."}`；`{"type":"usb","usb_resource":"USB0::...::INSTR"}`。
USBTMC 不等同于 USB 转串口，后者使用 serial。

主要接口：`identify`、`prepare_run`、`set_function_mode`、`set_protection_limits`、
`apply_setting`、`set_voltage`、`output_on/off`、`measure`、`error_queue`、
`clear_errors`、`probe`、`raw`、`close`。
`set_voltage` 要求先设置电压保护上限。真实设备 `apply_setting` 仅支持 CV；CI/CP 通过下面的运行引擎进行软件闭环，不能将保护限值当成恒流/恒功率设定。
保留兼容接口 `ITech7722(transport, scpi_map=None, log=None)`；其 `close()` 仅释放连接，推荐新项目用 `ITECH7722`。

## 7722：时序、闭环、记录与配置

```python
import time
from itech7722 import AppConfig, ITECHEngine, Recorder

cfg = AppConfig()
cfg.connection = {"type": "sim"}
cfg.limits = {"max_voltage": 5., "max_current": 1., "max_power": 5.}
cfg.setting.update(mode="DC", control="CV", value=1., frequency=0., output_mode="hold")
cfg.recording.update(log_file="data.csv", keypoint_file="keys.csv", interval_ms=200)
cfg.keypoints = {1.0: ["voltage", "current", "power"]}

with ITECHEngine() as engine:
    print(engine.connect(cfg))
    engine.start_run(cfg, [], Recorder(cfg, work_dir="."))
    time.sleep(1)
    engine.stop()
    while not engine.events.empty():
        print(engine.events.get())
    if engine.last_error:
        raise RuntimeError(engine.last_error)
```

- 时序：`load_sequence(path)` 返回 `Step` 列表；设 `cfg.setting['output_mode']='seq'`，将列表传给 `start_run`；`cfg.sequence['loop']` 控制循环。CSV 表头可用 `duration,control,value,frequency`，例如 `1,CV,1,50`。也保留原来的 `volt,curr,power` 列格式。
- 闭环：定值模式中将 `control` 设为 `CI` 或 `CP`，`value` 单位分别为 A 或 W，并配置 `cfg.feedback` 的电压上限、步长、周期、容差和超时。引擎使用 `VoltageFeedback` 调节电压，有限幅、超时、回读和保护检查。时序 CI/CP 保持原项目的校验限制。
- 配置：`load_config(path)` / `save_config(path, cfg)`；`validate_run(cfg, steps)` 可提前校验。`AppConfig.copy()` 深复制。
- 记录：`Recorder`、`Sample.now(...)`；支持原项目记录格式、旧表头备份、关键设定电压捕获、运行中 `engine.update_keypoints(rules)`。
- `ITECHEngine.events` 的事件为 `(kind, data)`，包含 sample、step、info、alarm、stopped；也可传入 `on_event` / `on_scpi` 回调。回调可能在工作线程执行，必须线程安全且不抛异常。图表由调用项目自行绘制。
- `engine.stop(timeout=3)` 返回是否已停止；停止失败时 `disconnect()` 抛异常并保留连接。不要在后台运行期间直接调用同一设备修改输出。

## 2700：前面板与插卡

```python
from keithley2700 import Keithley2700, Settings, Keithley7700, Keithley7702

# 前面板：实体开关 F
with Keithley2700(Settings(port="COM3", mode="直流电压 DCV")) as meter:
    raw, value, status = meter.measure()  # 超量程 value=None, status='OVERLOAD'
    print(raw, value, status)

# 7700：实体开关 R，完整通道号 1xx/2xx 对应槽位 1/2
with Keithley7700(port="COM3", channels=(101, 102), settle=0.5) as card:
    print(card.measure(101))
    print(card.read_all())

# 7702 用法相同，电压/两线电阻通道可到 140/240
# with Keithley7702(port="COM3", channels=(101, 140)) as card: ...
```

`open()` 自动校验、识别并配置；可手动 open/close，也可用 with。配置过程沿用原项目的 `*RST`。每个实例只能由单一调用线程控制。
可用模式字符串：`直流电压 DCV`、`交流电压 ACV`、`直流电流 DCI`、`交流电流 ACI`、`两线电阻 Ω2`、`四线电阻 Ω4`。

| 模块 | 电压/两线电阻（每槽） | 四线电阻起始通道 | 电流通道 |
|---|---|---|---|
| 7700 | 01–20 | 01–10，配对偏移 10 | 21、22 |
| 7702 | 01–40 | 01–20，配对偏移 20 | 41、42 |

`Keithley7700.valid_channels(mode, slot=1)` / `Keithley7702.valid_channels(...)` 列出合法完整编号。四线测量占用配对感测通道，配置冲突会在连接前报错。
一个采集配置沿用原项目规则，只选择一种卡型号；不在一次配置中混用 7700 和 7702。

逐通道配置：

```python
from keithley2700 import ChannelConfig, Keithley7702

with Keithley7702(
    port="COM3", channels=(101, 102),
    channel_configs=(
        ChannelConfig(101, "直流电压 DCV", "AUTO", 1.0, 0.5),
        ChannelConfig(102, "两线电阻 Ω2", "10000", 1.0, 0.5),
    ),
) as card:
    print(card.read_all())
```

`Settings` 还支持 baud、flow、timeout、interval、measure_range、nplc。
2700 发出命令固定以 CR 结束；接收自动识别 CR/LF/CRLF，保留原项目通信逻辑。

## 2700：连续采集和导出（可直接离线运行）

```python
import threading
from keithley2700 import Settings, collect, convert_csv

stop = threading.Event()
def emit(kind, data):
    if kind == "sample":
        index, row, elapsed = data
        print(row)
        if index >= 5:
            stop.set()
    elif kind == "error":
        print("采集失败:", data)

collect(Settings(simulation=True), "measurements.txt", stop, emit)
convert_csv("measurements.txt", "measurements.csv")
```

真机将 simulation 改为 False 并设置端口和 input_source（FRONT/7700/7702）。
`collect` 同步运行，可自行放入线程，停止时设置 Event；异常通过 error 事件报告，结束发送 done。TXT/CSV 不覆盖已有文件。通信日志默认写入库文件旁的 logs 目录，真机连续采集时该目录需可写；手动测量可用 `trace(direction, data)` 自行接日志。
保留原始 `Instrument(settings, stop, serial_factory=None, trace=None)` 接口，使用它时需自行 validate/open/configure/close。

## 验证

在本仓库 modules 目录运行 `python verify_original.py`：将原测试导入映射到新库，执行两个来源项目的非界面离线测试，以及新增便捷 API 测试。测试脚本需要来源测试目录，库运行不需要。
未连接真实硬件验证；界面布局测试不属于库的测试范围。
