from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import Config, save_config
from drive_profile import load_profile
from profile.settings import settings_from_profile


def profile_text(voltage=15, power=38, heater=25):
    return (f'[Drive]\nstrategy=time\ntemperature_unit=K\n'
            f'[Step 1]\nat=0\nmode=voltage\nvalue={voltage}\n'
            f'[Step 2]\nat=1\nmode=power\nvalue={power}\n'
            f'heater_w={heater}\ntest_temperature_k=280\n')


class ProfileSettingsTests(unittest.TestCase):
    def test_bundled_defaults_are_valid(self):
        path = Path(__file__).resolve().parents[1] / 'drive_time.ini'
        load_profile(path, Config())

    def test_stage_requirements_and_heater_first_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drive.ini'
            path.write_text(profile_text(), encoding='utf-8')
            original = Config(max_voltage=5)
            result = settings_from_profile(path, original)
            self.assertEqual(original.max_voltage, 5)
            self.assertEqual(original.heater_power, .2)
            self.assertEqual(result.max_voltage, 15)
            self.assertGreaterEqual(result.max_power * .95, 38)
            self.assertEqual(result.heater_power, 25)
            self.assertGreaterEqual(result.heater_max_power * .95, 25)
            self.assertEqual(result.heater_control, 'power')
            self.assertFalse(result.heater_enabled)
            self.assertEqual(result.frequency, original.frequency)
            self.assertEqual(result.max_current, original.max_current)
            self.assertEqual(result.heater_max_voltage, original.heater_max_voltage)
            self.assertEqual(result.heater_max_current, original.heater_max_current)
            load_profile(path, result)

    def test_existing_higher_limits_and_no_heater_stage_are_preserved(self):
        path = Path(__file__).resolve().parents[1] / 'drive_time.ini'
        original = Config(max_voltage=25, max_power=80, heater_power=3, heater_control='voltage')
        self.assertEqual(settings_from_profile(path, original), original)

    def test_invalid_file_does_not_partially_change_settings(self):
        for content in (profile_text(voltage=301), profile_text(power=571),
                        profile_text(heater=571), profile_text(heater='nan'),
                        profile_text().replace('at=1', 'at=0'),
                        profile_text().replace('test_temperature_k=280', 'test_temperature_k=1')):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'drive.ini'
                path.write_text(content, encoding='utf-8')
                original = Config()
                snapshot = replace(original)
                with self.assertRaises(ValueError):
                    settings_from_profile(path, original)
                self.assertEqual(original, snapshot)

    def make_app(self, directory, path):
        from ui.app import App
        cfg = Config(profile_file=str(path), max_voltage=5)
        save_config(Path(directory) / 'config.ini', cfg)
        with patch('ui.app.BASE', Path(directory)):
            app = App()
        app.withdraw()
        self.addCleanup(app.destroy)
        return app

    def test_startup_reads_externally_edited_file_before_building_form(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drive.ini'
            path.write_text(profile_text(), encoding='utf-8')
            app = self.make_app(directory, path)
            self.assertEqual(float(app.variables['max_voltage'][0].get()), 15)
            self.assertEqual(float(app.variables['heater_power'][0].get()), 25)
            self.assertEqual(len(app.profile_table.get_children()), 2)
            self.assertIsNone(app.worker)
            # Re-open after an external edit: the new file, not the saved limits,
            # determines the minimum settings shown in the form.
            path.write_text(profile_text(voltage=23, heater=28), encoding='utf-8')
            second = self.make_app(directory, path)
            self.assertEqual(float(second.variables['max_voltage'][0].get()), 23)
            self.assertEqual(float(second.variables['heater_power'][0].get()), 28)

    def test_manual_edit_revalidates_and_only_clears_profile_alarm(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drive.ini'
            path.write_text(profile_text(), encoding='utf-8')
            app = self.make_app(directory, path)
            app.variables['max_voltage'][0].set('5')
            app.preview_profile()
            self.assertIn('15 V', app.system_text.get())
            self.assertEqual(len(app.profile_table.get_children()), 0)
            with patch('ui.actions.confirm_parameters') as confirm:
                app.start_part('drive', use_profile=True)
                confirm.assert_not_called()
            app.report_status('2700通信失败', 'error', source='acquisition')
            app.variables['max_voltage'][0].set('20')
            app.after(380, app.quit)
            app.mainloop()
            self.assertEqual(len(app.profile_table.get_children()), 2)
            self.assertNotIn('方案不可运行', app.system_text.get())
            self.assertNotIn('15 V', app.system_text.get())
            self.assertIn('2700通信失败', app.system_text.get())
            # Form edits do not alter a session's configuration snapshot.
            self.assertEqual(app.cfg.max_voltage, 15)

    def test_refresh_reads_external_changes_and_syncs_heater(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'drive.ini'
            path.write_text(profile_text(), encoding='utf-8')
            app = self.make_app(directory, path)
            path.write_text(profile_text(voltage=21, heater=30), encoding='utf-8')
            app.reload_profile()
            self.assertEqual(float(app.variables['max_voltage'][0].get()), 21)
            self.assertEqual(float(app.variables['heater_power'][0].get()), 30)
            self.assertEqual(len(app.profile_table.get_children()), 2)
