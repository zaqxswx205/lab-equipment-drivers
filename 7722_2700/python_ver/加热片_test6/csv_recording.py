"""按轮次横向记录各通道、交流/加热电参数；保留旧逐通道写入API兼容性。"""
import csv
from datetime import datetime
from pathlib import Path


HEADERS = ['time', 'channel', 'wires', 'resistance_ohm', 'temperature_K',
           'strategy', 'stage', 'drive_mode', 'drive_target', 'setting_voltage_V',
           'output_on', 'voltage_V', 'current_A', 'power_W', 'power_factor']


class CsvRecording:
    def __init__(self, folder, simulation=False, channels=None):
        self.folder = Path(folder)
        self.prefix = 'SIM_' if simulation else ''
        self.file = self.writer = self.day = None
        self.channels = None if channels is None else sorted(c.channel if hasattr(c,'channel') else int(c) for c in channels)
        self.channel_names = {} if channels is None else {c.channel: getattr(c,'name','') for c in channels if hasattr(c,'channel')}
        self.headers = HEADERS if self.channels is None else (
            ['时间']+[label for c in self.channels for label in (f'通道{c}名称', f'通道{c}温度(K)')]+
            ['交流电压(V)','交流电流(A)','交流功率(W)','加热电压(V)','加热电流(A)','加热功率(W)']+
            [f'通道{c}电阻(Ω)' for c in self.channels]+[f'通道{c}接线(线)' for c in self.channels]+
            ['交流控制方式','交流目标值(V或W)','交流设定电压(V)','交流输出状态','交流功率因数','加热输出状态','驱动方案阶段'])

    def start(self, when):
        self.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        name = self.prefix+when.strftime('%Y%m%d_%H%M%S')
        suffix = 1
        while True:
            path = self.folder/(name+(f'_{suffix:02d}' if suffix > 1 else '')+'.csv')
            try:
                self.file = path.open('x', encoding='utf-8-sig', newline='')
                break
            except FileExistsError:
                suffix += 1
        self.writer = csv.writer(self.file)
        self.writer.writerow(self.headers)
        self.day = when.date()
        self.file.flush()
        return path

    def write(self, when, fields):
        if self.file is None or when.date() != self.day:
            self.start(when)
        self.writer.writerow([when.strftime('%Y-%m-%d %H:%M:%S'), *fields])
        self.file.flush()

    def close(self):
        if self.file:
            self.file.close()
        self.file = self.writer = None

    def write_snapshot(self, when, samples, drive=None, heater=None, stage=None,
                       mode=None, target=None, setting=None, enabled=None, heater_enabled=None, cfg=None):
        """One completed scan per row; electrical readings are the latest valid readbacks."""
        if self.channels is None:
            raise ValueError('宽表记录必须指定通道列表')
        readings={c.channel:(r,t) for c,r,t,stamp,at in samples}
        names = {c.channel:c.name for c in cfg.channels} if cfg else self.channel_names
        fields=[value for c in self.channels for value in (names.get(c,''),
            readings[c][1]+273.15 if c in readings and readings[c][1] is not None else '')]
        for measurements in (drive,heater):
            fields += [measurements.get(key,'') if measurements else '' for key in ('voltage','current','power')]
        fields += [readings[c][0] if c in readings else '' for c in self.channels]
        fields += [cfg.measurement_for(c)[1] if c in readings and cfg else '' for c in self.channels]
        fields += [mode if drive and mode else '',target if drive and target is not None else '',
                   setting if drive and setting is not None else '',enabled if drive else '',
                   drive.get('power_factor','') if drive else '',heater_enabled if heater else '']
        fields += [stage if stage is not None else '']
        self.write(when,fields)

