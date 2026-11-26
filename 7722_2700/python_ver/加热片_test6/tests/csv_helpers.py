"""Read either CSV layout as individual samples for existing device-behaviour tests.

Wide-layout tests inspect the physical CSV rows directly. This adapter lets
older acquisition/protection tests keep asserting per-channel sample values.
"""
import csv
import re


def legacy_rows(stream):
    for row in csv.DictReader(stream):
        if '时间' not in row:
            yield row
            continue
        channels=[match[1] for key in row if (match:=re.fullmatch(r'通道(\d+)温度\(K\)',key))]
        populated=[c for c in channels if row[f'通道{c}温度(K)']!='' or row[f'通道{c}电阻(Ω)']!='']
        for channel in populated or ['']:
            yield dict(time=row['时间'],channel=channel,
                temperature_K=row.get(f'通道{channel}温度(K)',''),
                resistance_ohm=row.get(f'通道{channel}电阻(Ω)',''),
                wires=row.get(f'通道{channel}接线(线)',''),
                voltage_V=row['交流电压(V)'],current_A=row['交流电流(A)'],power_W=row['交流功率(W)'],
                stage=row['驱动方案阶段'],drive_mode=row['交流控制方式'],
                drive_target=row['交流目标值(V或W)'],setting_voltage_V=row['交流设定电压(V)'],
                output_on=row['交流输出状态'],power_factor=row['交流功率因数'],strategy='')
