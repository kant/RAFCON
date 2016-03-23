#!/usr/bin/env python

import logging
import os
import gtk
import signal
import argparse
from os.path import realpath, dirname, join, expanduser, expandvars, isdir

import rafcon

from rafcon.utils import log

from rafcon.statemachine.start import state_machine_path
from rafcon.statemachine.config import global_config
from rafcon.statemachine.storage import storage
from rafcon.statemachine.state_machine import StateMachine
from rafcon.statemachine.states.hierarchy_state import HierarchyState
import rafcon.statemachine.singleton as sm_singletons

from rafcon.mvc.controllers.main_window import MainWindowController
from rafcon.mvc.views.main_window import MainWindowView

import rafcon.mvc.singleton as mvc_singletons
from rafcon.mvc.config import global_gui_config
from rafcon.mvc.runtime_config import global_runtime_config
from rafcon.network.network_config import global_net_config


def setup_logger():
    import sys
    # Apply defaults to logger of gtkmvc
    for handler in logging.getLogger('gtkmvc').handlers:
        logging.getLogger('gtkmvc').removeHandler(handler)
    stdout = logging.StreamHandler(sys.stdout)
    stdout.setFormatter(logging.Formatter("%(asctime)s: %(levelname)-8s - %(name)s:  %(message)s"))
    stdout.setLevel(logging.DEBUG)
    logging.getLogger('gtkmvc').addHandler(stdout)


def config_path(path):
    if not path or path == 'None':
        return None
    # replace ~ with /home/user
    path = expanduser(path)
    # e.g. replace ${RAFCON_PATH} with the root path of RAFCON
    path = expandvars(path)
    if not isdir(path):
        raise argparse.ArgumentTypeError("{0} is not a valid path".format(path))
    if os.access(path, os.R_OK):
        return path
    else:
        raise argparse.ArgumentTypeError("{0} is not a readable dir".format(path))


if __name__ == '__main__':
    setup_logger()
    # from rafcon.utils import log
    logger = log.get_logger("start")
    logger.info("RAFCON launcher")

    rafcon_root_path = dirname(realpath(rafcon.__file__))
    if not os.environ.get('RAFCON_PATH', None):
        # set env variable RAFCON_PATH to the root directory of RAFCON
        os.environ['RAFCON_PATH'] = rafcon_root_path

    if not os.environ.get('RAFCON_LIB_PATH', None):
        # set env variable RAFCON_LIB_PATH to the library directory of RAFCON (when not using RMPM)
        os.environ['RAFCON_LIB_PATH'] = join(dirname(rafcon_root_path), 'libraries')

    home_path = expanduser('~')
    if home_path:
        home_path = join(home_path, ".config", "rafcon")
    else:
        home_path = 'None'

    parser = argparse.ArgumentParser(description='Start RAFCON')

    parser.add_argument('-n', '--new', action='store_true', help="whether to create a new state-machine")
    parser.add_argument('-o', '--open', action='store', nargs='*', type=state_machine_path, dest='sm_paths',
                        metavar='path',
                        help="specify directories of state-machines that shall be opened. Paths must contain a "
                             "statemachine.yaml file")
    parser.add_argument('-c', '--config', action='store', type=config_path, metavar='path', dest='config_path',
                        default=home_path, nargs='?', const=home_path,
                        help="path to the configuration file config.yaml. Use 'None' to prevent the generation of "
                             "a config file and use the default configuration. Default: {0}".format(home_path))
    parser.add_argument('-g', '--gui_config', action='store', type=config_path, metavar='path', dest='gui_config_path',
                        default=home_path, nargs='?', const=home_path,
                        help="path to the configuration file gui_config.yaml. Use 'None' to prevent the generation of "
                             "a config file and use the default configuration. Default: {0}".format(home_path))
    parser.add_argument('-nc', '--net_config', action='store', type=config_path, metavar='path', dest='net_config_path',
                        default=home_path, nargs='?', const=home_path,
                        help="path to the configuration file net_config.yaml. Use 'None' to prevent the generation of "
                             "a config file and use the default configuration. Default: {0}".format(home_path))

    result = parser.parse_args()
    setup_config = vars(result)

    # Make mvc directory the working directory
    # Needed for views, which assume to be in the mvc path and import glade files relatively
    os.chdir(join(rafcon_root_path, 'mvc'))

    # Create the GUI-View
    main_window_view = MainWindowView()

    signal.signal(signal.SIGINT, sm_singletons.signal_handler)

    # load configuration files
    global_config.load(path=setup_config['config_path'])
    global_gui_config.load(path=setup_config['gui_config_path'])
    global_net_config.load(path=setup_config['net_config_path'])
    global_runtime_config.load(path=setup_config['gui_config_path'])

    if global_net_config.get_config_value('NETWORK_CONNECTIONS'):
        from rafcon.network.singleton import network_connections

        network_connections.initialize()

    # Initialize library
    sm_singletons.library_manager.initialize()

    if setup_config['sm_paths']:
        for path in setup_config['sm_paths']:
            try:
                state_machine = storage.load_statemachine_from_path(path)
                sm_singletons.state_machine_manager.add_state_machine(state_machine)
            except Exception as e:
                logger.exception("Could not load state-machine {0}".format(path))

    if setup_config['new']:
        root_state = HierarchyState()
        state_machine = StateMachine(root_state)
        sm_singletons.state_machine_manager.add_state_machine(state_machine)

    sm_manager_model = mvc_singletons.state_machine_manager_model

    main_window_controller = MainWindowController(sm_manager_model, main_window_view, editor_type='LogicDataGrouped')

    # Ensure that the next message is being printed (needed for LN manager to detect finished startup)
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.info("Ready")
    logger.setLevel(level)

    if global_net_config.get_config_value("NETWORK_CONNECTIONS", False):
        from twisted.internet import reactor
        from twisted.internet import gtk2reactor

        # needed for glib.idle_add, and signals
        gtk2reactor.install()
        reactor.run()
    else:
        gtk.main()

    # If there is a running state-machine, wait for it to be finished before exiting
    sm = sm_singletons.state_machine_manager.get_active_state_machine()
    if sm:
        sm.root_state.join()
