"""Record/display asynchronous samples without owning a serial port."""
from datetime import datetime


def publish(owner, samples):
    s = owner
    drive = s.clients.get('drive')
    m = drive.measurement if drive and not drive.failed else None
    heater=s.clients.get('heater')
    hm=heater.measurement if heater and not heater.failed else None
    step = s.current_step
    elapsed = (s.now()-s.epoch)/60
    if samples or m or hm:
        stage=(s.sequencer.index+1 if s.sequencer and s.sequencer.index>=0 and
               s.controls.drive.is_set() and getattr(s.controls,'drive_source','profile')=='profile' else None)
        s.log_file.write_snapshot(max(row[3] for row in samples) if samples else datetime.now(),
            samples,m,hm,stage,step.mode if step else None,step.value if step else None,
            drive.setting if drive else None,s.enabled,s.heater_enabled,s.cfg)
        s.emit('sample', dict(temperatures={c.channel:t for c,r,t,stamp,at in samples},
            resistances={c.channel:r for c,r,t,stamp,at in samples},
            measurement_errors=dict(s.measurement_errors),
            sample_elapsed_s={c.channel:at-s.epoch for c,r,t,stamp,at in samples},
            elapsed_min=elapsed, drive_elapsed_min=(s.now()-s.drive_epoch)/60 if s.drive_epoch else None,
            setting=drive.setting if m else None,
            stage=s.sequencer.index+1 if m and s.sequencer else None,
            drive_mode=step.mode if m and step else None,drive_target=step.value if m and step else None,
            output_on=s.enabled if m else None,strategy=s.drive_profile.strategy if m and s.drive_profile else None,
            voltage=m['voltage'] if m else None,current=m['current'] if m else None,
            power=m['power'] if m else None,power_factor=m['power_factor'] if m else None))
        s.cycles += 1
