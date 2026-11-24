"""Run original offline regression tests against the extracted standalone libraries."""
import importlib.util
import sys
import unittest
from pathlib import Path

import itech7722
import keithley2700


def main():
    base = Path(__file__).resolve().parent.parent
    sys.modules['acquisition'] = keithley2700
    for name in ('device', 'config', 'sequence', 'recorder', 'feedback', 'engine'):
        sys.modules['itech7722_' + name] = itech7722
    suite = unittest.TestSuite()
    folders = [(base / '2700/python_ver/test_3',
                ['test_acquisition', 'test_7700', 'test_7702', 'test_mixed']),
               (base / '7722/python_ver/test_1',
                ['test_itech7722', 'test_regressions', 'test_feedback',
                 'test_keypoints', 'test_recording_schema'])]
    for folder, names in folders:
        sys.path.append(str(folder))
        for name in names:
            spec = importlib.util.spec_from_file_location(name, folder / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    import test_public_api
    suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(test_public_api))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
