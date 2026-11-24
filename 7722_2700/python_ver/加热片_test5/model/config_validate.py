"""Config.validate() rules, kept verbatim from the original monolith."""
from __future__ import annotations

import math
from dataclasses import asdict


def validate(self):
    for k, v in asdict(self).items():
        if isinstance(v, (int, float)) and not math.isfinite(v):
            raise ValueError(f'{k} 必须为有限数值')
    if not self.power_port.strip() or not self.meter_port.strip():
        raise ValueError('两个串口名称不能为空')
    if self.power_port.strip().upper() == self.meter_port.strip().upper():
        raise ValueError('两台设备不能使用同一串口')
    if self.heater_enabled or self.heater_temperature_enabled:
        if not self.heater_port.strip():
            raise ValueError('加热电源串口不能为空')
        if self.heater_baud <= 0:
            raise ValueError('加热电源波特率须为正数')
        for key, maximum in [('heater_max_voltage', 300), ('heater_max_current', 6), ('heater_max_power', 600)]:
            if not 0 < getattr(self, key) <= maximum:
                raise ValueError(f'{key} 必须在 (0, {maximum}] 内')
        if self.heater_control not in ('voltage', 'power'):
            raise ValueError('加热控制方式须为voltage或power')
        if self.heater_control == 'voltage' and not 0 <= self.heater_voltage <= self.heater_max_voltage:
            raise ValueError('加热设定电压须在0至加热最大电压之间')
        if self.heater_control == 'power' and not 0 <= self.heater_power <= self.heater_max_power*.95:
            raise ValueError('加热目标功率须非负且不高于加热功率保护的95%')
    if self.mode != 'AC' or self.frequency <= 0:
        raise ValueError('制冷机驱动仅支持AC模式，频率须为正数')
    for key, maximum in [('max_voltage', 300), ('max_current', 6), ('max_power', 600)]:
        if not 0 < getattr(self, key) <= maximum:
            raise ValueError(f'{key} 必须在 (0, {maximum}] 内')
    if self.power_slew_v_s <= 0 or not 0 < self.power_tolerance_rel <= .2 or self.power_timeout <= 0:
        raise ValueError('功率调节升压速率、超时须为正数，相对容差须为(0,0.2]')
    if not self.profile_file.strip():
        raise ValueError('请选择驱动方案配置文件')
    if not .05 <= self.interval <= 3600 or not .1 <= self.timeout <= 60:
        raise ValueError('采样间隔须为0.05–3600秒，通信超时须为0.1–60秒')
    if not .01 <= self.nplc <= 10 or not 0 <= self.settle <= 60:
        raise ValueError('NPLC须为0.01–10，稳定时间须为0–60秒')
    if self.meter_baud not in (300, 600, 1200, 2400, 4800, 9600, 19200) or self.power_baud <= 0:
        raise ValueError('波特率无效')
    valid_channels = self.available_channels()
    used = set()
    for c in self.channels:
        if (not math.isfinite(c.min_temperature) or not math.isfinite(c.max_temperature)
                or not -200 <= c.min_temperature < c.max_temperature <= 850):
            raise ValueError(f'通道 {c.channel} 保护阈值必须满足 73.15 K ≤ 下限 < 上限 ≤ 1123.15 K')
        module, wires = self.measurement_for(c.channel)
        count = {7700: 20, 7702: 40}[module]
        if c.channel not in valid_channels:
            raise ValueError(f'通道 {c.channel} 不支持所在槽位的 {module} / {wires}线制，或占用了四线感测配对通道')
        if not math.isfinite(c.lead_ohm) or c.lead_ohm < 0 or (wires == 4 and c.lead_ohm != 0):
            raise ValueError('两线引线补偿须非负；四线补偿须为0')
        occupied = {c.channel}
        if wires == 4:
            occupied.add(c.channel + count//2)
        if used & occupied:
            raise ValueError(f'通道 {c.channel} 重复或与四线感测通道冲突')
        used |= occupied
    if self.reference_channel not in [c.channel for c in self.channels]:
        raise ValueError('参考温度通道必须是已添加的采集通道')
    if self.heater_temperature_enabled:
        reference = next(c for c in self.channels if c.channel == self.reference_channel)
        if self.heater_trigger_action not in ('on', 'off'):
            raise ValueError('温度触发动作须为on或off')
        if not reference.min_temperature < self.heater_trigger_temperature < reference.max_temperature:
            raise ValueError('加热触发温度须严格位于参考通道保护范围内')
        if self.heater_port.strip().upper() == self.meter_port.strip().upper():
            raise ValueError('温度触发加热时，加热电源与2700必须使用不同串口')
