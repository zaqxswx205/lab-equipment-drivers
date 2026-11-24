import unittest
from unittest.mock import patch
from instruments.transport7722 import SimTransport
from panel_identification import identify_panel, PanelIdentificationError


class PanelTests(unittest.TestCase):
    def run_panel(self, tp, mode='DC', identity='ITECH,SIM7722,LOCAL,1.0'):
        record = ('COM9', '7722电源', identity, {'baud': 9600})
        with patch('power7722.time.sleep'):
            identify_panel(record, mode, transport_factory=lambda *a, **k: tp)

    def test_mode_changes_with_output_always_off(self):
        for mode in ('AC', 'DC'):
            with self.subTest(mode=mode):
                tp = SimTransport()
                tp.state['output'] = True
                self.run_panel(tp, mode)
                self.assertEqual(tp.state['mode'], mode)
                self.assertFalse(tp.state['output'])
                self.assertTrue(tp.closed)
                self.assertNotIn('OUTP:STAT ON', tp.commands)
                self.assertLess(tp.commands.index('OUTP:STAT?'), tp.commands.index('SOUR:NORM:MODE '+mode))

    def test_identity_changed_prevents_mode_change(self):
        tp = SimTransport()
        with self.assertRaises(PanelIdentificationError):
            self.run_panel(tp, identity='different')
        self.assertFalse(any(c.startswith('SOUR:NORM:MODE ') for c in tp.commands))
        self.assertFalse(tp.state['output'])
        self.assertTrue(tp.closed)

    def test_failed_off_prevents_mode_change(self):
        tp = SimTransport()
        original = tp.query
        tp.query = lambda cmd: '1' if cmd == 'OUTP:STAT?' else original(cmd)
        with self.assertRaises(PanelIdentificationError) as caught:
            self.run_panel(tp)
        self.assertFalse(caught.exception.off_confirmed)
        self.assertFalse(any(c.startswith('SOUR:NORM:MODE ') for c in tp.commands))
        self.assertTrue(tp.closed)

    def test_mode_failure_still_turns_off(self):
        tp = SimTransport()
        original = tp.query
        tp.query = lambda cmd: 'AC' if cmd == 'SOUR:NORM:MODE?' else original(cmd)
        with self.assertRaises(PanelIdentificationError):
            self.run_panel(tp)
        self.assertEqual(tp.commands[-1], 'OUTP:STAT?')
        self.assertFalse(tp.state['output'])
        self.assertTrue(tp.closed)
