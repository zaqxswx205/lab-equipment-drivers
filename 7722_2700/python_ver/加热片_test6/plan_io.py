"""Read performance parameters stored on drive stages; retain legacy import."""
import configparser
from pathlib import Path
from performance import parse_plan, PerformanceRun
from ui.profile_editor import rows_from_text, text_from_rows, TEST_FIELDS


def import_legacy_rows(text, legacy):
    rows = rows_from_text(text)
    parser = configparser.ConfigParser(interpolation=None)
    if legacy:
        parser.read_string(legacy)
    result = []
    changed = False
    for row in rows:
        if row['heater_w'] or not row['points'].strip():
            result.append(row)
            continue
        for j, identifier in enumerate(row['points'].split(',')):
            section = 'Point '+identifier.strip()
            if not parser.has_section(section):
                raise ValueError(f'旧方案关联{section}，但未找到参数；请先恢复旧性能点文件')
            copy = dict(row, extra=dict(row['extra']))
            for key in TEST_FIELDS:
                copy[key] = parser.get(section,key,fallback=copy[key])
            copy['temperature'] = parser.get(section,'temperature_k')
            if j:
                copy.update(trigger='temperature',seconds='')
            copy['points'] = ''
            result.append(copy)
            changed = True
    return result, changed


def points_from_drive(text, cfg, legacy=''):
    rows, _ = import_legacy_rows(text, legacy)
    sections = []
    for row in rows:
        if row['heater_w'].strip():
            sections.append(f'[Point {len(sections)+1}]\ntemperature_k={row["temperature"]}\n'+
                            '\n'.join(f'{key}={row[key]}' for key in TEST_FIELDS))
    return parse_plan('\n'.join(sections), cfg) if sections else ()
