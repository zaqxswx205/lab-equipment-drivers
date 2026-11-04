"""每次采集及跨日建立独立CSV，保留2700和7722数据在同一表中。"""
import csv
from datetime import datetime
from pathlib import Path


HEADERS = ['time', 'channel', 'wires', 'resistance_ohm', 'temperature_K',
           'strategy', 'stage', 'drive_mode', 'drive_target', 'setting_voltage_V',
           'output_on', 'voltage_V', 'current_A', 'power_W', 'power_factor']


class CsvRecording:
    def __init__(self, folder, simulation=False):
        self.folder = Path(folder)
        self.prefix = 'SIM_' if simulation else ''
        self.file = self.writer = self.day = None

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
        self.writer.writerow(HEADERS)
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

