"""Experiment configuration dataclass."""
from __future__ import annotations

from dataclasses import dataclass, field

from .channels import Channel, available_channels


@dataclass
class Config:
    power_port: str = 'COM6'
    heater_enabled: bool = False
    heater_port: str = 'COM7'
    heater_baud: int = 9600
    heater_control: str = 'power'
    heater_voltage: float = 0.
    heater_power: float = .2
    heater_max_voltage: float = 5.
    heater_max_current: float = 1.
    heater_max_power: float = 20.
    meter_port: str = 'COM1'
    power_baud: int = 9600
    meter_baud: int = 9600
    meter_flow: bool = False
    timeout: float = 3.
    mode: str = 'AC'
    frequency: float = 50.
    max_voltage: float = 5.
    max_current: float = 1.
    max_power: float = 20.
    profile_file: str = 'drive_time.ini'
    power_slew_v_s: float = .5
    power_tolerance_rel: float = .05
    power_timeout: float = 60.
    interval: float = 1.
    nplc: float = 1.
    settle: float = .5
    reference_channel: int = 102
    heater_temperature_enabled: bool = False
    heater_trigger_temperature: float = 0.
    heater_trigger_action: str = 'on'
    slot1_module: int = 7702
    slot1_wires: int = 2
    slot2_module: int = 7702
    slot2_wires: int = 2
    channels: list[Channel] = field(default_factory=lambda: [Channel(101), Channel(102)])

    def validate(self):
        from . import config_validate
        return config_validate.validate(self)

    def measurement_for(self, channel):
        slot = channel//100
        if slot not in (1, 2):
            raise ValueError(f'通道 {channel} 的槽位无效，仅支持1xx和2xx')
        return getattr(self, f'slot{slot}_module'), getattr(self, f'slot{slot}_wires')

    def available_channels(self):
        return (available_channels(self.slot1_module, self.slot1_wires, 1)
                + available_channels(self.slot2_module, self.slot2_wires, 2))
