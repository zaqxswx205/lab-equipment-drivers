"""Named reference curves shared by conversion, configuration and the UI."""
from .lakeshore import PT100_TABLE as LAKESHORE_TABLE
from .user_pt100 import PT100_TABLE as USER_TABLE

CURVE_LABELS = {
    'user_table': '常用表（35.15–349.15 K）',
    'lakeshore': 'Lake Shore（30–800 K）',
}
CURVE_KEYS = {label: key for key, label in CURVE_LABELS.items()}


def curve_table(curve='user_table'):
    if curve not in CURVE_LABELS:
        raise ValueError('换算曲线只支持lakeshore或user_table')
    return LAKESHORE_TABLE if curve == 'lakeshore' else USER_TABLE


def curve_limits(curve='user_table'):
    table = curve_table(curve)
    return table[0][0], table[-1][0]
