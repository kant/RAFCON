import sys
import logging
import gtk
import threading
import time
import os
import signal

# general tool elements
from rafcon.utils import log

# mvc elements
from rafcon.mvc.models import GlobalVariableManagerModel
from rafcon.mvc.controllers.main_window import MainWindowController
from rafcon.mvc.views.main_window import MainWindowView

# singleton elements
import rafcon.mvc.singleton

# test environment elements
import testing_utils
from testing_utils import call_gui_callback
import pytest


def create_models():
    logger = log.get_logger(__name__)
    logger.setLevel(logging.DEBUG)
    #logging.getLogger('gtkmvc').setLevel(logging.DEBUG)
    for handler in logging.getLogger('gtkmvc').handlers:
        logging.getLogger('gtkmvc').removeHandler(handler)
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(logging.Formatter("%(asctime)s: %(levelname)-8s - %(name)s:  %(message)s"))
    stdout.setLevel(logging.DEBUG)
    logging.getLogger('gtkmvc').addHandler(stdout)
    logging.getLogger('statemachine.state').setLevel(logging.DEBUG)
    logging.getLogger('controllers.state_properties').setLevel(logging.DEBUG)

    global_var_manager_model = GlobalVariableManagerModel()
    global_var_manager_model.global_variable_manager.set_variable("global_variable_1", "value1")
    global_var_manager_model.global_variable_manager.set_variable("global_variable_2", "value2")

    return logger, global_var_manager_model

@log.log_exceptions(None, gtk_quit=True)
def trigger_gui_signals(*args):
    print "Wait for the gui to initialize"
    time.sleep(2.0)
    sm_manager_model = args[0]
    main_window_controller = args[1]
    menubar_ctrl = main_window_controller.get_controller('menu_bar_controller')

    call_gui_callback(menubar_ctrl.on_step_mode_activate, None, None)
    number_of_steps = 9
    # TODO: This time is dangerous! If the PC where this test runs is too slow the test will fail! Redesign test!
    # Experience values: on a 64bit Intel Xeon with 1,2 GHz 8 core PC 0.03 are safe enough
    sleep_time = 0.05
    time.sleep(sleep_time)
    for i in range(number_of_steps):
        call_gui_callback(menubar_ctrl.on_step_into_activate, None, None)
        time.sleep(sleep_time)

    for i in range(number_of_steps):
        call_gui_callback(menubar_ctrl.on_backward_step_activate, None, None)
        time.sleep(sleep_time)

    sm = rafcon.statemachine.singleton.state_machine_manager.get_active_state_machine()
    time.sleep(sleep_time)

    for key, sd in sm.root_state.scoped_data.iteritems():
        if sd.name == "beer_number":
            assert sd.value == 100
        elif sd.name == "wine_number":
            assert sd.value == 40
        elif sd.name == "whiskey_number":
            assert sd.value == 20

    call_gui_callback(menubar_ctrl.on_save_as_activate, None, None, testing_utils.get_unique_temp_path())

    call_gui_callback(menubar_ctrl.on_stop_activate, None)
    call_gui_callback(menubar_ctrl.on_quit_activate, None)


def test_backward_stepping(caplog):

    testing_utils.test_multithrading_lock.acquire()
    testing_utils.remove_all_libraries()
    rafcon.statemachine.singleton.state_machine_manager.delete_all_state_machines()
    os.chdir(testing_utils.RAFCON_PATH + "/mvc/")
    gtk.rc_parse("./themes/dark/gtk-2.0/gtkrc")
    signal.signal(signal.SIGINT, rafcon.statemachine.singleton.signal_handler)

    logger, gvm_model = create_models()

    rafcon.statemachine.singleton.library_manager.initialize()

    [state_machine, version, creation_time] = rafcon.statemachine.singleton.\
        global_storage.load_statemachine_from_path(testing_utils.get_test_sm_path("backward_step_barrier_test"))

    main_window_view = MainWindowView()
    rafcon.statemachine.singleton.state_machine_manager.add_state_machine(state_machine)
    if testing_utils.sm_manager_model is None:
        testing_utils.sm_manager_model = rafcon.mvc.singleton.state_machine_manager_model

    main_window_controller = MainWindowController(testing_utils.sm_manager_model, main_window_view,
                                                  editor_type="LogicDataGrouped")
    thread = threading.Thread(target=trigger_gui_signals,
                              args=[testing_utils.sm_manager_model, main_window_controller])
    thread.start()

    gtk.main()
    logger.debug("Gtk main loop exited!")
    thread.join()
    logger.debug("Joined test triggering thread!")
    os.chdir(testing_utils.RAFCON_PATH + "/../test/common")

    testing_utils.reload_config()
    testing_utils.test_multithrading_lock.release()
    testing_utils.assert_logger_warnings_and_errors(caplog)


if __name__ == '__main__':
    test_backward_stepping(None)
    # pytest.main([__file__])