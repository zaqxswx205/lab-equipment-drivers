import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import Config
from ui.parameter_confirmation import (ParameterConfirmation, confirmation_values,
                                       fields_for, numeric_equal, confirm_parameters, DRIVE_FIELDS, COMMON_FIELDS)


class ParameterConfirmationTests(unittest.TestCase):
    def test_field_scope_and_kelvin(self):
        self.assertEqual(len(fields_for('drive')), 10)
        self.assertEqual(fields_for('heater'), fields_for('drive'))
        for key in ('frequency','max_voltage','max_current','max_power'):
            self.assertIn(key, dict(fields_for('heater')))
        self.assertEqual(confirmation_values(Config(heater_trigger_temperature=0), 'heater')
                         ['heater_trigger_temperature'], 273.15)

    def test_numeric_equality_rejects_nonfinite_and_accepts_formatting(self):
        self.assertTrue(numeric_equal(' 5.00 ', 5))
        self.assertTrue(numeric_equal('1e-1', .1))
        for value in ('', 'wrong', 'NaN', 'Infinity', '5.001'):
            self.assertFalse(numeric_equal(value, 5))

    def test_dialog_immediate_feedback_and_no_bypass(self):
        root = tk.Tk()
        root.withdraw()
        try:
            expected = confirmation_values(Config(), 'drive')
            dialog = ParameterConfirmation(root, 'drive', expected)
            root.update()
            dialog.accept()
            self.assertFalse(dialog.result)
            for key, value in expected.items():
                dialog.variables[key].set(str(value))
            self.assertEqual(str(dialog.confirm_button['state']), 'normal')
            dialog.variables['max_voltage'].set('123')
            self.assertEqual(dialog.markers['max_voltage']['text'], '输入参数不一致，请确认')
            self.assertEqual(dialog.markers['max_voltage']['fg'], '#cc0000')
            self.assertEqual(str(dialog.confirm_button['state']), 'disabled')
            dialog.accept()
            self.assertFalse(dialog.result)
            dialog.variables['max_voltage'].set(f'{expected["max_voltage"]:.2f}')
            dialog.accept()
            self.assertTrue(dialog.result)
            cancelled = ParameterConfirmation(root, 'heater', confirmation_values(Config(), 'heater'))
            self.assertEqual(len(cancelled.variables), 10)
            cancelled.destroy()
            self.assertFalse(cancelled.result)
        finally:
            root.destroy()

    def test_heater_cannot_confirm_with_only_heater_and_meter_fields(self):
        root = tk.Tk()
        root.withdraw()
        try:
            expected = confirmation_values(Config(), 'heater')
            dialog = ParameterConfirmation(root, 'heater', expected)
            for key, _ in COMMON_FIELDS:
                dialog.variables[key].set(str(expected[key]))
            dialog.accept()
            self.assertFalse(dialog.result)
            self.assertEqual(str(dialog.confirm_button['state']), 'disabled')
            for key, _ in DRIVE_FIELDS:
                dialog.variables[key].set(str(expected[key]))
            dialog.variables['frequency'].set(str(expected['frequency']+1))
            dialog.accept()
            self.assertFalse(dialog.result)
            dialog.variables['frequency'].set(str(expected['frequency']))
            dialog.accept()
            self.assertTrue(dialog.result)
        finally:
            root.destroy()

    def test_heater_rejects_ui_ac_values_that_differ_from_session_snapshot(self):
        cfg = Config()
        expected = confirmation_values(cfg, 'heater')
        for key, _ in DRIVE_FIELDS:
            with self.subTest(key=key):
                values = dict(expected)
                values[key] += 1
                parent = SimpleNamespace(variables={name:(SimpleNamespace(get=lambda v=value:str(v)),float)
                                                     for name,value in values.items()})
                parent.variables['heater_control']=(SimpleNamespace(get=lambda:'恒定功率'),str)
                with patch('ui.parameter_confirmation.notices.error') as error, \
                     patch('ui.parameter_confirmation.ParameterConfirmation') as dialog:
                    self.assertFalse(confirm_parameters(parent, 'heater', cfg))
                    dialog.assert_not_called()
                    error.assert_called_once()
                    self.assertIs(error.call_args.args[0], parent)

    def test_voltage_mode_asks_set_voltage_without_inactive_power_target(self):
        cfg=Config(heater_control='voltage',heater_voltage=2,heater_max_voltage=5,heater_power=99)
        expected=confirmation_values(cfg,'heater')
        self.assertNotIn('heater_power',expected)
        self.assertEqual(expected['heater_voltage'],2)
        self.assertEqual(len(expected),9)
        self.assertEqual(fields_for('heater','voltage'),fields_for('drive','voltage'))
        root=tk.Tk()
        root.withdraw()
        try:
            dialog=ParameterConfirmation(root,'heater',expected)
            self.assertNotIn('heater_power',dialog.variables)
            for key,value in expected.items():
                dialog.variables[key].set(str(value))
            dialog.variables['heater_voltage'].set('3')
            dialog.accept()
            self.assertFalse(dialog.result)
            dialog.variables['heater_voltage'].set('2')
            dialog.accept()
            self.assertTrue(dialog.result)
        finally:
            root.destroy()

    def test_voltage_confirmation_ignores_power_and_uses_actual_set_voltage(self):
        cfg=Config(heater_control='voltage',heater_voltage=2,heater_max_voltage=5)
        expected=confirmation_values(cfg,'heater')
        variables={('heater_max_voltage' if key=='heater_voltage' else key):
                   (SimpleNamespace(get=lambda v=value:str(v)),float) for key,value in expected.items()}
        variables['heater_control']=(SimpleNamespace(get=lambda:'恒定电压'),str)
        variables['heater_power']=(SimpleNamespace(get=lambda:'999'),float)
        parent=SimpleNamespace(variables=variables,wait_window=lambda dialog:None)
        with patch('ui.parameter_confirmation.ParameterConfirmation',return_value=SimpleNamespace(result=True)) as dialog, \
             patch('ui.parameter_confirmation.notices.error') as error:
            self.assertTrue(confirm_parameters(parent,'heater',cfg))
            error.assert_not_called()
            self.assertEqual(dialog.call_args.args[2]['heater_voltage'],2)

    def test_changed_heater_mode_is_rejected_even_with_matching_numbers(self):
        cfg=Config()
        expected=confirmation_values(cfg,'heater')
        variables={key:(SimpleNamespace(get=lambda v=value:str(v)),float) for key,value in expected.items()}
        variables['heater_control']=(SimpleNamespace(get=lambda:'恒定电压'),str)
        parent=SimpleNamespace(variables=variables)
        with patch('ui.parameter_confirmation.notices.error') as error, \
             patch('ui.parameter_confirmation.ParameterConfirmation') as dialog:
            self.assertFalse(confirm_parameters(parent,'heater',cfg))
            dialog.assert_not_called()
            error.assert_called_once()

    def test_cancel_prevents_manual_start(self):
        from ui.app import App
        app = App()
        app.withdraw()
        app.heater_link.set(False)
        app.variables['max_voltage'][0].set('10')
        try:
            for part in ('drive', 'heater'):
                from drive_profile import Profile, Step
                with patch('ui.actions.ask_manual_drive', return_value=Profile('time',[Step(0,'voltage',1)])), \
                     patch('ui.actions.confirm_parameters', return_value=False) as confirm, \
                     patch('ui.actions.save_config') as save, \
                     patch('ui.actions.threading.Thread') as thread:
                    app.start_part(part)
                    confirm.assert_called_once()
                    self.assertEqual(confirm.call_args.args[1], part)
                    save.assert_not_called()
                    thread.assert_not_called()
                    self.assertFalse(app.controls.drive.is_set())
                    self.assertFalse(app.controls.heater.is_set())
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()
