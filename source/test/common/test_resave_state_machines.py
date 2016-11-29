import pytest
from os.path import join, realpath, dirname
import rafcon
import subprocess
import sys
import testing_utils


def test_library_resave():
    script = join(dirname(realpath(rafcon.__file__)), "gui", "resave_state_machines.py")
    config_path = join(rafcon.__path__[0], "..", "test", "common", "configs_for_start_script_test", "valid_config")
    library_folder = join(rafcon.__path__[0], "..", "libraries", "generic")
    target_folder = join(testing_utils.RAFCON_TEMP_PATH_TEST_BASE, "resave_test", "test_library_resave")
    cmd = sys.executable + " %s %s %s %s" % (script, config_path, library_folder, target_folder)
    cmd_res = subprocess.call(cmd, shell=True)
    assert cmd_res == 0
    import os.path
    assert os.path.isfile(join(target_folder, "wait", "statemachine.json"))


if __name__ == '__main__':
    test_library_resave()
    # pytest.main(['-s', __file__])