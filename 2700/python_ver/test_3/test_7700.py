import csv
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from acquisition import (Settings, Instrument, collect, convert_csv, valid_channels,
                         parse_channel_reading, OLD_FIELDS, LEGACY_FIELDS, Cancelled)
from test_acquisition import FakeSerial


class ModuleSerial(FakeSerial):
    def __init__(self, options='7700,7700', wrong_channel=False):
        super().__init__()
        self.front = '0'
        self.options = options
        self.selected = 0
        self.wrong_channel = wrong_channel

    def write(self, data):
        command = data.decode('ascii').strip()
        if command.startswith('ROUT:CLOS (@'):
            self.selected = int(command.split('@')[1].split(')')[0])
        if command in ('*OPT?', 'READ?'):
            self.commands.append(command)
            self.writes.append(data)
            result = self.options if command == '*OPT?' else f'+1.234E-3,{self.selected + int(self.wrong_channel):03}'
            self.response.extend((result + '\r\n').encode())
        else:
            super().write(data)


class ModuleTests(unittest.TestCase):
    def test_valid_channels_and_rejections(self):
        for mode in ('直流电压 DCV', '交流电压 ACV', '两线电阻 Ω2'):
            self.assertEqual(len(valid_channels(mode)), 40)
        self.assertEqual(valid_channels('直流电流 DCI'), (121, 122, 221, 222))
        self.assertEqual(len(valid_channels('四线电阻 Ω4')), 20)
        for changes in [dict(channels=()), dict(channels=(101,101)), dict(channels=(123,)),
                        dict(channels=(111,), mode='四线电阻 Ω4'),
                        dict(channels=(101,), mode='直流电流 DCI'), dict(channels=(301,)),
                        dict(settle=float('nan')), dict(settle=-1)]:
            with self.assertRaises(ValueError):
                Settings(input_source='7700', **changes).validate()

    def test_all_modes_switch_single_and_multiple_channels(self):
        for mode in ('直流电压 DCV','交流电压 ACV','直流电流 DCI','交流电流 ACI','两线电阻 Ω2','四线电阻 Ω4'):
            channels = (121, 222) if '电流' in mode else (101, 210)
            fake = ModuleSerial()
            device = Instrument(Settings(input_source='7700', mode=mode, channels=channels, settle=0),
                                threading.Event(), lambda **_: fake)
            device.open()
            device.configure()
            self.assertIn('ROUT:CLOS:ACON OFF', fake.commands)
            self.assertIn('FORM:ELEM READ,CHAN', fake.commands)
            if '交流' in mode:
                self.assertTrue(any('DET:BAND 300' in cmd for cmd in fake.commands))
            for ch in channels:
                self.assertEqual(device.measure(ch)[1:], (0.001234, 'OK'))
                self.assertIn(f'ROUT:CLOS (@{ch})', fake.commands)
            device.close()
            self.assertEqual(fake.commands[-3:], ['ROUT:SCAN:LSEL NONE','ROUT:OPEN:ALL','SYST:LOC'])
            self.assertTrue(fake.closed)

    def test_missing_module_and_front_switch(self):
        for options, front in [('7700,NONE','0'), ('7702,7700','0'), ('7700,7700','1')]:
            fake = ModuleSerial(options)
            fake.front = front
            device = Instrument(Settings(input_source='7700', channels=(101,201)), threading.Event(), lambda **_: fake)
            with self.assertRaises(RuntimeError):
                device.open()
            device.close()
            self.assertNotIn('*RST', fake.commands)

    def test_slot_diagnostics(self):
        for options, expected in [('NONE,NONE', '主机未识别到所选型号 7700'),
                                  ('NONE,7700', '实际位于槽位 2')]:
            fake = ModuleSerial(options)
            device = Instrument(Settings(input_source='7700', channels=(101,)), threading.Event(), lambda **_: fake)
            with self.assertRaisesRegex(RuntimeError, expected):
                device.open()
            device.close()

    def test_tagged_response(self):
        self.assertEqual(parse_channel_reading('+9.9E37,101',101),(None,'OVERLOAD'))
        for raw in ['1,102','1','1,abc','1,101,extra','bad,101']:
            with self.assertRaises(ValueError):
                parse_channel_reading(raw,101)

    def test_pipeline_saves_selected_channels_and_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            txt, out = Path(folder)/'multi.txt', Path(folder)/'multi.csv'
            stop = threading.Event()
            events = []
            fake = ModuleSerial()
            def emit(kind, data):
                events.append((kind,data))
                if kind == 'sample':
                    self.assertEqual(len(txt.read_text(encoding='utf-8').splitlines()), data[0]+1)
                    if data[0] == 4:
                        stop.set()
            collect(Settings(input_source='7700',channels=(201,101),settle=0,interval=0.05),
                    txt,stop,emit,serial_factory=lambda **_: fake)
            self.assertFalse([data for kind,data in events if kind=='error'])
            self.assertEqual([data[1]['channel'] for kind,data in events if kind=='sample'],['101','201','101','201'])
            self.assertEqual(convert_csv(txt,out),4)
            with out.open(encoding='utf-8-sig',newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(rows[1]['channel'],'201')
            self.assertEqual(rows[0]['value'],'0.001234')

    def test_wrong_channel_never_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            txt=Path(folder)/'bad.txt'; events=[]
            fake=ModuleSerial(wrong_channel=True)
            collect(Settings(input_source='7700',channels=(101,),settle=0),txt,threading.Event(),
                    lambda *e: events.append(e),serial_factory=lambda **_:fake)
            self.assertFalse(any(kind=='sample' for kind,_ in events))
            self.assertTrue(any(kind=='error' and '通道不匹配' in msg for kind,msg in events))
            self.assertEqual(len(txt.read_text(encoding='utf-8').splitlines()),1)
            self.assertTrue(fake.closed)

    def test_channel_switch_error_never_read_or_saved(self):
        class RelayError(ModuleSerial):
            def write(self,data):
                super().write(data)
                if data.startswith(b'ROUT:CLOS (@'):
                    self.errors.append('-222,"Parameter data out of range"')
        fake=RelayError()
        device=Instrument(Settings(input_source='7700',channels=(101,),settle=0),threading.Event(),lambda **_:fake)
        device.open();device.configure()
        with self.assertRaisesRegex(RuntimeError,'-222'):
            device.measure(101)
        self.assertNotIn('READ?',fake.commands)
        device.close()

    def test_cancel_during_settle(self):
        stop=threading.Event(); fake=ModuleSerial()
        device=Instrument(Settings(input_source='7700',settle=30),stop,lambda **_:fake)
        device.open();device.configure()
        timer=threading.Timer(0.02,stop.set);timer.start()
        with self.assertRaises(Cancelled):
            device.measure(101)
        timer.join();device.close()
        self.assertNotIn('READ?',fake.commands)
        self.assertTrue(fake.closed)

    def test_legacy_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            for i,fields in enumerate((OLD_FIELDS,LEGACY_FIELDS)):
                src,dst=Path(folder)/f'{i}.txt',Path(folder)/f'{i}.csv'
                with src.open('w',encoding='utf-8',newline='') as f:
                    writer=csv.DictWriter(f,fields,delimiter='\t');writer.writeheader()
                    writer.writerow(dict.fromkeys(fields,'1'))
                convert_csv(src,dst)
                with dst.open(encoding='utf-8-sig',newline='') as f:
                    self.assertEqual(next(csv.DictReader(f))['channel'],'FRONT')


if __name__ == '__main__':
    unittest.main()
