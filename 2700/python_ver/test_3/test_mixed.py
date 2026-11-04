import csv
import tempfile
import threading
import unittest
from pathlib import Path
from acquisition import ChannelConfig, Settings, Instrument, collect, convert_csv, PREVIOUS_FIELDS, FULL_FIELDS, FIELDS
from test_7700 import ModuleSerial


class MixedSerial(ModuleSerial):
    def __init__(self, configs):
        super().__init__('7702,NONE')
        self.configs = {c.channel:c for c in configs}
        self.open_input = None
        self.function = None
        self.ranges = {}
        self.nplcs = {}
        self.seen = []

    def write(self,data):
        command=data.decode('ascii').strip()
        if command=='ROUT:OPEN:ALL':
            self.open_input=None
        if command.startswith('SENS:FUNC '):
            assert self.open_input is None, 'function changed with an input connected'
            self.function=command.split("'")[1]
        if command.startswith('SENS:') and ':RANG ' in command:
            self.ranges[command.split(':RANG')[0]]=float(command.split()[-1])
        if command.startswith('SENS:') and ':NPLC ' in command:
            self.nplcs[command.split(':NPLC')[0]]=float(command.split()[-1])
        if command.startswith('ROUT:CLOS (@'):
            self.open_input=int(command.split('@')[1].split(')')[0])
        if command=='READ?':
            from acquisition import MODES
            config=self.configs[self.open_input]
            function=MODES[config.mode][0]
            assert self.function==function,(self.open_input,self.function,function)
            assert self.nplcs['SENS:'+function]==config.nplc
            if config.measure_range!='AUTO':
                assert self.ranges['SENS:'+function]==float(config.measure_range)
            self.commands.append(command);self.writes.append(data)
            value=1000 if function=='RES' else 0.001 if function.startswith('CURR') else 2
            self.response.extend(f'{value},{self.open_input}\r\n'.encode())
            self.seen.append((self.open_input,self.function))
        else:
            super().write(data)


