"""GS610 step current source + Keithley 2700 front DC voltage acquisition."""
import argparse
import csv
import math
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from gs610_driver import GS610, SerialInstrument, plan_sweep, prompt_value, number, validate_port
from keithley2700 import Instrument, Settings


def main():
    if len(sys.argv) == 1:
        print('2700 前面板电压采集 + 610 电流输出（电流 A、电压 V、时间 s）')
        print('2700 开关置 F；按 Ctrl+C 退出。')
        port2700 = prompt_value('2700 串口（例如 COM3）', validate=validate_port)
        port610 = prompt_value('610 串口（例如 COM4）', validate=validate_port)
        while port610.upper() == port2700.upper():
            print('两台仪器必须使用不同串口')
            port610 = prompt_value('610 串口', validate=validate_port)
        while True:
            start = prompt_value('起始电流 A', convert=number)
            stop = prompt_value('截止电流 A（恒流请输入与起始相同的值）', convert=number)
            step = prompt_value('步进电流 A', '0.001', number)
            limit = prompt_value('610 限压 V', convert=number)
            try:
                plan_sweep(start, stop, step, limit, False)
                break
            except ValueError as exc:
                print(f'参数有误：{exc}，请重新设置。')
        samples = prompt_value('每个电流点采集次数', 1, int,
                               validate=lambda v: None if v >= 1 else (_ for _ in ()).throw(ValueError('至少为1')))
        sys.argv.extend(['--port2700', port2700, '--port610', port610,
                         '--start', str(start), '--stop', str(stop), '--step', str(step),
                         '--voltage-limit', str(limit), '--samples', str(samples)])
    p = argparse.ArgumentParser(description='610 输出电流，2700 前面板采集直流电压；单位 A/V/s')
    p.add_argument('--port2700', required=True)
    p.add_argument('--port610', required=True)
    p.add_argument('--baud2700', type=int, default=9600)
    p.add_argument('--baud610', type=int, default=9600)
    p.add_argument('--term610', choices=['LF', 'CR', 'CRLF'], default='LF')
    p.add_argument('--flow2700', action='store_true', help='2700 软件流控')
    p.add_argument('--flow610', choices=['none', 'xon', 'rtscts'], default='none')
    p.add_argument('--start', required=True, help='起始电流 A；恒流时与 stop 相同')
    p.add_argument('--stop', required=True, help='截止电流 A')
    p.add_argument('--step', default='0.001', help='正步长 A')
    p.add_argument('--voltage-limit', required=True, help='610 对称限压 V')
    p.add_argument('--round-trip', action='store_true')
    p.add_argument('--samples', type=int, default=1, help='每个电流点采集次数')
    p.add_argument('--interval', type=float, default=1, help='同一电流点两次读数之间等待秒数')
    p.add_argument('--settle', type=float, default=1, help='设置电流后稳定秒数')
    p.add_argument('--nplc', type=float, default=1)
    p.add_argument('--range2700', default='AUTO', help='AUTO 或电压量程 V')
    p.add_argument('--csv', type=Path)
    p.add_argument('--dry-run', action='store_true', help='只验证参数、显示电流点，不连接仪器')
    a = p.parse_args()
    if a.port2700.strip().upper() == a.port610.strip().upper():
        p.error('两台仪器必须使用不同串口')
    if a.samples < 1 or any(not math.isfinite(v) or v < 0 for v in (a.interval, a.settle)):
        p.error('samples 至少为 1；interval/settle 必须为有限非负数')
    settings = Settings(port=a.port2700, baud=a.baud2700, flow=a.flow2700,
                        input_source='FRONT', nplc=a.nplc, measure_range=a.range2700)
    settings.validate()
    points, source_range, limit = plan_sweep(a.start, a.stop, a.step, a.voltage_limit, a.round_trip)
    print(f'{len(points)} 个电流点，量程 {source_range} A，限压 ±{limit} V')
    if a.dry_run:
        print(', '.join(map(str, points)))
        return
    import serial
    path = a.csv or Path(__file__).parent / 'result' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.csv')
    path.parent.mkdir(parents=True, exist_ok=True)
    meter = Instrument(settings, threading.Event())
    source = None
    port = None
    try:
        with path.open('x', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['timestamp', 'point', 'sample', 'elapsed_s', 'set_current_A', 'voltage_V', 'status', 'raw'])
            print('2700:', meter.open())
            meter.configure()
            port = serial.Serial(a.port610, a.baud610, timeout=5, write_timeout=5,
                                 xonxoff=a.flow610 == 'xon', rtscts=a.flow610 == 'rtscts')
            port.reset_input_buffer()
            source = GS610(SerialInstrument(port, a.term610))
            identity = source.query('*IDN?')
            if len(identity.split(',')) < 2 or identity.split(',')[1].strip().upper() not in ('765501', 'GS610'):
                raise RuntimeError(f'设备不是 GS610: {identity}')
            print('610:', identity)
            source.configure(source_range, limit)
            # Original driver configures remote sensing. Use local sensing for
            # the current source; voltage is independently measured by 2700.
            source.write(':SENS:RSEN OFF')
            source.check_error()
            if source.query(':SENS:RSEN?') != '0':
                raise RuntimeError('610 未切换到本地感测')
            started = time.monotonic()
            for index, current in enumerate(points, 1):
                source.set_current(current)
                if index == 1:
                    source.write(':OUTP ON')
                    source.check_error()
                    if source.query(':OUTP?') != '1':
                        raise RuntimeError('610 输出未开启')
                time.sleep(a.settle)
                for sample in range(1, a.samples + 1):
                    raw, value, status = meter.measure(0)
                    writer.writerow([datetime.now().isoformat(timespec='milliseconds'), index, sample,
                                     time.monotonic() - started, str(current), value, status, raw])
                    f.flush()
                    os.fsync(f.fileno())
                    print(f'[{index}/{len(points)}] {current} A -> {value} V ({status})', flush=True)
                    if status != 'OK':
                        raise RuntimeError('2700 测量超量程，停止输出')
                    if sample < a.samples:
                        time.sleep(a.interval)
    finally:
        if source is not None:
            try:
                source.write(':OUTP OFF')
                if source.query(':OUTP?') != '0':
                    raise RuntimeError('输出仍开启')
            except Exception as exc:
                print(f'关闭610输出失败，请立即在面板确认输出状态：{exc}', file=sys.stderr)
        if port is not None:
            port.close()
        meter.close()
    print(f'数据已保存：{path.resolve()}')


if __name__ == '__main__':
    if len(sys.argv) == 1:
        from gui import main as gui_main
        gui_main()
        sys.exit(0)
    try:
        main()
    except KeyboardInterrupt:
        print('已中断采集', file=sys.stderr)
        sys.exit(130)
