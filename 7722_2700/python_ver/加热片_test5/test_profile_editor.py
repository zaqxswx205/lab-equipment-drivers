import configparser
import tempfile
import unittest
from pathlib import Path

from core import Config
from drive_profile import load_profile
from ui.profile_editor import rows_from_text, text_from_rows
from ui.profile_help import with_profile_help


class ProfileEditorTests(unittest.TestCase):
    def test_shared_time_loads_active_legacy_value_and_saves_correct_field(self):
        from ui.profile_editor import judgment_field, set_cell_value
        text=('[Drive]\nstrategy=time\ntemperature_unit=K\n[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
              'heater_w=.02\ntest_temperature_k=300\nstability_mode=peak_to_peak\nduration_s=600\nwindow_s=120\n')
        rows=rows_from_text(text)
        row=rows[0]
        self.assertEqual(row[judgment_field(row)],'120')
        self.assertTrue(set_cell_value(row,'judgment_s','180'))
        converted=text_from_rows(rows,text)
        parser=configparser.ConfigParser()
        parser.read_string(converted)
        self.assertEqual(parser['Step 1']['window_s'],'180')
        self.assertEqual(parser['Step 1']['duration_s'],'600')

    def test_completion_change_keeps_shared_time_value(self):
        from ui.profile_editor import judgment_field, set_cell_value
        text='[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\nheater_w=.02\nstability_mode=duration\nduration_s=600\nwindow_s=120\n'
        row=rows_from_text(text)[0]
        set_cell_value(row,'stability_mode','peak_to_peak')
        self.assertEqual(row[judgment_field(row)],'600')
        set_cell_value(row,'judgment_s','240')
        set_cell_value(row,'stability_mode','duration')
        self.assertEqual(row[judgment_field(row)],'240')

    def test_inapplicable_cells_cannot_change_parameters(self):
        from ui.profile_editor import cell_enabled, set_cell_value
        row=rows_from_text('[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\nheater_w=.02\n')[0]
        self.assertFalse(cell_enabled(row,'judgment_s'))
        self.assertFalse(set_cell_value(row,'judgment_s','100'))
        self.assertFalse(set_cell_value(row,'peak_to_peak_k','9'))
        set_cell_value(row,'stability_mode','duration')
        self.assertTrue(cell_enabled(row,'judgment_s'))
        self.assertFalse(cell_enabled(row,'peak_to_peak_k'))
        set_cell_value(row,'stability_mode','peak_to_peak')
        self.assertTrue(cell_enabled(row,'peak_to_peak_k'))
        set_cell_value(row,'heater_w','')
        self.assertTrue(cell_enabled(row,'stability_mode'))
        self.assertTrue(cell_enabled(row,'judgment_s'))

    def test_completion_can_be_filled_before_heater_and_survives_save(self):
        from ui.profile_editor import set_cell_value, cell_enabled
        text='[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
        rows=rows_from_text(text)
        row=rows[0]
        self.assertTrue(set_cell_value(row,'stability_mode','duration'))
        self.assertTrue(set_cell_value(row,'judgment_s','120'))
        loaded=rows_from_text(text_from_rows(rows,text))[0]
        self.assertEqual(loaded['heater_w'],'')
        self.assertEqual(loaded['stability_mode'],'duration')
        self.assertEqual(loaded['duration_s'],'120')
        self.assertTrue(set_cell_value(loaded,'stability_mode','peak_to_peak'))
        self.assertTrue(cell_enabled(loaded,'peak_to_peak_k'))

    def test_legacy_units_and_binding_roundtrip(self):
        text = ('[Drive]\nstrategy=mixed\ntime_unit=min\ntemperature_unit=C\n'
                '[Step 1]\ntrigger=time_or_temperature\nat=2\ntemperature_at=0\n'
                'mode=power\nvalue=.2\nperformance_points=1,2\n')
        rows = rows_from_text(text)
        self.assertEqual(float(rows[0]['seconds']),120)
        self.assertEqual(float(rows[0]['temperature']),273.15)
        converted = text_from_rows(rows,text)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'profile.ini'
            path.write_text(converted,encoding='utf-8')
            profile=load_profile(path,Config())
        self.assertEqual(profile.steps[0].at,120)
        self.assertEqual(profile.steps[0].temperature_at,0)
        self.assertEqual(profile.steps[0].performance_points,(1,2))

    def test_switching_trigger_discards_irrelevant_fields(self):
        text='[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
        rows=rows_from_text(text)
        rows[0].update(trigger='temperature',temperature='280',points='1')
        converted=text_from_rows(rows,text)
        parser=configparser.ConfigParser()
        parser.read_string(converted)
        self.assertEqual(parser['Step 1']['at'],'280')
        self.assertNotIn('temperature_at',parser['Step 1'])
        self.assertEqual(parser['Step 1']['performance_points'],'1')

    def test_reordering_renumbers_stages_and_preserves_values(self):
        text=('[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
              '[Step 2]\nat=10\nmode=power\nvalue=.2\n')
        text += '\n; 用户备注：预留阶段\n; [Step 3]\n; value = 0.35\n'
        rows=rows_from_text(text)
        self.assertIn('; value = 0.35', text_from_rows(rows,text))
        parser=configparser.ConfigParser()
        parser.read_string(text_from_rows(rows[::-1],text))
        self.assertEqual(parser['Step 1']['value'],'.2')
        self.assertEqual(parser['Step 2']['value'],'1')

    def test_examples_upgrade_old_help_without_active_extra_stages(self):
        old='; 驱动方案填写说明\n; 旧说明\n\n[Drive]\nstrategy=time\n[Step 1]\nat=0\nmode=voltage\nvalue=1\n'
        updated=with_profile_help(old)
        self.assertIn('; 统一阶段表格示例',updated)
        self.assertEqual(updated,with_profile_help(updated))
        self.assertEqual(len(rows_from_text(updated)),1)
