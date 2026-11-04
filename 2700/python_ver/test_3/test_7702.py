import csv
import tempfile
import threading
import unittest
from pathlib import Path
from acquisition import Settings, Instrument, collect, convert_csv, valid_channels
from test_7700 import ModuleSerial


class Tests7702(unittest.TestCase):
    def test_channel_rules(self):
        self.assertEqual(len(valid_channels('直流电压 DCV','7702')),80)
        self.assertEqual(valid_channels('直流电流 DCI','7702'),(141,142,241,242))
        self.assertEqual(len(valid_channels('四线电阻 Ω4','7702')),40)
        for mode, channels in [('四线电阻 Ω4',(121,)),('直流电压 DCV',(141,)),
                               ('直流电流 DCI',(121,)),('直流电压 DCV',(143,))]:
            with self.assertRaises(ValueError):
                Settings(input_source='7702',mode=mode,channels=channels).validate()
        Settings(input_source='7702',channels=(140,240)).validate()
        Settings(input_source='7702',mode='四线电阻 Ω4',channels=(120,220)).validate()

    def test_measure_all_functions_and_high_channels(self):
        for mode in ('直流电压 DCV','交流电压 ACV','直流电流 DCI','交流电流 ACI','两线电阻 Ω2','四线电阻 Ω4'):
            channels=(141,242) if '电流' in mode else (120,220) if '四线' in mode else (140,240)
            fake=ModuleSerial('7702,7702')
            device=Instrument(Settings(input_source='7702',mode=mode,channels=channels,settle=0),threading.Event(),lambda **_:fake)
            device.open();device.configure()
            self.assertEqual(device.module_options,('7702','7702'))
            self.assertIn('FORM:ELEM READ,CHAN',fake.commands)
            for ch in channels:
                self.assertEqual(device.measure(ch)[1:],(0.001234,'OK'))
                self.assertIn(f'ROUT:CLOS (@{ch})',fake.commands)
            device.close()
            self.assertEqual(fake.commands[-2: ],['ROUT:OPEN:ALL','SYST:LOC'])

    def test_model_and_slot_mismatch(self):
        for options, phrase in [('NONE,7702','实际位于槽位 2'),('7700,NONE','所选型号 7702'),('NONE,NONE','槽位1=NONE')]:
            fake=ModuleSerial(options)
            device=Instrument(Settings(input_source='7702',channels=(101,)),threading.Event(),lambda **_:fake)
            with self.assertRaisesRegex(RuntimeError,phrase):
                device.open()
            device.close()
            self.assertNotIn('*RST',fake.commands)

    def test_slot_two_collection_and_export(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'7702.txt'; events=[]; stop=threading.Event()
            fake=ModuleSerial('NONE,7702')
            def emit(kind,data):
                events.append((kind,data))
                if kind=='sample' and data[0]==2:
                    stop.set()
            collect(Settings(input_source='7702',channels=(221,240),settle=0),path,stop,emit,serial_factory=lambda **_:fake)
            self.assertFalse([data for kind,data in events if kind=='error'])
            self.assertEqual([data[1]['channel'] for kind,data in events if kind=='sample'],['221','240'])
            self.assertTrue(any(kind=='modules' and '槽位2=7702' in data for kind,data in events))
            self.assertEqual(convert_csv(path,Path(folder)/'7702.csv'),2)
            with path.open(encoding='utf-8',newline='') as f:
                self.assertEqual([row['channel'] for row in csv.DictReader(f,delimiter='\t')],['221','240'])
