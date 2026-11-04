"""Compare the built C program with the original Python planner; no serial I/O."""
import importlib.util
from decimal import Decimal
from pathlib import Path
import random
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('original', ROOT / 'gs610_sweep.py')
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)
exe = str(ROOT / 'gs610_sweep.exe')
rng = random.Random(610)
cases = [
    ('-3.2', '3.2', '0.08', '12', True),
    ('0', '0', '1', '12', True),
    ('0.2', '-0.2', '0.15', '12', False),
    ('0', '2.05e-5', '1e-10', '110', True),
]
for _ in range(120):
    cases.append((
        str(Decimal(rng.randrange(-320000, 320001)) / 100000),
        str(Decimal(rng.randrange(-320000, 320001)) / 100000),
        str(Decimal(rng.randrange(1, 90000)) / 100000),
        str(rng.choice([0.001, 12, 30, 60, 110])), bool(rng.randrange(2)),
    ))
for case in cases:
    start, stop, step, limit, returning = case
    try:
        expected, source_range, _ = original.plan_sweep(*case)
        valid = True
    except ValueError:
        valid = False
    result = subprocess.run([
        exe, '--dry-run', '--start', start, '--stop', stop, '--step', step,
        '--voltage-limit', limit, '--round-trip' if returning else '--single-pass',
    ], capture_output=True, encoding='utf-8', check=False)
    assert (result.returncode == 0) == valid, (case, result.stdout, result.stderr)
    if valid:
        lines = result.stdout.splitlines()
        assert [Decimal(line[:-2]) for line in lines[1:]] == expected, case
        assert f'固定量程 {source_range} A' in lines[0], case
print(f'{len(cases)} Python/C planner parity cases passed.')

for args in [
    ['--timeout', 'nan'], ['--dwell', '-1'], ['--step', '0'],
    ['--round-trip', '--single-pass'], ['--bytesize', '9'],
]:
    result = subprocess.run([exe, '--dry-run', '--round-trip', *args],
                            capture_output=True, encoding='utf-8', check=False)
    assert result.returncode != 0, args
print('5 CLI invalid-option checks passed.')
