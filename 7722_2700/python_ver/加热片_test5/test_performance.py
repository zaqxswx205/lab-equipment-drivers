"""Performance sequencing and full offline lifecycle regression."""
import csv
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import core
from performance import PerformanceRun, parse_plan
from profile.model import Profile, Step
from runtime import RunControls


def plan_text(points):
    return '\n'.join(f'[Point {i}]\ntemperature_k={t}\nheater_w={h}'
                     for i, (t,h,d) in enumerate(points, 1))


class PerformanceTests(unittest.TestCase):
    def cfg(self):
        return core.Config(interval=.05, settle=0, heater_enabled=True, heater_control='power',
                           heater_power=.02, heater_max_power=2, max_power=2,
                           power_timeout=30)

    def test_validation_limits_temperature_and_order(self):
        cfg = self.cfg()
        for points in ([(float('nan'),.02,.02)], [(273.15,3,.02)],
                       [(100,.02,.02)], [(273.15,.02,.02),(280,.02,.02)]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                parse_plan(plan_text(points), cfg)
        self.assertEqual(len(parse_plan(plan_text([(273.15,.02,.02),(273.15,.03,.04)]), cfg)), 2)

    def run_points(self, points, temperatures, advance=True, cancel_heater=False):
        cfg = self.cfg()
        controls = RunControls(acquisition=True, drive=True, profile=Profile('time',[Step(0,'voltage',1, performance_points=tuple(range(1,len(points)+1))), Step(100,'voltage',0)]))
        performance = controls.performance = PerformanceRun(parse_plan(plan_text(points), cfg))
        events = []
        reads = iter(temperatures)
        temperature = temperatures[-1]
        def read(meter, channel, stop):
            nonlocal temperature
            temperature = next(reads, temperature)
            return core.resistance(temperature), temperature
        def emit(kind, value):
            events.append((kind, value))
            if kind == 'heater_sample' and cancel_heater:
                controls.heater.clear()
            if kind == 'performance_status' and performance.ready.is_set() and advance:
                performance.advance()
        with tempfile.TemporaryDirectory() as folder, patch.object(core.SimMeter, 'read', read):
            core.run(cfg, threading.Event(), emit, simulation=True, max_cycles=30,
                     output_dir=folder, controls=controls)
            files = list(Path(folder).glob('*_performance.csv'))
            rows = []
            if files:
                with files[0].open(encoding='utf-8-sig') as stream:
                    rows = list(csv.DictReader(stream))
        return events, rows, performance

    def test_same_temperature_points_switch_power_and_finish_manually(self):
        events, rows, p = self.run_points([(300,.02,.02),(300,.03,.04)], [20])
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(any('全部性能点完成' in value for kind,value in events if kind=='performance_status'))
        self.assertEqual({r['point'] for r in rows}, {'1','2'})
        self.assertEqual({float(r['heater_target_W']) for r in rows}, {.02,.03})
        self.assertFalse(p.ready.is_set())

    def test_lower_temperature_requires_new_cooling_crossing(self):
        events, rows, p = self.run_points([(300,.02,.02),(280,.03,.04)], [20]*12+[0]*60)
        self.assertFalse(events[-1][1]['error'])
        self.assertTrue(any(r['point']=='2' and r['phase']=='cooling' for r in rows))
        self.assertTrue(any(r['point']=='2' and r['phase']=='testing' for r in rows))

    def test_never_advances_without_manual_request(self):
        events, rows, p = self.run_points([(300,.02,.02),(300,.03,.04)], [20], advance=False)
        self.assertEqual(p.index, 0)
        self.assertEqual({r['point'] for r in rows}, {'1'})

    def test_heater_cancel_terminates_plan_without_restarting(self):
        events, rows, p = self.run_points([(300,.02,.02),(300,.03,.04)], [20],
                                          advance=False, cancel_heater=True)
        self.assertTrue(events[-1][1]['error'])
        self.assertEqual(p.index, 0)
        self.assertEqual(sum(kind=='heater_sample' for kind,value in events), 1)
        self.assertFalse(p.ready.is_set())

    def test_early_next_request_is_ignored(self):
        p = PerformanceRun(parse_plan(plan_text([(300,.02,.02)]), self.cfg()))
        p.advance()
        self.assertFalse(p.next_requested.is_set())

    def test_invalid_temperature_aborts_instead_of_starting_heater(self):
        events, rows, p = self.run_points([(300,.02,.02)], [200])
        self.assertTrue(events[-1][1]['error'])
        self.assertFalse(any(kind=='heater_sample' for kind,value in events))


if __name__ == '__main__':
    unittest.main()
