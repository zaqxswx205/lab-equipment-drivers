import tkinter as tk
import unittest
from unittest.mock import patch

from core import Config
from ui.parameter_confirmation import (ParameterConfirmation, confirmation_values,
                                       fields_for, numeric_equal)


class ParameterConfirmationTests(unittest.TestCase):
    def test_field_scope_and_kelvin(self):
        self.assertEqual(len(fields_for('drive')), 9)
        self.assertEqual(len(fields_for('heater')), 6)
        self.assertNotIn('max_voltage', dict(fields_for('heater')))
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
            dialog.variables['max_voltage'].set('5.00')
            dialog.accept()
            self.assertTrue(dialog.result)
            cancelled = ParameterConfirmation(root, 'heater', confirmation_values(Config(), 'heater'))
            self.assertEqual(len(cancelled.variables), 6)
            cancelled.destroy()
            self.assertFalse(cancelled.result)
        finally:
            root.destroy()

    def test_cancel_prevents_manual_start(self):
        from ui.app import App
        app = App()
        app.withdraw()
        app.heater_link.set(False)
        app.variables['max_voltage'][0].set('10')
        try:
            for part in ('drive', 'heater'):
                with patch('ui.actions.confirm_parameters', return_value=False) as confirm, \
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