class MixedTests(unittest.TestCase):
    def test_mixed_functions_parameter_isolation_and_export(self):
        configs=(ChannelConfig(101,'两线电阻 Ω2','10000',5,0),
                 ChannelConfig(102,'直流电压 DCV','10',1,0),
                 ChannelConfig(103,'交流电压 ACV','100',0.1,0),
                 ChannelConfig(104,'四线电阻 Ω4','1000',1,0),
                 ChannelConfig(141,'直流电流 DCI','0.01',0.1,0),
                 ChannelConfig(142,'交流电流 ACI','1',5,0))
        settings=Settings(input_source='7702',channels=tuple(c.channel for c in configs),channel_configs=configs,interval=0.05)
        fake=MixedSerial(configs);stop=threading.Event();events=[]
        with tempfile.TemporaryDirectory() as folder:
            txt=Path(folder)/'mixed.txt';out=Path(folder)/'mixed.csv'
            def emit(kind,data):
                events.append((kind,data))
                if kind=='sample' and data[0]==12: stop.set()
            collect(settings,txt,stop,emit,serial_factory=lambda **_:fake)
            self.assertFalse([data for kind,data in events if kind=='error'])
            rows=[data[1] for kind,data in events if kind=='sample']
            self.assertEqual(len(rows),12)
            self.assertEqual(rows[0]['mode'],'两线电阻 Ω2')
            self.assertEqual(rows[0]['unit'],'Ohm')
            self.assertEqual(rows[1]['unit'],'V')
            self.assertEqual(rows[0]['value'],'1000')
            self.assertEqual(rows[1]['value'],'2')
            self.assertTrue(all(list(r) == FIELDS for r in rows))
            self.assertEqual(convert_csv(txt,out),12)
            with out.open(encoding='utf-8-sig',newline='') as f:
                self.assertEqual(list(next(csv.DictReader(f))), FIELDS)
            for channel,_ in fake.seen:
                self.assertIn(f'ROUT:CLOS (@{channel})',fake.commands)
            self.assertTrue(fake.closed)

    def test_same_function_different_parameters_and_auto_range(self):
        configs=(ChannelConfig(101,'直流电压 DCV','1',0.1,0),
                 ChannelConfig(102,'直流电压 DCV','10',5,0),
                 ChannelConfig(103,'直流电压 DCV','AUTO',1,0))
        settings=Settings(input_source='7702',channels=(101,102,103),channel_configs=configs)
        device=Instrument(settings,threading.Event(),lambda **_:MixedSerial(configs))
        device.open();device.configure()
        for channel in (101,102,103,101):device.measure(channel)
        self.assertIn('SENS:VOLT:DC:RANG:AUTO ON',device.serial.commands)
        self.assertEqual(device.serial.seen[-1][0],101)
        device.close()

    def test_four_wire_pair_conflicts(self):
        for model, primary, sense in [('7702',101,121),('7702',220,240),('7700',101,111)]:
            configs=(ChannelConfig(primary,'四线电阻 Ω4'),ChannelConfig(sense))
            with self.assertRaisesRegex(ValueError,'感测通道'):
                Settings(input_source=model,channels=(primary,sense),channel_configs=configs).validate()
        configs=(ChannelConfig(101,'四线电阻 Ω4'),ChannelConfig(201,'直流电压 DCV'))
        Settings(input_source='7702',channels=(101,201),channel_configs=configs).validate()

    def test_invalid_or_missing_configuration(self):
        for configs in [(ChannelConfig(101),), (ChannelConfig(101),ChannelConfig(101)),
                        (ChannelConfig(101),ChannelConfig(102,nplc=11)),
                        (ChannelConfig(101),ChannelConfig(102,settle=float('nan'))),
                        (ChannelConfig(101),ChannelConfig(102,measure_range='-1'))]:
            with self.assertRaises(ValueError):
                Settings(input_source='7702',channels=(101,102),channel_configs=configs).validate()
        with self.assertRaises(ValueError):
            Settings(input_source='FRONT',channel_configs=(ChannelConfig(101),)).validate()

    def test_simulated_mixed_units_and_metadata(self):
        configs=(ChannelConfig(101,'两线电阻 Ω2','10000',5,0.2),ChannelConfig(102,'直流电压 DCV','10',0.1,0.05))
        stop=threading.Event();rows=[]
        def emit(kind,data):
            if kind=='sample':
                rows.append(data[1])
                if len(rows)==2:stop.set()
        with tempfile.TemporaryDirectory() as folder:
            collect(Settings(input_source='7702',simulation=True,channel_configs=configs),Path(folder)/'sim.txt',stop,emit)
        self.assertEqual([r['unit'] for r in rows],['Ohm','V'])
        self.assertTrue(all(list(r) == FIELDS for r in rows))

    def test_old_test2_data_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'old.txt';out=Path(folder)/'old.csv'
            with src.open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,PREVIOUS_FIELDS,delimiter='\t');writer.writeheader()
                row=dict.fromkeys(PREVIOUS_FIELDS,'1');row['channel']='102';writer.writerow(row)
            self.assertEqual(convert_csv(src,out),1)
            with out.open(encoding='utf-8-sig',newline='') as f:
                row=next(csv.DictReader(f));self.assertEqual(row['channel'],'102');self.assertEqual(list(row), FIELDS)

    def test_full_old_schema_exports_only_requested_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'old_full.txt';out=Path(folder)/'new.csv'
            with src.open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,FULL_FIELDS,delimiter='\t');writer.writeheader()
                row=dict.fromkeys(FULL_FIELDS,'1');row['channel']='101';writer.writerow(row)
            self.assertEqual(convert_csv(src,out),1)
            with out.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                self.assertEqual(reader.fieldnames,['timestamp','channel','mode','unit','value','status'])
                self.assertEqual(next(reader)['channel'],'101')

    def test_parameter_error_stops_before_connect_or_read(self):
        class Bad(MixedSerial):
            def write(self,data):
                super().write(data)
                if data.startswith(b'SENS:RES:NPLC '):
                    self.errors.append('-221,"Settings conflict"')
        configs=(ChannelConfig(101,'两线电阻 Ω2'),)
        fake=Bad(configs)
        device=Instrument(Settings(input_source='7702',channels=(101,),channel_configs=configs),threading.Event(),lambda **_:fake)
        device.open();device.configure()
        with self.assertRaisesRegex(RuntimeError,'-221'):device.measure(101)
        self.assertNotIn('ROUT:CLOS (@101)',fake.commands)
        self.assertNotIn('READ?',fake.commands)
        device.close()
