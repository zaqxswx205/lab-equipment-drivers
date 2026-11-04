"""GS610 RS-232 round-trip DC current sweep with voltage acquisition. Protocol: IM765501-01E, chapters 14/16/18."""

import argparse
import csv
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile

# Nominal range, actual source capacity, resolution, maximum load voltage (18-1).
RANGES = tuple(
    tuple(Decimal(value) for value in row)
    for row in [
        ('0.00002', '0.0000205', '0.0000000001', '110'),
        ('0.0002', '0.000205', '0.000000001', '110'),
        ('0.002', '0.00205', '0.00000001', '110'),
        ('0.02', '0.0205', '0.0000001', '110'),
        ('0.2', '0.205', '0.000001', '110'),
        ('0.5', '0.5', '0.00001', '110'),
        ('1', '1', '0.00001', '60'),
        ('2', '2', '0.00001', '30'),
        ('3', '3.2', '0.00001', '12'),
    ]
)


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f'无效数字: {value}') from exc
    if not result.is_finite():
        raise ValueError('参数必须是有限数字')
    return result


def current_points(start, stop, step):
    """Positive step magnitude; direction follows endpoints; include exact stop."""
    start, stop, step = map(number, (start, stop, step))
    if step <= 0:
        raise ValueError('步进电流必须大于 0，升降方向由起始和截止电流决定')
    if max(abs(start), abs(stop)) > Decimal('3.2'):
        raise ValueError('电流必须在 -3.2 A 到 +3.2 A 之间')
    distance = abs(stop - start)
    count = int(distance // step)
    if count + 1 + bool(distance % step) > 100000:
        raise ValueError('扫描点数超过 100000，请增大步进电流')
    direction = Decimal(1) if stop >= start else Decimal(-1)
    points = [start + direction * step * i for i in range(count + 1)]
    if points[-1] != stop:
        points.append(stop)
    return points


def plan_sweep(start, stop, step, voltage_limit, round_trip=True):
    """Include both endpoints on each leg, measuring the turning point twice."""
    points = current_points(start, stop, step)
    if round_trip:
        return_points = current_points(stop, start, step)
        if len(points) + len(return_points) > 100000:
            raise ValueError('往返扫描总点数超过 100000，请增大步进电流')
        points.extend(return_points)
    peak = max(map(abs, points))
    source_range, _, resolution, max_voltage = next(r for r in RANGES if peak <= r[1])
    if any(p % resolution for p in points):
        raise ValueError(f'{source_range} A 量程分辨率为 {resolution} A，扫描点必须为其整数倍')
    limit = number(voltage_limit)
    if not Decimal('0.001') <= limit <= max_voltage:
        raise ValueError(f'此量程限压值必须在 0.001 V 到 {max_voltage} V 之间')
    return points, source_range, limit


class SweepCancelled(RuntimeError):
    """Operator requested cancellation; normal output cleanup still runs."""


def validate_port(value):
    value = value.strip()
    if os.name == 'nt':
        if not re.fullmatch(r'COM[1-9][0-9]*', value, re.IGNORECASE):
            raise ValueError('串口名必须是 COM3、COM4 等，不能填写 Python 或 Anaconda 的文件路径')
        return value.upper()
    if not value:
        raise ValueError('请选择串口')
    return value


class SweepCSV:
    """Append outbound readings; atomically update adjacent return columns."""

    fields = ['point', 'elapsed_s', 'set_current_A', 'measured_voltage_V']

    def __init__(self, path, return_start, has_return):
        self.path = Path(path)
        self.return_start = return_start
        self.has_return = has_return
        self.header = self.fields + (['return_' + name for name in self.fields] if has_return else [])
        # Reserve the new path before any instrument commands are sent.
        with self.path.open('x', newline='', encoding='utf-8-sig') as stream:
            csv.writer(stream).writerow(self.header)

    def save(self, records):
        if len(records) <= self.return_start:
            with self.path.open('a', newline='', encoding='utf-8-sig') as stream:
                csv.writer(stream).writerow(list(records[-1]) + ([''] * 4 if self.has_return else []))
            return
        # CSV cannot update variable-length cells in place. Replace a complete
        # snapshot so an interrupted write leaves the last saved readings intact.
        temporary = None
        try:
            with NamedTemporaryFile(mode='w', newline='', encoding='utf-8-sig',
                                    dir=self.path.parent, prefix=self.path.name + '.',
                                    suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                writer = csv.writer(stream)
                writer.writerow(self.header)
                for index, outbound in enumerate(records[:self.return_start]):
                    return_index = self.return_start + index
                    returning = records[return_index] if return_index < len(records) else ('',) * 4
                    writer.writerow((*outbound, *returning))
            os.replace(temporary, self.path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()


class SerialInstrument:
    """ASCII SCPI over a pySerial port; never accept a partial response."""

    def __init__(self, port, terminator='LF'):
        self.port = port
        self.terminator = {'CR': b'\r', 'LF': b'\n', 'CRLF': b'\r\n'}[terminator]
        self.synchronized = True

    def write(self, command):
        if '\r' in command or '\n' in command:
            raise ValueError('单条命令不能包含换行符')
        data = command.encode('ascii') + self.terminator
        if self.port.write(data) != len(data):
            self.synchronized = False
            raise TimeoutError('串口发送不完整，请重新连接并检查流控设置')

    def query(self, command):
        if not self.synchronized:
            raise RuntimeError('串口响应已失步，请重新连接；仍可发送关闭输出命令')
        try:
            self.write(command)
            response = self.port.read_until(self.terminator, size=4096)
            if not response.endswith(self.terminator):
                raise TimeoutError('串口响应超时或不完整，请检查接线、波特率及 Term 设置')
            text = response[:-len(self.terminator)].decode('ascii').strip()
            if not text:
                raise RuntimeError('设备返回空响应，请检查串口设置')
            return text
        except BaseException:
            # A late response must not be mistaken for a subsequent query result.
            self.synchronized = False
            raise


class GS610:
    def __init__(self, instrument):
        self.instrument = instrument

    def write(self, command):
        self.instrument.write(command)

    def query(self, command):
        return self.instrument.query(command).strip()

    def check_error(self):
        response = self.query(':SYST:ERR?')
        try:
            code = int(response.split(',', 1)[0])
        except ValueError as exc:
            raise RuntimeError(f'设备错误响应无法解析: {response}') from exc
        if code:
            raise RuntimeError(f'GS610 错误: {response}')

    def configure(self, source_range, voltage_limit):
        self.write(':SYST:REM')
        self.write(':OUTP OFF')
        self.check_error()
        for command in (
            ':SOUR:FUNC CURR', ':SOUR:SHAP DC', ':SOUR:MODE FIX',
            ':TRIG:SOUR IMM', ':SOUR:DEL MIN',
            f':SOUR:CURR:RANG {source_range}',
            ':SOUR:VOLT:PROT:STAT ON', ':SOUR:VOLT:PROT:LINK ON',
            f':SOUR:VOLT:PROT:ULIM {voltage_limit}',
            ':SENS:ACH OFF', ':SENS:FUNC VOLT', ':SENS:RSEN ON', ':SENS:STAT ON',
            ':SENS:RANG:AUTO ON', ':SENS:ITIM PLC', ':SENS:DEL MIN',
            ':SENS:AVER:STAT OFF', ':CALC:MATH:STAT OFF', ':CALC:NULL:STAT OFF',
        ):
            self.write(command)
            self.check_error()
        if self.query(':SENS:RSEN?') != '1':
            raise RuntimeError('设备未切换为 4W 四线制（SENSE 感测），停止扫描')
        # Tracking couples lower limit to negative upper limit (manual 5.3).
        upper = number(self.query(':SOUR:VOLT:PROT:ULIM?'))
        lower = number(self.query(':SOUR:VOLT:PROT:LLIM?'))
        if self.query(':SOUR:VOLT:PROT:STAT?') != '1':
            raise RuntimeError('设备限压功能未开启')
        if upper <= 0 or lower >= 0 or max(upper, abs(lower)) > voltage_limit:
            raise RuntimeError(f'设备实际限压不符合请求: 上限 {upper} V，下限 {lower} V')
        print(f'实际限压: {lower} V ~ {upper} V')
        if self.query(':SENS:FUNC?').upper() not in ('VOLT', 'VOLTAGE'):
            raise RuntimeError('设备未切换为电压测量，停止扫描')

    def set_current(self, current):
        self.write(f':SOUR:CURR:LEV {current}')
        self.check_error()
        actual = number(self.query(':SOUR:CURR:LEV?'))
        if actual != current:
            raise RuntimeError(f'电流设定被设备调整: 请求 {current} A，读回 {actual} A')

    def measure_voltage(self):
        response = self.query(':READ?')
        self.check_error()
        status = int(self.query(':STAT:SENS:COND?'))
        if status & (1 << 5):
            raise RuntimeError('反馈电压测量超量程')
        try:
            return number(response)
        except ValueError as exc:
            raise RuntimeError(f'无效的反馈电压: {response}') from exc

    def sweep(self, points, source_range, voltage_limit, dwell=1.0, keep_output=False,
              csv_path=None, on_record=None, stop_event=None):
        if not math.isfinite(dwell) or dwell < 0:
            raise ValueError('每步停留时间必须为有限的非负数')
        # The repeated turning point is the first independent return reading.
        # Also handle a round trip whose start and stop are the same current.
        return_start = next((i for i in range(1, len(points))
                             if points[i] == points[i - 1]
                             or (points[i] - points[i - 1]) * (points[1] - points[0]) < 0), len(points))
        output = SweepCSV(csv_path, return_start, return_start < len(points)) if csv_path else None
        return self._sweep(points, source_range, voltage_limit, dwell, keep_output, output, on_record, stop_event)

    def _sweep(self, points, source_range, voltage_limit, dwell, keep_output, output,
               on_record=None, stop_event=None):
        completed = False
        records = []
        try:
            if stop_event is not None and stop_event.is_set():
                raise SweepCancelled('扫描已停止')
            self.configure(source_range, voltage_limit)
            started = time.monotonic()
            for index, current in enumerate(points):
                if stop_event is not None and stop_event.is_set():
                    raise SweepCancelled('扫描已停止')
                self.set_current(current)
                if index == 0:
                    self.write(':OUTP ON')
                    self.check_error()
                    if self.query(':OUTP?') != '1':
                        raise RuntimeError('设备输出未能开启，请检查联锁及输出控制设置')
                if stop_event is None:
                    time.sleep(dwell)
                elif stop_event.wait(dwell):
                    raise SweepCancelled('扫描已停止')
                measured = self.measure_voltage()
                elapsed = time.monotonic() - started
                record = (index + 1, elapsed, current, measured)
                records.append(record)
                if output:
                    output.save(records)
                if on_record is not None:
                    on_record(record)
                print(f'[{index + 1}/{len(points)}] 设定: {current} A | '
                      f'电压: {measured} V', flush=True)
            if stop_event is not None and stop_event.is_set():
                raise SweepCancelled('扫描已停止')
            completed = True
            return records
        finally:
            active_exception = sys.exc_info()[0] is not None
            if not (completed and keep_output):
                try:
                    self.write(':OUTP OFF')
                    self.check_error()
                    if self.query(':OUTP?') != '0':
                        raise RuntimeError('输出状态仍未关闭')
                    print('输出已关闭')
                except Exception as exc:
                    # Do not replace an active sweep exception with a cleanup error.
                    print(f'关闭输出失败，请在设备面板确认并关闭输出: {exc}', file=sys.stderr)
                    if not active_exception:
                        raise


def application_directory():
    """Save data beside the Python source."""
    return Path(__file__).resolve().parent


def default_result_path():
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S-%f')
    return str(application_directory() / 'result' / f'result-{timestamp}.csv')


def result_path(value):
    path = Path(value)
    return path if path.is_absolute() else application_directory() / path


def prompt_value(label, default=None, convert=str, choices=None, validate=None):
    """Prompt again on invalid input; a blank entry accepts a displayed default."""
    while True:
        suffix = f' [{default}]' if default is not None else ''
        raw = input(f'{label}{suffix}: ').strip()
        if not raw and default is not None:
            raw = str(default)
        try:
            if not raw:
                raise ValueError('此项不能为空')
            value = convert(raw)
            if choices is not None and value not in choices:
                raise ValueError('请选择: ' + ', '.join(map(str, choices)))
            if validate is not None:
                validate(value)
            return value
        except (ValueError, ArithmeticError) as exc:
            print(f'输入有误: {exc}，请重新输入。')


def prompt_seconds(value, positive=False):
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ValueError('请输入有限的正数' if positive else '请输入有限的非负数')


def interactive_settings(args):
    print('GS610 串口电流扫描设置（按回车采用方括号中的默认值，Ctrl+C 退出）')
    try:
        from serial.tools import list_ports
    except ImportError:
        print('尚未安装 pyserial；连接前请执行 python -m pip install -r requirements.txt')
    else:
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        for port in ports:
            print(f'可用串口 {port.device}: {port.description}')
        if not ports:
            print('未发现串口，请检查适配器及其驱动。')
    args.port = prompt_value('串口名（例如 COM3）', args.port, validate_port)
    args.baudrate = prompt_value('波特率', args.baudrate, int,
                                [9600, 14400, 19200, 38400, 57600, 115200])
    args.bytesize = prompt_value('数据位（7/8）', args.bytesize, int, [7, 8])
    args.parity = prompt_value('校验位（N=无，E=偶，O=奇）', args.parity, str.upper, ['N', 'E', 'O'])
    args.stopbits = prompt_value('停止位（1/2）', args.stopbits, int, [1, 2])
    args.flow = prompt_value('流控（none=无，xon=软件，rtscts=硬件）', args.flow,
                             str.lower, ['none', 'xon', 'rtscts'])
    args.terminator = prompt_value('结束符（CR/LF/CRLF）', args.terminator,
                                   str.upper, ['CR', 'LF', 'CRLF'])
    args.timeout = prompt_value('串口读写超时（秒）', args.timeout, float,
                                validate=lambda value: prompt_seconds(value, positive=True))
    args.round_trip = prompt_value('是否双程扫描（初始 → 截止 → 初始）？（y/n）',
                                   'n' if args.round_trip is False else 'y',
                                   str.lower, ['y', 'n']) == 'y'
    while True:
        args.start = prompt_value('初始电流（A）', args.start, number)
        args.stop = prompt_value('截止电流（A）', args.stop, number)
        args.step = prompt_value('步进电流（A，正数；自动判断升降方向）', args.step, number)
        args.voltage_limit = prompt_value('限压（V，3 A 量程最大 12 V）', args.voltage_limit, number)
        try:
            plan_sweep(args.start, args.stop, args.step, args.voltage_limit, args.round_trip)
            break
        except (ValueError, ArithmeticError) as exc:
            print(f'扫描参数不匹配: {exc}。请重新设置电流和限压。')
    args.dwell = prompt_value('每步采样前等待（秒）', args.dwell, float, validate=prompt_seconds)
    final_level = '初始' if args.round_trip else '截止'
    args.keep_output = prompt_value(f'扫描成功结束后保持{final_level}电流输出？（y/n）',
                                    'y' if args.keep_output else 'n', str.lower, ['y', 'n']) == 'y'
    while True:
        label = f'CSV 保存路径（回车采用 {args.csv}）'
        args.csv = input(label + ': ').strip() or args.csv
        path = result_path(args.csv)
        if path.exists():
            print('文件已存在，请换一个文件名，避免覆盖数据。')
        elif not path.parent.is_dir():
            print('保存目录不存在，请输入已有目录下的文件路径。')
        else:
            args.csv = str(path)
            break
        args.csv = default_result_path()
    print(f'串口: {args.port}，{args.baudrate} 波特，{args.bytesize}{args.parity}{args.stopbits}，'
          f'流控 {args.flow}，结束符 {args.terminator}')
    print(f'每点等待 {args.dwell} 秒后采集反馈电压；'
          f'结束后{"保持输出" if args.keep_output else "关闭输出"}；'
          f'CSV: {args.csv or "不保存"}')


def main():
    parser = argparse.ArgumentParser(description='横河 GS610 电流往返步进扫描：初始 → 截止 → 初始（单位 V/A/s）')
    parser.add_argument('--port', help='串口名，例如 COM3；使用 --list 查看')
    parser.add_argument('--list', action='store_true', help='列出电脑串口，不发送设备命令')
    parser.add_argument('--identify', action='store_true', help='仅查询设备型号，不启动扫描')
    parser.add_argument('--baudrate', type=int, default=9600,
                        choices=[9600, 14400, 19200, 38400, 57600, 115200])
    parser.add_argument('--bytesize', type=int, choices=[7, 8], default=8)
    parser.add_argument('--parity', choices=['N', 'E', 'O'], default='N')
    parser.add_argument('--stopbits', type=int, choices=[1, 2], default=1)
    parser.add_argument('--flow', choices=['none', 'xon', 'rtscts'], default='none')
    parser.add_argument('--terminator', choices=['CR', 'LF', 'CRLF'], default='LF')
    parser.add_argument('--timeout', type=float, default=5, help='串口读写超时秒数，默认 5')
    parser.add_argument('--start', default='-3.2', help='初始电流 A，默认 -3.2')
    parser.add_argument('--stop', default='3.2', help='截止电流 A，默认 3.2')
    parser.add_argument('--step', default='0.08', help='步进电流 A，正数，默认 0.08（80 mA）')
    parser.add_argument('--voltage-limit', default='12', help='对称限压 V，默认 12')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--round-trip', dest='round_trip', action='store_true', help='双程扫描：初始 → 截止 → 初始')
    mode.add_argument('--single-pass', dest='round_trip', action='store_false', help='单程扫描：初始 → 截止')
    parser.set_defaults(round_trip=None)
    parser.add_argument('--dwell', type=float, default=1.0, help='每点停留秒数，默认 1')
    parser.add_argument('--csv', metavar='PATH', default=default_result_path(),
                        help='CSV 路径，默认脚本目录的 result 文件夹下 result-测试日期时间.csv')
    parser.add_argument('--keep-output', action='store_true', help='成功后保持最终电流输出（双程为初始，单程为截止）')
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不连接设备')
    parser.add_argument('--interactive', action='store_true', help='逐项交互设置参数（不带参数运行时默认启用）')
    args = parser.parse_args()
    if not (args.list or args.identify or args.dry_run):
        (application_directory() / 'result').mkdir(exist_ok=True)
    if args.interactive or len(sys.argv) == 1:
        if args.list or args.identify:
            parser.error('交互式扫描不能与 --list 或 --identify 一起使用')
        interactive_settings(args)
    args.csv = str(result_path(args.csv))
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout 必须是有限的正数')
    if args.dry_run and (args.list or args.identify):
        parser.error('--dry-run 不能与 --list 或 --identify 一起使用')
    if not (args.list or args.identify):
        if args.round_trip is None:
            args.round_trip = prompt_value('是否双程扫描（初始 → 截止 → 初始）？（y/n）',
                                           'y', str.lower, ['y', 'n']) == 'y'
        for key, label in [('start', '初始电流 (A)'), ('stop', '截止电流 (A)'), ('step', '步进电流 (A，正数)')]:
            if getattr(args, key) is None:
                setattr(args, key, input(label + ': ').strip())
        points, source_range, limit = plan_sweep(args.start, args.stop, args.step, args.voltage_limit, args.round_trip)
        if not math.isfinite(args.dwell) or args.dwell < 0:
            parser.error('--dwell 必须是有限的非负数')
        route = f'{points[0]} A -> {number(args.stop)} A'
        if args.round_trip:
            route += f' -> {points[-1]} A'
        print(f'{route}，{"双程" if args.round_trip else "单程"}共 {len(points)} 点，'
              f'固定量程 {source_range} A，限压 ±{limit} V')
        if args.dry_run:
            for point in points:
                print(f'{point} A')
            return
    if not args.list and not args.port:
        parser.error('请用 --port COM3 指定串口；可先运行 python gs610_sweep.py --list')
    try:
        import serial
        from serial.tools import list_ports
    except ImportError as exc:
        raise RuntimeError('请先运行 python -m pip install -r requirements.txt 安装 pyserial') from exc
    if args.list:
        ports = sorted(list_ports.comports(), key=lambda p: p.device)
        for port in ports:
            print(f'{port.device}: {port.description}')
        if not ports:
            print('未找到串口，请检查 RS-232 适配器及其驱动')
        return
    args.port = validate_port(args.port)
    with serial.Serial(
        port=args.port, baudrate=args.baudrate, bytesize=args.bytesize,
        parity=args.parity, stopbits=args.stopbits, timeout=args.timeout,
        write_timeout=args.timeout, xonxoff=args.flow == 'xon',
        rtscts=args.flow == 'rtscts', dsrdtr=False,
    ) as port:
        port.reset_input_buffer()
        device = GS610(SerialInstrument(port, args.terminator))
        identity = device.query('*IDN?')
        print(f'设备: {identity}')
        fields = [field.strip().upper() for field in identity.split(',')]
        if len(fields) < 2 or fields[1] not in ('765501', 'GS610'):
            raise RuntimeError('设备型号不是 GS610 (765501)，停止运行')
        if not args.identify:
            device.sweep(points, source_range, limit, args.dwell, args.keep_output, args.csv)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n扫描已中断', file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f'运行失败: {exc}', file=sys.stderr)
        sys.exit(1)
