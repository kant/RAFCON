"""
.. module:: container_state
   :platform: Unix, Windows
   :synopsis: A module to represent a generic container state in the state machine

.. moduleauthor:: Sebastian Brunner


"""
from copy import copy, deepcopy
from threading import Condition

from gtkmvc import Observable
from rafcon.statemachine.state_elements.scope import ScopedData, ScopedVariable

from rafcon.statemachine.enums import DataPortType, StateExecutionState, StateMachineExecutionStatus
from rafcon.statemachine.id_generator import *
from rafcon.statemachine.singleton import state_machine_execution_engine
from rafcon.statemachine.state_elements.data_flow import DataFlow
from rafcon.statemachine.state_elements.outcome import Outcome
from rafcon.statemachine.state_elements.transition import Transition
from rafcon.statemachine.states.state import State
from rafcon.statemachine.states.library_state import LibraryState
from rafcon.statemachine.storage import storage
from rafcon.statemachine.validity_check.validity_checker import ValidityChecker
from rafcon.utils.type_helpers import type_inherits_of_type

try:
    from collections import OrderedDict
except ImportError:
    OrderedDict = dict

from rafcon.utils import log
logger = log.get_logger(__name__)


class ContainerState(State):

    """A class for representing a state in the state machine

    Only the variables are listed that are not already contained in the state base class

    :ivar dict states: the child states of the container state of the state
    :ivar dict transitions: transitions between all child states
    :ivar dict data_flows: data flows between all child states
    :ivar str start_state_id: the state to start with when the hierarchy state is executed
    :ivar dict scoped_variables: the scoped variables of the container

    """

    def __init__(self, name=None, state_id=None, input_data_ports=None, output_data_ports=None, outcomes=None,
                 states=None, transitions=None, data_flows=None, start_state_id=None,
                 scoped_variables=None, v_checker=None):

        self._states = OrderedDict()
        self._transitions = {}
        self._data_flows = {}
        self._scoped_variables = {}
        self._scoped_data = {}
        # reference to an object that checks the validity of this container state
        self._v_checker = v_checker
        self._current_state = None
        # condition variable to wait for not connected states
        self._transitions_cv = Condition()
        self._child_execution = False

        State.__init__(self, name, state_id, input_data_ports, output_data_ports, outcomes)

        self.scoped_variables = scoped_variables if scoped_variables is not None else {}
        self.states = states if states is not None else {}
        self.transitions = transitions if transitions is not None else {}
        self.data_flows = data_flows if data_flows is not None else {}
        if start_state_id is not None:
            self.start_state_id = start_state_id

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- generic methods -----------------------------------------
    # ---------------------------------------------------------------------------------------------

    @staticmethod
    def state_to_dict(state):
        dict_representation = {
            'name': state.name,
            'state_id': state.state_id,
            'description': state.description,
            'input_data_ports': state.input_data_ports,
            'output_data_ports': state.output_data_ports,
            'outcomes': state.outcomes,
            'transitions': state.transitions,
            'data_flows': state.data_flows,
            'scoped_variables': state.scoped_variables
        }
        return dict_representation

    @classmethod
    def from_dict(cls, dictionary):
        states = None if 'states' not in dictionary else dictionary['states']
        transitions = dictionary['transitions']
        data_flows = dictionary['data_flows']
        state = cls(name=dictionary['name'],
                    state_id=dictionary['state_id'],
                    input_data_ports=dictionary['input_data_ports'],
                    output_data_ports=dictionary['output_data_ports'],
                    outcomes=dictionary['outcomes'],
                    states=None,
                    transitions=transitions if states else None,
                    data_flows=data_flows if states else None,
                    scoped_variables=dictionary['scoped_variables'],
                    v_checker=None)
        try:
            state.description = dictionary['description']
        except (TypeError, KeyError):  # (Very) old state machines do not have a description field
            import traceback
            formatted_lines = traceback.format_exc().splitlines()
            logger.warning("Erroneous description for state '{1}': {0}".format(formatted_lines[-1], dictionary['name']))

        if states:
            return state
        else:
            return state, dictionary['transitions'], dictionary['data_flows']

    @classmethod
    def from_yaml(cls, loader, node):
        dict_representation = loader.construct_mapping(node, deep=True)
        state, transitions, data_flows = cls.from_dict(dict_representation)
        return state, transitions, data_flows

    def __str__(self):
        return "{0} [{1} child states]".format(super(ContainerState, self).__str__(), len(self.states))

    def __eq__(self, other):
        # logger.info("compare method \n\t\t\t{0} \n\t\t\t{1}".format(self, other))
        if not isinstance(other, self.__class__):
            return False
        try:
            diff_states = [self.states[state_id] == state for state_id, state in other.states.iteritems()]
            diff_states.append(len(self.states) == len(other.states))
        except KeyError:
            return False
        return all(diff_states) and str(self) == str(other)

    def __copy__(self):
        input_data_ports = {elem_id: copy(elem) for elem_id, elem in self._input_data_ports.iteritems()}
        output_data_ports = {elem_id: copy(elem) for elem_id, elem in self._output_data_ports.iteritems()}
        outcomes = {elem_id: copy(elem) for elem_id, elem in self._outcomes.iteritems()}
        states = {elem_id: copy(elem) for elem_id, elem in self._states.iteritems()}
        scoped_variables = {elem_id: copy(elem) for elem_id, elem in self._scoped_variables.iteritems()}
        data_flows = {elem_id: copy(elem) for elem_id, elem in self._data_flows.iteritems()}
        transitions = {elem_id: copy(elem) for elem_id, elem in self._transitions.iteritems()}

        state = self.__class__(self.name, self.state_id, input_data_ports, output_data_ports, outcomes, states,
                               transitions, data_flows, None, scoped_variables, None)
        state.description = deepcopy(self.description)
        return state

    __deepcopy__ = __copy__

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- execution functions -------------------------------------
    # ---------------------------------------------------------------------------------------------

    def run(self, *args, **kwargs):
        """Implementation of the abstract run() method of the :class:`threading.Thread`

        Should be filled with code, that should be executed for each container_state derivative.
        :raises exceptions.NotImplementedError: in every case
        """
        raise NotImplementedError("The ContainerState.run() function has to be implemented!")

    def recursively_preempt_states(self):
        """ Preempt the state and all of it child states.
        """
        super(ContainerState, self).recursively_preempt_states()
        # notify the transition condition variable to let the state instantaneously stop
        self._transitions_cv.acquire()
        self._transitions_cv.notify_all()
        self._transitions_cv.release()
        for state in self.states.itervalues():
            state.recursively_preempt_states()

    def recursively_pause_states(self):
        """ Pause the state and all of it child states.
        """
        super(ContainerState, self).recursively_pause_states()
        for state in self.states.itervalues():
            state.recursively_pause_states()

    def recursively_resume_states(self):
        """ Resume the state and all of it child states.
        """
        super(ContainerState, self).recursively_resume_states()
        for state in self.states.itervalues():
            state.recursively_resume_states()

    def setup_run(self):
        """ Executes a generic set of actions that has to be called in the run methods of each derived state class.

        :return:
        """
        super(ContainerState, self).setup_run()
        # reset the scoped data
        self._scoped_data = {}
        self.add_default_values_of_scoped_variables_to_scoped_data()
        self.add_input_data_to_scoped_data(self.input_data)

    def handle_no_transition(self, state):
        """ This function handles the case that there is no transition for a specific outcome of a substate. It waits on a
        condition variable to a new transition that will be connected by the programmer or GUI-user.

        :param state: The substate to find a transition for
        :return: The transition for the target state.
        :raises exceptions.RuntimeError: if the execution engine is stopped
                                        (this will be caught at the end of the run method)
        """
        transition = None
        while not transition:

            # (child) state preempted or aborted
            if self.preempted or state.final_outcome.outcome_id in [-2, -1]:
                if self.concurrency_queue:
                    self.concurrency_queue.put(self.state_id)
                self.state_execution_status = StateExecutionState.WAIT_FOR_NEXT_STATE

                if state.final_outcome.outcome_id == -1:
                    self.final_outcome = Outcome(-1, "aborted")
                else:
                    self.final_outcome = Outcome(-2, "preempted")

                logger.debug("{0} of {1} not connected, using default transition to parental {2}".format(
                    state.final_outcome, state, self.final_outcome))
                return None

            # depending on the execution mode pause execution
            execution_signal = state_machine_execution_engine.handle_execution_mode(self)
            if execution_signal is StateMachineExecutionStatus.STOPPED:
                # this will be caught at the end of the run method
                raise RuntimeError("state stopped")

            # wait until the user connects the outcome of the state with a transition
            logger.warn("Waiting for new transition at {1} of {0} ".format(state, state.final_outcome))
            self._transitions_cv.acquire()
            self._transitions_cv.wait(3.0)
            self._transitions_cv.release()

            transition = self.get_transition_for_outcome(state, state.final_outcome)

        return transition

    def handle_no_start_state(self):
        """Handles the situation, when no start state exists during execution

        The method waits, until a transition is created. It then checks again for an existing start state and waits
        again, if this is not the case. It returns the None state if the the state machine was stopped.
        """
        start_state = self.get_start_state(set_final_outcome=True)
        while not start_state:
            # depending on the execution mode pause execution
            execution_signal = state_machine_execution_engine.handle_execution_mode(self)
            if execution_signal is StateMachineExecutionStatus.STOPPED:
                # this will be caught at the end of the run method
                return None

            self._transitions_cv.acquire()
            self._transitions_cv.wait(3.0)
            self._transitions_cv.release()
            start_state = self.get_start_state(set_final_outcome=True)
        return start_state

    # ---------------------------------------------------------------------------------------------
    # -------------------------------------- state functions --------------------------------------
    # ---------------------------------------------------------------------------------------------

    @Observable.observed
    def group_states(self, state_ids, scoped_variables=None):
        """ Group states and scoped variables into a new hierarchy state and remain internal connections.
            Interconnecting transitions and data flows to parent and other child states are removed, at the moment.

        :param state_ids: state_id's of all states that are to be grouped.
        :param scoped_variables: data_port_id's of all scoped variables that are to be grouped, too.
        :return:
        """
        # TODO remain all related linkage by adding outcomes and input output port to new hierarchy state
        # TODO remember changed state or state element ids and provide them for the model functionalities

        assert all([state_id in [state_id for state in self.states] for state_id in state_ids])
        if scoped_variables is None:
            scoped_variables = []
        assert all([p_id in self.scoped_variables.keys() for p_id in scoped_variables])

        [related_transitions, related_data_flows] = self.related_linkage_states_and_scoped_variables(state_ids,
                                                                                                     scoped_variables)

        # all states
        states_to_group = {state_id: self.states[state_id] for state_id in state_ids}
        # all internal scoped variables
        scoped_variables_to_group = {dp_id: self.scoped_variables[dp_id] for dp_id in scoped_variables}
        # all internal transitions
        transitions_internal = {t.transition_id: t for t in related_transitions['enclosed']}
        # all internal data flows
        data_flows_internal = {df.data_flow_id: df for df in related_data_flows['enclosed']}

        # input data ports from ingoing data flows
        # internal data flows from ingoing data flows
        # external data flows from ingoing data flows
        # output data ports from outgoing data flows
        # internal data flows from outgoing data flows
        # external data flows from outgoing data flows

        [self.remove_state(state_id, recursive_deletion=False, destruct=False) for state_id in state_ids]
        [self.remove_scoped_variable(sv_id) for sv_id in scoped_variables]
        # TODO if the version is final create the ingoing and outgoing internal linkage before and hand it while state creation
        from rafcon.statemachine.states.hierarchy_state import HierarchyState
        s = HierarchyState(states=states_to_group, transitions=transitions_internal, data_flows=data_flows_internal,
                           scoped_variables=scoped_variables_to_group, state_id=self.state_id)
        state_id = self.add_state(s)

        def find_logical_destinations_of_transitions(transitions):
            destinations = {}
            for t in transitions:
                if (t.to_state, t.to_outcome) in destinations:
                    destinations[(t.to_state, t.to_outcome)].append(t)
                else:
                    destinations[(t.to_state, t.to_outcome)] = [t]
            return destinations

        # transition from ingoing transition
        ingoing_logical_destinations = find_logical_destinations_of_transitions(related_transitions['ingoing'])
        if len(ingoing_logical_destinations) > 1:
            logger.warning("There is only one ingoing transition on a state possible. \n"
                           "The following transitions are removed by 'group_states': \n{}"
                           "".format('\n'.join([str(destination) for destination in ingoing_logical_destinations.items()[1:]])))
        ingoing_transitions = None
        if len(ingoing_logical_destinations) > 0:
            ingoing_transitions = ingoing_logical_destinations.items()[0][1]
        # transitions from outgoing transitions
        transitions_outgoing = {t.transition_id: t for t in related_transitions['outgoing']}
        outgoing_logical_destinations = find_logical_destinations_of_transitions(related_transitions['outgoing'])

        if ingoing_transitions:
            t = ingoing_transitions[0]
            s.add_transition(None, None, t.to_state, t.to_outcome)
            for t in ingoing_transitions:
                self.add_transition(t.from_state, t.from_outcome, s.state_id, None)

        def create_name(name_str, dict_name_to_ids):
            number_str = ""
            number_of_str = 0
            # logger.info("new outcome {}".format((t.to_state, t.to_outcome)))
            while name_str + number_str in dict_name_to_ids.keys():
                number_str = "_{}".format(number_of_str)
                number_of_str += 1
                name_str += number_str
            return name

        # outcomes from outgoing transitions
        outcomes_outgoing_transitions = {}
        new_outcome_ids = {}
        state_outcomes_by_name = {oc.name: oc_id for oc_id, oc in s.outcomes.iteritems()}
        for goal, transitions in outgoing_logical_destinations.iteritems():
            t = transitions[0]
            name = s.states[t.from_state].outcomes[t.from_outcome].name
            # print (t.to_state, t.to_outcome)
            # print outcomes_outgoing_transitions
            if goal in outcomes_outgoing_transitions:
                # logger.info("old outcome {}".format((t.to_state, t.to_outcome)))
                name = outcomes_outgoing_transitions[goal]
            else:
                name = create_name(name, new_outcome_ids)
                outcomes_outgoing_transitions[goal] = name
            # print outcomes_outgoing_transitions, "\n", new_outcome_ids
            if name not in new_outcome_ids:
                if name in state_outcomes_by_name:
                    new_outcome_ids[name] = state_outcomes_by_name[name]
                    # logger.info("old outcome_id {0}\n{1}".format(state_outcomes_by_name[name], new_outcome_ids))
                else:
                    new_outcome_ids[name] = s.add_outcome(name=name)
                    # logger.info("new outcome_id {0}\n{1}".format(new_outcome_ids[name], new_outcome_ids))
            # else:
            #     logger.info("name {0} in {1} -> {2}".format(name, new_outcome_ids, outcomes_outgoing_transitions))
        # external outgoing transitions
        for goal, name in outcomes_outgoing_transitions.iteritems():
            try:
                self.add_transition(s.state_id, new_outcome_ids[name], goal[0], goal[1])
            except Exception:
                logger.warning("\n".join(str(t) for t in self.transitions.values()))
                logger.warning("seems to exist {0}".format((s.state_id, new_outcome_ids[name], goal[0], goal[1])))
        # internal outgoing transitions
        for t_id, t in transitions_outgoing.iteritems():
            name = outcomes_outgoing_transitions[(t.to_state, t.to_outcome)]
            s.add_transition(t.from_state, t.from_outcome, s.state_id, new_outcome_ids[name])

        def assign_ingoing_outgoing(df, going_data_linkage_for_port):
            if (df.to_state, df.to_key) in ingoing_data_linkage_for_port['to']:
                ingoing_data_linkage_for_port['to'][(df.to_state, df.to_key)]['external'].append(df)
            else:
                ingoing_data_linkage_for_port['to'][(df.to_state, df.to_key)] = {'external': [df], 'internal': [df]}
            if (df.from_state, df.from_key) in ingoing_data_linkage_for_port['from']:
                ingoing_data_linkage_for_port['from'][(df.from_state, df.from_key)]['internal'].append(df)
            else:
                ingoing_data_linkage_for_port['from'][(df.from_state, df.from_key)] = {'external': [df], 'internal': [df]}
        # outgoing data linkage to rebuild
        outgoing_data_linkage_for_port = {'from': {}, 'to': {}}
        for df in related_data_flows['outgoing']:
            assign_ingoing_outgoing(df, outgoing_data_linkage_for_port)

        # prior from-linkage-merge for outgoing over to-linkage-merge -> less outgoing data flows
        for data_port_linkage in outgoing_data_linkage_for_port:
            pass
            # output data ports from outgoing data flows

            # internal data flows from outgoing data flows
            # external data flows from outgoing data flows

        # ingoing data linkage to rebuild
        input_port_names = []
        ingoing_data_linkage_for_port = {'from': {}, 'to': {}}
        for df in related_data_flows['ingoing']:
            assign_ingoing_outgoing(df, ingoing_data_linkage_for_port)

        # prior to-linkage-merge for ingoing over from-linkage-merge -> less ingoing data flows
        avoided_in_from = []
        in_port_names_ids = {}
        def create_in_port_for(state_id_of_port, key_of_port, parent_of_state, state_to_create_port_in, port_names_ids):
            port = parent_of_state.states[state_id_of_port].get_data_port_by_id[key_of_port]
            create_name(port.name, in_port_names_ids)

        # too complex ??? start with multi connections to, then do multi connections from and final left over single connections
        # to_ip_name_ids
        for goal, data_port_linkage in ingoing_data_linkage_for_port['to'].iteritems():
            port_id = None
            if len(data_port_linkage['external']) > 1:
                for df in data_port_linkage['external'].itervalues():
                    if port_id is None:
                        port_id = create_in_port_for(df.from_state, df.from_key, self, s, in_port_names_ids)

                    avoided_in_from.append(df)



        to_be_deleted = []
        for goal, data_port_linkage in ingoing_data_linkage_for_port['from'].iteritems():
            data_port_linkage['external'] = filter(lambda df: df not in avoided_in_from, data_port_linkage['external'])
            data_port_linkage['internal'] = filter(lambda df: df not in avoided_in_from, data_port_linkage['internal'])
            if data_port_linkage['internal'] and data_port_linkage['external']:
                pass
            else:
                to_be_deleted.append(goal)
            # input data ports from ingoing data flows
            # internal data flows from ingoing data flows
            # external data flows from ingoing data flows
        for goal, data_port_linkage in ingoing_data_linkage_for_port['to'].iteritems():
            pass

        return state_id

    @Observable.observed
    def ungroup_state(self, state_id):
        """ Ungroup state with state id state_id into its parent and remain internal linkage in parent.
            Interconnecting transitions and data flows to parent and other child states are preserved except:
            - a transition that is going from income to outcome directly and
            - a data-flow that is linking input and output directly.

        :param state_id: State that is to be ungrouped.
        :return:
        """
        state = self.states[state_id]
        [related_transitions, related_data_flows] = self.related_linkage_state(state_id)

        # ingoing logical linkage to rebuild -> related_transitions['external']['ingoing']
        # outgoing logical linkage to rebuild -> related_transitions['external']['outgoing']
        # ingoing data linkage to rebuild
        ingoing_data_linkage_for_port = {}
        for df in related_data_flows['internal']['ingoing']:
            if (df.from_state, df.from_key) in ingoing_data_linkage_for_port:
                ingoing_data_linkage_for_port[(df.from_state, df.from_key)]['internal'].append(df)
            else:
                ingoing_data_linkage_for_port[(df.from_state, df.from_key)] = {'external': [], 'internal': [df]}
            if not ingoing_data_linkage_for_port[(df.from_state, df.from_key)]['external']:
                for ext_df in self.data_flows.itervalues():
                    if (ext_df.to_state, ext_df.to_key) == (df.from_state, df.from_key):
                        ingoing_data_linkage_for_port[(df.from_state, df.from_key)]['external'].append(ext_df)
        # outgoing data linkage to rebuild
        outgoing_data_linkage_for_port = {}
        for df in related_data_flows['internal']['outgoing']:
            if (df.to_state, df.to_key) in outgoing_data_linkage_for_port:
                outgoing_data_linkage_for_port[(df.to_state, df.to_key)]['internal'].append(df)
            else:
                outgoing_data_linkage_for_port[(df.to_state, df.to_key)] = {'external': [], 'internal': [df]}
            if not outgoing_data_linkage_for_port[(df.to_state, df.to_key)]['external']:
                for ext_df in self.data_flows.itervalues():
                    if (ext_df.from_state, ext_df.from_key) == (df.to_state, df.to_key):
                        outgoing_data_linkage_for_port[(df.to_state, df.to_key)]['external'].append(ext_df)

        child_states = [state.states[child_state_id] for child_state_id in state.states]
        child_scoped_variables = [sv for sv_id, sv in state.scoped_variables.iteritems()]
        self.remove_state(state_id, recursive_deletion=False)
        state_id_dict = {}
        sv_id_dict = {}
        for state in child_states:
            new_state_id = self.add_state(state)
            state_id_dict[state.state_id] = new_state_id
        for sv in child_scoped_variables:
            new_sv_id = self.add_scoped_variable(sv.name, sv.data_type, sv.default_value, sv.data_port_id)
            sv_id_dict[sv.data_port_id] = new_sv_id

        # re-create transitions
        for t in related_transitions['internal']['enclosed']:
            self.add_transition(state_id_dict[t.from_state], t.from_outcome, state_id_dict[t.to_state], t.to_outcome)
        assert len(related_transitions['internal']['ingoing']) <= 1
        if related_transitions['internal']['ingoing']:
            ingoing_t = related_transitions['internal']['ingoing'][0]
            for t in related_transitions['external']['ingoing']:
                self.add_transition(t.from_state, t.from_outcome, state_id_dict[ingoing_t.to_state], ingoing_t.to_outcome)
        for ext_t in related_transitions['external']['outgoing']:
            for t in related_transitions['internal']['outgoing']:
                if (t.to_state, t.to_outcome) == (ext_t.from_state, ext_t.from_outcome):
                    self.add_transition(state_id_dict[t.from_state], t.from_outcome, ext_t.to_state, ext_t.to_outcome)

        # re-create data flow linkage
        for df in related_data_flows['internal']['enclosed']:
            self.add_data_flow(state_id_dict[df.from_state], df.from_key, state_id_dict[df.to_state], df.to_key)
        for data_port_linkage in ingoing_data_linkage_for_port.itervalues():
            for ext_df in data_port_linkage['external']:
                for df in data_port_linkage['internal']:
                    # print "ingoing: ", ext_df, df
                    if df.to_state not in state_id_dict and df.to_state == state_id:
                        self.add_data_flow(ext_df.from_state, ext_df.from_key, self.state_id, sv_id_dict[df.to_key])
                    else:
                        self.add_data_flow(ext_df.from_state, ext_df.from_key, state_id_dict[df.to_state], df.to_key)
        for data_port_linkage in outgoing_data_linkage_for_port.itervalues():
            for ext_df in data_port_linkage['external']:
                for df in data_port_linkage['internal']:
                    # print "outgoing: ", ext_df, df
                    if df.from_state not in state_id_dict and df.from_state == state_id:
                        self.add_data_flow(self.state_id, sv_id_dict[df.from_key], ext_df.to_state, ext_df.to_key)
                    else:
                        self.add_data_flow(state_id_dict[df.from_state], df.from_key, ext_df.to_state, ext_df.to_key)

    @Observable.observed
    def add_state(self, state, storage_load=False):
        """Adds a state to the container state.

        :param state: the state that is going to be added
        :param storage_load: True if the state was directly loaded from filesystem
        :return: the state_id of the new state
        :raises exceptions.AttributeError: if state.state_id already exist
        """
        assert isinstance(state, State)
        # logger.info("add state {}".format(state))

        # handle the case that the child state id is the same as the container state id
        while state.state_id == self.state_id:
            state.change_state_id()

        # TODO: add validity checks for states and then remove this check => to discuss
        if state.state_id in self._states.iterkeys():
            raise AttributeError("State id %s already exists in the container state", state.state_id)
        else:
            state.parent = self
            self._states[state.state_id] = state

        if not storage_load:
            # unmark path for removal: this is needed when a state with the same id is removed and added again in this state
            if self.get_sm_for_state():
                own_sm_id = self.get_sm_for_state().state_machine_id
                if own_sm_id is not None:
                    storage.unmark_path_for_removal_for_sm_id(own_sm_id, state.get_file_system_path())

        return state.state_id

    @Observable.observed
    def remove_state(self, state_id, recursive_deletion=True, force=True, destruct=True):
        """Remove a state from the container state.

        :param state_id: the id of the state to remove
        :param recursive_deletion: a flag to indicate a recursive deletion of all substates
        :raises exceptions.AttributeError: if state.state_id does not
        """
        if state_id not in self.states:
            raise AttributeError("State_id %s does not exist" % state_id)

        if state_id == self.start_state_id:
            self.set_start_state(None)

        # remove script folder
        if self.get_sm_for_state():
            own_sm_id = self.get_sm_for_state().state_machine_id
            if own_sm_id is None:
                logger.warn("Something is going wrong during state removal. State does not belong to "
                            "a state machine!")
            else:
                storage.mark_path_for_removal_for_sm_id(own_sm_id, self.states[state_id].get_file_system_path())

        # first delete all transitions and data_flows, which are connected to the state to be deleted
        keys_to_delete = []
        for key, transition in self.transitions.iteritems():
            if transition.from_state == state_id or transition.to_state == state_id:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            self.remove_transition(key, True)

        keys_to_delete = []
        for key, data_flow in self.data_flows.iteritems():
            if data_flow.from_state == state_id or data_flow.to_state == state_id:
                keys_to_delete.append(key)
        for key in keys_to_delete:
            self.remove_data_flow(key)

        if recursive_deletion:
            # Recursively delete all transitions, data flows and states within the state to be deleted
            if isinstance(self.states[state_id], ContainerState):
                for child_state_id in self.states[state_id].states.keys():
                    self.states[state_id].remove_state(child_state_id, force=True)
                for transition_id in self.states[state_id].transitions.keys():
                    self.states[state_id].remove_transition(transition_id)
                for data_flow_id in self.states[state_id].data_flows.keys():
                    self.states[state_id].remove_data_flow(data_flow_id)

        if destruct:
            self.states[state_id].destruct()
        # final delete the state it self
        del self.states[state_id]

    def related_linkage_state(self, state_id):

        related_transitions = {'external': {'ingoing': [], 'outgoing': []},
                               'internal': {'enclosed': [], 'ingoing': [], 'outgoing': []}}
        related_data_flows = {'external': {'ingoing': [], 'outgoing': []},
                              'internal': {'enclosed': [], 'ingoing': [], 'outgoing': []}}
        # ingoing logical linkage to rebuild
        related_transitions['external']['ingoing'] = [t for t in self.transitions.itervalues() if t.to_state == state_id]
        # outgoing logical linkage to rebuild
        related_transitions['external']['outgoing'] = [t for t in self.transitions.itervalues() if t.from_state == state_id]
        # ingoing data linkage to rebuild
        related_data_flows['external']['ingoing'] = [df for df in self.data_flows.itervalues() if df.to_state == state_id]
        # outgoing outgoing linkage to rebuild
        related_data_flows['external']['outgoing'] = [df for df in self.data_flows.itervalues() if df.from_state == state_id]

        state = self.states[state_id]
        if not isinstance(state, ContainerState):
            return related_transitions, related_data_flows

        for t_id, t in state.transitions.iteritems():
            # check if internal of new hierarchy state
            if t.from_state in state.states and t.to_state in state.states:
                related_transitions['internal']['enclosed'].append(t)
            elif t.to_state in state.states:
                related_transitions['internal']['ingoing'].append(t)
            elif t.from_state in state.states:
                related_transitions['internal']['outgoing'].append(t)
            else:
                print "no relation"
                # raise AttributeError("All transition have to be ingoing, outgoing or internal.")

        for df_id, df in state.data_flows.iteritems():
            # check if internal of hierarchy state
            if df.from_state in state.states and df.to_state in state.states or \
                    df.from_state in state.states and self.state_id == df.to_state and df.to_key in state.scoped_variables or \
                    self.state_id == df.from_state and df.from_key in state.scoped_variables and df.to_state in state.states:
                related_data_flows['internal']['enclosed'].append(df)
            elif df.to_state in state.states or \
                    state.state_id == df.to_state and df.to_key in state.scoped_variables or \
                    df.to_state == df.from_state and df.from_key in state.input_data_ports:
                related_data_flows['internal']['ingoing'].append(df)
            elif df.from_state in self.states[state_id].states or \
                    state.state_id == df.from_state and df.from_key in state.scoped_variables or \
                    df.to_state == df.from_state and df.to_key in state.output_data_ports:
                related_data_flows['internal']['outgoing'].append(df)
            else:
                print "no relation"
                # raise AttributeError("All data flow have to be ingoing, outgoing or internal.")

        return related_transitions, related_data_flows

    def related_linkage_states_and_scoped_variables(self, state_ids, scoped_variables):

        # find all related transitions
        related_transitions = {'enclosed': [], 'ingoing': [], 'outgoing': []}
        for t in self.transitions.itervalues():
            # check if internal of new hierarchy state
            if t.from_state in state_ids and t.to_state in state_ids:
                related_transitions['enclosed'].append(t)
            elif t.to_state in state_ids:
                related_transitions['ingoing'].append(t)
            elif t.from_state in state_ids:
                related_transitions['outgoing'].append(t)

        # find all related data flows
        related_data_flows = {'enclosed': [], 'ingoing': [], 'outgoing': []}
        for df in self.data_flows.itervalues():
            # check if internal of new hierarchy state
            if df.from_state in state_ids and df.to_state in state_ids or \
                    df.from_state in state_ids and self.state_id == df.to_state and df.to_key in scoped_variables or \
                    self.state_id == df.from_state and df.from_key in scoped_variables and df.to_state in state_ids:
                related_data_flows['enclosed'].append(df)
            elif df.to_state in state_ids or \
                    self.state_id == df.to_state and df.to_key in scoped_variables:
                related_data_flows['ingoing'].append(df)
            elif df.from_state in state_ids or \
                    self.state_id == df.from_state and df.from_key in scoped_variables:
                related_data_flows['outgoing'].append(df)

        return related_transitions, related_data_flows

    @Observable.observed
    def substitute_state(self, state_id, state):

        if state_id not in self.states:
            raise ValueError("The state_id {0} to be substitute has to be in the states list of "
                             "respective parent state {1}.".format(state_id, self.get_path()))

        [related_transitions, related_data_flows] = self.related_linkage_state(state_id)

        old_outcome_names = {oc_id: oc.name for oc_id, oc in self.states[state_id].outcomes.iteritems()}
        old_input_data_ports = copy(self.states[state_id].input_data_ports)
        old_output_data_ports = copy(self.states[state_id].output_data_ports)
        old_state_was_library = False
        if isinstance(self.states[state_id], LibraryState):
            old_input_data_port_runtime_values = self.states[state_id].input_data_port_runtime_values
            old_output_data_port_runtime_values = self.states[state_id].output_data_port_runtime_values
            old_use_runtime_value_input_data_ports = self.states[state_id].use_runtime_value_input_data_ports
            old_use_runtime_value_output_data_ports = self.states[state_id].use_runtime_value_output_data_ports
            old_state_was_library = True

        self.remove_state(state_id)
        old_state_id = state_id
        state_id = self.add_state(state)

        act_outcome_ids_by_name = {oc.name: oc_id for oc_id, oc in state.outcomes.iteritems()}
        act_input_data_port_by_name = {ip.name: ip for ip in state.input_data_ports.itervalues()}
        act_output_data_port_by_name = {op.name: op for op in state.output_data_ports.itervalues()}

        for t in related_transitions['external']['ingoing']:
            self.add_transition(t.from_state, t.from_outcome, state_id, t.to_outcome, t.transition_id)

        for t in related_transitions['external']['outgoing']:
            from_outcome = act_outcome_ids_by_name.get(old_outcome_names[t.from_outcome], None)
            if from_outcome is not None:
                self.add_transition(state_id, from_outcome, t.to_state, t.to_outcome, t.transition_id)

        for old_ip in old_input_data_ports.itervalues():
            ip = act_input_data_port_by_name.get(old_input_data_ports[old_ip.data_port_id].name, None)
            if ip is not None and ip.data_type == old_input_data_ports[old_ip.data_port_id].data_type:
                if isinstance(state, LibraryState) and old_state_was_library:
                    state.input_data_port_runtime_values[ip.data_port_id] = old_input_data_port_runtime_values[old_ip.data_port_id]
                    state.use_runtime_value_input_data_ports[ip.data_port_id] = old_use_runtime_value_input_data_ports[old_ip.data_port_id]
                elif not isinstance(state, LibraryState):
                    ip.default_value = old_input_data_ports[old_ip.data_port_id].default_value
        for df in related_data_flows['external']['ingoing']:
            ip = act_input_data_port_by_name.get(old_input_data_ports[df.to_key].name, None)
            if ip is not None and ip.data_type == old_input_data_ports[df.to_key].data_type:
                self.add_data_flow(df.from_state, df.from_key, state_id, ip.data_port_id, df.data_flow_id)

        for old_op in old_output_data_ports.itervalues():
            op = act_output_data_port_by_name.get(old_output_data_ports[old_op.data_port_id], None).name
            if op is not None and op.data_type == old_output_data_ports[old_op.data_port_id].data_type:
                if isinstance(state, LibraryState) and old_state_was_library:
                    state.output_data_port_runtime_values[op.data_port_id] = old_output_data_port_runtime_values[old_op.data_port_id]
                    state.use_runtime_value_output_data_ports[op.data_port_id] = old_use_runtime_value_output_data_ports[old_op.data_port_id]
                elif not isinstance(state, LibraryState):
                    op.default_value = old_output_data_ports[old_op.data_port_id].default_value
        for df in related_data_flows['external']['outgoing']:
            op = act_output_data_port_by_name.get(old_output_data_ports[df.from_key].name, None)
            if op is not None and op.data_type == old_output_data_ports[df.from_key].data_type:
                self.add_data_flow(state_id, op.data_port_id, df.to_state, df.to_key, df.data_flow_id)

        return state_id

    @Observable.observed
    def change_state_type(self, state, new_state_class):
        """ Changes the type of the state to another type

        :param state: the state to be changed
        :param new_state_class: the new type of the state
        :return: the new state having the new state type
        :rtype: :py:class:`rafcon.statemachine.states.state.State`
        :raises exceptions.ValueError: if the state does not exist in the container state
        """
        from rafcon.mvc.state_machine_helper import create_new_state_from_state_with_type

        state_id = state.state_id

        if state_id not in self.states:
            raise ValueError("State '{0}' with id '{1}' does not exist".format(state.name, state_id))

        new_state = create_new_state_from_state_with_type(state, new_state_class)
        new_state.parent = self

        assert new_state.state_id == state_id

        self.states[state_id] = new_state

        return new_state

    # @Observable.observed
    def set_start_state(self, state):
        """Sets the start state of a container state

        :param state: The state_id of a state or a direct reference ot he state (that was already added
                    to the container) that will be the start state of this container state.

        """
        if state is None:
            self.start_state_id = None
        elif isinstance(state, State):
            self.start_state_id = state.state_id
        else:
            self.start_state_id = state

    def get_start_state(self, set_final_outcome=False):
        """Get the start state of the container state

        :param set_final_outcome: if the final_outcome of the state should be set if the income directly connects to
                                    an outcome
        :return: the start state
        """

        # overwrite the start state in the case that a specific start state is specific e.g. by start_from_state
        if self.get_path() in state_machine_execution_engine.start_state_paths:
            for state_id, state in self.states.iteritems():
                if state.get_path() in state_machine_execution_engine.start_state_paths:
                    state_machine_execution_engine.start_state_paths.remove(self.get_path())
                    return state

        if self.start_state_id is None:
            return None

        # It is possible to connect the income directly with an outcome
        if self.start_state_id == self.state_id:
            if set_final_outcome:
                for transition_id in self.transitions:
                    # the transition of which the from state is None is the transition that directly connects the income
                    if self.transitions[transition_id].from_state is None:
                        to_outcome_id = self.transitions[transition_id].to_outcome
                        self.final_outcome = self.outcomes[to_outcome_id]
                        break
            return self

        return self.states[self.start_state_id]

    # ---------------------------------------------------------------------------------------------
    # ---------------------------------- transition functions -------------------------------------
    # ---------------------------------------------------------------------------------------------

    def check_transition_id(self, transition_id):
        """ Check the transition id and calculate a new one if its None

        :param transition_id: The transition-id to check
        :return: The new transition id
        :raises exceptions.AttributeError: if transition.transition_id already exists
        """
        if transition_id is not None:
            if transition_id in self._transitions.iterkeys():
                raise AttributeError("The transition id %s already exists. Cannot add transition!", transition_id)
        else:
            transition_id = generate_transition_id()
            while transition_id in self._transitions.iterkeys():
                transition_id = generate_transition_id()
        return transition_id

    def basic_transition_checks(self, from_state_id, from_outcome, to_state_id, to_outcome, transition_id):
        pass

    def check_if_outcome_already_connected(self, from_state_id, from_outcome):
        """ check if outcome of from state is not already connected

        :param from_state_id: The source state of the transition
        :param from_outcome: The outcome of the source state to connect the transition to
        :raises exceptions.AttributeError: if the outcome of the state with the state_id==from_state_id
                                            is already connected
        """
        for trans_key, transition in self.transitions.iteritems():
            if transition.from_state == from_state_id:
                if transition.from_outcome == from_outcome:
                    raise AttributeError("Outcome %s of state %s is already connected" %
                                         (str(from_outcome), str(from_state_id)))

    def create_transition(self, from_state_id, from_outcome, to_state_id, to_outcome, transition_id):
        """ Creates a new transition.

        Lookout: Check the parameters first before creating a new transition

        :param from_state_id: The source state of the transition
        :param from_outcome: The outcome of the source state to connect the transition to
        :param to_state_id: The target state of the transition
        :param to_outcome: The target outcome of a container state
        :param transition_id: An optional transition id for the new transition
        :raises exceptions.AttributeError: if the from or to state is incorrect
        :return: the id of the new transition
        """

        # get correct states
        if from_state_id is not None:
            if from_state_id == self.state_id:
                from_state = self
            else:
                from_state = self.states[from_state_id]

        # finally add transition
        if from_outcome is not None:
            if from_outcome in from_state.outcomes:
                if to_outcome is not None:
                    if to_outcome in self.outcomes:  # if to_state is None then the to_outcome must be an outcome of self
                        self.transitions[transition_id] = \
                            Transition(from_state_id, from_outcome, to_state_id, to_outcome, transition_id, self)
                    else:
                        raise AttributeError("to_state does not have outcome %s", to_outcome)
                else:  # to outcome is None but to_state is not None, so the transition is valid
                    self.transitions[transition_id] = \
                        Transition(from_state_id, from_outcome, to_state_id, to_outcome, transition_id, self)
            else:
                raise AttributeError("from_state does not have outcome %s", from_state)
        else:
            self.transitions[transition_id] = \
                Transition(None, None, to_state_id, to_outcome, transition_id, self)

        # notify all states waiting for transition to be connected
        self._transitions_cv.acquire()
        self._transitions_cv.notify_all()
        self._transitions_cv.release()

        return transition_id

    @Observable.observed
    def add_transition(self, from_state_id, from_outcome, to_state_id, to_outcome, transition_id=None):
        """Adds a transition to the container state

        Note: Either the toState or the toOutcome needs to be "None"

        :param from_state_id: The source state of the transition
        :param from_outcome: The outcome id of the source state to connect the transition to
        :param to_state_id: The target state of the transition
        :param to_outcome: The target outcome id of a container state
        :param transition_id: An optional transition id for the new transition
        """

        transition_id = self.check_transition_id(transition_id)

        new_transition = Transition(from_state_id, from_outcome, to_state_id, to_outcome, transition_id, self)
        self.transitions[transition_id] = new_transition

        # notify all states waiting for transition to be connected
        self._transitions_cv.acquire()
        self._transitions_cv.notify_all()
        self._transitions_cv.release()
        # self.create_transition(from_state_id, from_outcome, to_state_id, to_outcome, transition_id)
        return transition_id

    def get_transition_for_outcome(self, state, outcome):
        """Determines the next transition of a state.

        :param state: The state for which the transition is determined
        :param outcome: The outcome of the state, that is given in the first parameter
        :return: the transition specified by the the state and the outcome
        :raises exceptions.TypeError: if the types of the passed parameters are incorrect
        """
        if not isinstance(state, State):
            raise TypeError("state must be of type State")
        if not isinstance(outcome, Outcome):
            raise TypeError("outcome must be of type Outcome")
        result_transition = None
        for key, transition in self.transitions.iteritems():
            if transition.from_state == state.state_id and transition.from_outcome == outcome.outcome_id:
                result_transition = transition
        return result_transition

    @Observable.observed
    def remove_transition(self, transition_id, force=False):
        """Removes a transition from the container state

        :param transition_id: the id of the transition to remove
        :raises exceptions.AttributeError: if the transition_id is already used
        """
        if transition_id == -1 or transition_id == -2:
            raise AttributeError("The transition_id must not be -1 (Aborted) or -2 (Preempted)")
        if transition_id not in self._transitions:
            raise AttributeError("The transition_id %s does not exist" % str(transition_id))
        del self._transitions[transition_id]

    def remove_outcome_hook(self, outcome_id):
        """Removes internal transition going to the outcome
        """
        for transition_id in self.transitions.keys():
            transition = self.transitions[transition_id]
            if transition.to_outcome == outcome_id and transition.to_state == self.state_id:
                self.remove_transition(transition_id)

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------- data-flow functions -------------------------------------
    # ---------------------------------------------------------------------------------------------
    # TODO input, output oder scope nicht auf sich selbst
    # TODO scope nicht auf andere scope
    # TODO output-in, input-in nur ein data flow
    # TODO data flows mit gleichen Attributen nur einmal

    @Observable.observed
    #Primary key is data_flow_id.
    def add_data_flow(self, from_state_id, from_data_port_id, to_state_id, to_data_port_id, data_flow_id=None):
        """Adds a data_flow to the container state

        :param from_state_id: The id source state of the data_flow
        :param from_data_port_id: The output_key of the source state
        :param to_state_id: The id target state of the data_flow
        :param to_data_port_id: The input_key of the target state
        :param data_flow_id: an optional id for the data flow
        """
        if data_flow_id is None:
            data_flow_id = generate_data_flow_id()
            while data_flow_id in self._data_flows.iterkeys():
                data_flow_id = generate_data_flow_id()

        self.data_flows[data_flow_id] = DataFlow(from_state_id, from_data_port_id, to_state_id, to_data_port_id,
                                                 data_flow_id, self)
        return data_flow_id

    @Observable.observed
    def remove_data_flow(self, data_flow_id):
        """ Removes a data flow from the container state

        :param int data_flow_id: the id of the data_flow to remove
        :raises exceptions.AttributeError: if the data_flow_id does not exist
        """
        if data_flow_id not in self.data_flows:
            raise AttributeError("The data_flow_id %s does not exist" % str(data_flow_id))
        del self.data_flows[data_flow_id]

    def remove_data_flows_with_data_port_id(self, data_port_id):
        """Remove an data ports whose from_key or to_key equals the passed data_port_id

        :param int data_port_id: the id of a data_port of which all data_flows should be removed, the id can be a input or
                            output data port id

        """
        # delete all data flows in parent related to data_port_id and self.state_id
        if not self.is_root_state:
            data_flow_ids_to_remove = []
            for data_flow_id, data_flow in self.parent.data_flows.iteritems():
                if data_flow.from_state == self.state_id and data_flow.from_key == data_port_id or \
                        data_flow.to_state == self.state_id and data_flow.to_key == data_port_id:
                    data_flow_ids_to_remove.append(data_flow_id)

            for data_flow_id in data_flow_ids_to_remove:
                self.parent.remove_data_flow(data_flow_id)

        # delete all data flows in self related to data_port_id and self.state_id
        data_flow_ids_to_remove = []
        for data_flow_id, data_flow in self.data_flows.iteritems():
            if data_flow.from_state == self.state_id and data_flow.from_key == data_port_id or \
                    data_flow.to_state == self.state_id and data_flow.to_key == data_port_id:
                data_flow_ids_to_remove.append(data_flow_id)

        for data_flow_id in data_flow_ids_to_remove:
            self.remove_data_flow(data_flow_id)

    # ---------------------------------------------------------------------------------------------
    # ---------------------------- scoped variables functions --------.----------------------------
    # ---------------------------------------------------------------------------------------------

    def get_scoped_variable_from_name(self, name):
        """ Get the scoped variable for a unique name

        :param name: the unique name of the scoped variable
        :return: the scoped variable specified by the name
        :raises exceptions.AttributeError: if the name is not in the the scoped_variables dictionary
        """
        for scoped_variable_id, scoped_variable in self.scoped_variables.iteritems():
            if scoped_variable.name == name:
                return scoped_variable_id
        raise AttributeError("Name %s is not in scoped_variables dictionary", name)

    @Observable.observed
    def add_scoped_variable(self, name, data_type=None, default_value=None, scoped_variable_id=None):
        """ Adds a scoped variable to the container state

        :param name: The name of the scoped variable
        :param data_type: An optional data type of the scoped variable
        :param default_value: An optional default value of the scoped variable
        :param scoped_variable_id: An optional scoped variable id of the
        :return: the unique id of the added scoped variable
        :raises exceptions.ValueError: if the scoped variable is not valid
        """
        if scoped_variable_id is None:
            # All data port ids have to passed to the id generation as the data port id has to be unique inside a state
            scoped_variable_id = generate_data_port_id(self.get_data_port_ids())
        self._scoped_variables[scoped_variable_id] = ScopedVariable(name, data_type, default_value,
                                                                    scoped_variable_id, self)

        # Check for name uniqueness
        valid, message = self._check_data_port_name(self._scoped_variables[scoped_variable_id])
        if not valid:
            del self._scoped_variables[scoped_variable_id]
            raise ValueError(message)

        return scoped_variable_id

    @Observable.observed
    def remove_scoped_variable(self, scoped_variable_id):
        """Remove a scoped variable from the container state

        :param scoped_variable_id: the id of the scoped variable to remove
        :raises exceptions.AttributeError: if the id of the scoped variable already exists
        """
        if scoped_variable_id not in self._scoped_variables:
            raise AttributeError("A scoped variable with id %s does not exist" % str(scoped_variable_id))

        # delete all data flows connected to scoped_variable
        self.remove_data_flows_with_data_port_id(self._scoped_variables[scoped_variable_id].data_port_id)

        # delete scoped variable
        del self._scoped_variables[scoped_variable_id]

    # ---------------------------------------------------------------------------------------------
    # ---------------------------- scoped variables functions end ---------------------------------
    # ---------------------------------------------------------------------------------------------

    def get_outcome(self, state_id, outcome_id):
        if state_id == self.state_id:
            if outcome_id in self.outcomes:
                return self.outcomes[outcome_id]
        elif state_id in self.states:
            state = self.states[state_id]
            if outcome_id in state.outcomes:
                return state.outcomes[outcome_id]
        return None

    def get_data_port(self, state_id, port_id):
        """Searches for a data port

        The data port specified by the state id and data port id is searched in the state itself and in its children.

        :param str state_id: The id of the state the port is in
        :param int port_id:  The id of the port
        :return: The searched port or None if it is not found
        """
        if state_id == self.state_id:
            return self.get_data_port_by_id(port_id)
        for child_state_id, child_state in self.states.iteritems():
            if state_id != child_state_id:
                continue
            port = child_state.get_data_port_by_id(port_id)
            if port:
                return port
        return None

    def get_data_port_by_id(self, data_port_id):
        """Search for the given data port id in the data ports of the state

        The method tries to find a data port in the input and output data ports as well as in the scoped variables.

        :param data_port_id: the unique id of the data port
        :return: the data port with the searched id or None if not found
        """
        data_port = super(ContainerState, self).get_data_port_by_id(data_port_id)
        if data_port:
            return data_port
        if data_port_id in self.scoped_variables:
            return self.scoped_variables[data_port_id]
        return None

    def get_data_port_ids(self):
        return self._scoped_variables.keys() + self._input_data_ports.keys() + self._output_data_ports.keys()

    # ---------------------------------------------------------------------------------------------
    # ---------------------------------- input data handling --------------------------------------
    # ---------------------------------------------------------------------------------------------

    def get_inputs_for_state(self, state):
        """Retrieves all input data of a state. If several data flows are connected to an input port the
        most current data is used for the specific input port.

        :param state: the state of which the input data is determined
        :return: the input data of the target state
        """
        result_dict = {}

        tmp_dict = self.get_default_input_values_for_state(state)
        result_dict.update(tmp_dict)

        for input_port_key, value in state.input_data_ports.iteritems():
            # for all input keys fetch the correct data_flow connection and read data into the result_dict
            actual_value = None
            actual_value_time = 0
            for data_flow_key, data_flow in self.data_flows.iteritems():

                if data_flow.to_key == input_port_key:
                    if data_flow.to_state == state.state_id:
                        # fetch data from the scoped_data list: the key is the data_port_key + the state_id
                        key = str(data_flow.from_key)+data_flow.from_state
                        if key in self.scoped_data:
                            if actual_value is None or actual_value_time < self.scoped_data[key].timestamp:
                                actual_value = deepcopy(self.scoped_data[key].value)
                                actual_value_time = self.scoped_data[key].timestamp

            if actual_value is not None:
                result_dict[value.name] = actual_value

        return result_dict

    # ---------------------------------------------------------------------------------------------
    # ---------------------------- functions to modify the scoped data ----------------------------
    # ---------------------------------------------------------------------------------------------

    def add_input_data_to_scoped_data(self, dictionary):
        """Add a dictionary to the scoped data

        As the input_data dictionary maps names to values, the functions looks for the proper data_ports keys in the
        input_data_ports dictionary

        :param dictionary: The dictionary that is added to the scoped data
        :param state: The state to which the input_data was passed (should be self in most cases)
        """
        for dict_key, value in dictionary.iteritems():
            for input_data_port_key, data_port in self.input_data_ports.iteritems():
                if dict_key == data_port.name:
                    self.scoped_data[str(input_data_port_key)+self.state_id] = \
                        ScopedData(data_port.name, value, type(value), self.state_id, DataPortType.INPUT)
                    # forward the data to scoped variables
                    for data_flow_key, data_flow in self.data_flows.iteritems():
                        if data_flow.from_key == input_data_port_key and data_flow.from_state == self.state_id:
                            if data_flow.to_state == self.state_id and data_flow.to_key in self.scoped_variables:
                                current_scoped_variable = self.scoped_variables[data_flow.to_key]
                                self.scoped_data[str(data_flow.to_key)+self.state_id] = \
                                    ScopedData(current_scoped_variable.name, value, type(value), self.state_id,
                                               DataPortType.SCOPED)

    def add_state_execution_output_to_scoped_data(self, dictionary, state):
        """Add a state execution output to the scoped data

        :param dictionary: The dictionary that is added to the scoped data
        :param state: The state that finished execution and provide the dictionary
        """
        for output_name, value in dictionary.iteritems():
            for output_data_port_key, data_port in state.output_data_ports.iteritems():
                if output_name == data_port.name:
                    self.scoped_data[str(output_data_port_key)+state.state_id] = \
                        ScopedData(data_port.name, value, type(value), state.state_id, DataPortType.OUTPUT)

    def add_default_values_of_scoped_variables_to_scoped_data(self):
        """Add the scoped variables default values to the scoped_data dictionary

        """
        for key, scoped_var in self.scoped_variables.iteritems():
            self.scoped_data[str(scoped_var.data_port_id)+self.state_id] = \
                ScopedData(scoped_var.name, scoped_var.default_value, scoped_var.data_type, self.state_id,
                           DataPortType.SCOPED)

    def update_scoped_variables_with_output_dictionary(self, dictionary, state):
        """Update the values of the scoped variables with the output dictionary of a specific state.

        :param: the dictionary to update the scoped variables with
        :param: the state the output dictionary belongs to
        """
        for key, value in dictionary.iteritems():
            output_data_port_key = None
            # search for the correct output data port key of the source state
            for o_key, o_port in state.output_data_ports.iteritems():
                if o_port.name == key:
                    output_data_port_key = o_key
                    break
            if output_data_port_key is None:
                if not key == "error":
                    logger.warning("Output variable %s was written during state execution, "
                                   "that has no data port connected to it.", str(key))
            for data_flow_key, data_flow in self.data_flows.iteritems():
                if data_flow.from_key == output_data_port_key and data_flow.from_state == state.state_id:
                    if data_flow.to_state == self.state_id:  # is target of data flow own state id?
                        if data_flow.to_key in self.scoped_variables.iterkeys():  # is target data port scoped?
                            current_scoped_variable = self.scoped_variables[data_flow.to_key]
                            self.scoped_data[str(data_flow.to_key) + self.state_id] = \
                                ScopedData(current_scoped_variable.name, value, type(value), state.state_id,
                                           DataPortType.SCOPED)

    # ---------------------------------------------------------------------------------------------
    # ------------------------ functions to modify the scoped data end ----------------------------
    # ---------------------------------------------------------------------------------------------

    def change_state_id(self, state_id=None):
        """
        Changes the id of the state to a new id. This functions replaces the old state_id with the new state_id in all
        data flows and transitions.

        :param state_id: The new state if of the state
        """
        old_state_id = self.state_id
        super(ContainerState, self).change_state_id(state_id)
        # Use private variables to change ids to prevent validity checks
        # change id in all transitions
        for transition in self.transitions.itervalues():
            if transition.from_state == old_state_id:
                transition._from_state = self.state_id
            if transition.to_state == old_state_id:
                transition._to_state = self.state_id

        # change id in all data_flows
        for data_flow in self.data_flows.itervalues():
            if data_flow.from_state == old_state_id:
                data_flow._from_state = self.state_id
            if data_flow.to_state == old_state_id:
                data_flow._to_state = self.state_id

    def get_state_for_transition(self, transition):
        """Calculate the target state of a transition

        :param transition: The transition of which the target state is determined
        :return: the to-state of the transition
        :raises exceptions.TypeError: if the transition parameter is of wrong type
        """
        if not isinstance(transition, Transition):
            raise TypeError("transition must be of type Transition")
        # the to_state is None when the transition connects an outcome of a child state to the outcome of a parent state
        if transition.to_state == self.state_id or transition.to_state is None:
            return self
        else:
            return self.states[transition.to_state]

    def write_output_data(self):
        """ Write the scoped data to output of the state. Called before exiting the container state.

        """
        for output_name, value in self.output_data.iteritems():
            output_port_id = self.get_io_data_port_id_from_name_and_type(output_name, DataPortType.OUTPUT)
            actual_value = None
            actual_value_time = 0
            for data_flow_id, data_flow in self.data_flows.iteritems():
                if data_flow.to_state == self.state_id:
                    if data_flow.to_key == output_port_id:
                        scoped_data_key = str(data_flow.from_key)+data_flow.from_state
                        if scoped_data_key in self.scoped_data:
                            # if self.scoped_data[scoped_data_key].timestamp > actual_value_time is True
                            # the data of a previous execution of the same state is overwritten
                            if actual_value is None or self.scoped_data[scoped_data_key].timestamp > actual_value_time:
                                actual_value = deepcopy(self.scoped_data[scoped_data_key].value)
                                actual_value_time = self.scoped_data[scoped_data_key].timestamp
                        else:
                            if not self.backward_execution:
                                logger.debug(
                                    "Output data with name {0} of state {1} was not found in the scoped data "
                                    "of state {2}. Thus the state did not write onto this output. "
                                    "This can mean a state machine design error.".format(
                                        str(output_name), str(self.states[data_flow.from_state].get_path()),
                                        self.get_path()))
            if actual_value is not None:
                self.output_data[output_name] = actual_value

    # ---------------------------------------------------------------------------------------------
    # -------------------------------------- check methods ---------------------------------------
    # ---------------------------------------------------------------------------------------------

    def check_child_validity(self, child):
        """Check validity of passed child object

        The method is called by state child objects (transitions, data flows) when these are initialized or changed. The
        method checks the type of the child and then checks its validity in the context of the state.

        :param object child: The child of the state that is to be tested
        :return bool validity, str message: validity is True, when the child is valid, False else. message gives more
            information especially if the child is not valid
        """
        # First let the state do validity checks for outcomes and data ports
        valid, message = super(ContainerState, self).check_child_validity(child)
        if not valid and message != "no valid child type":
            return False, message
        # Continue with checks if previous ones did not fail
        # Check type of child and call appropriate validity test
        if isinstance(child, DataFlow):
            return self._check_data_flow_validity(child)
        if isinstance(child, Transition):
            return self._check_transition_validity(child)
        return valid, message

    def check_data_port_connection(self, check_data_port):
        """Checks the connection validity of a data port

        The method is called by a child state to check the validity of a data port in case it is connected with data
        flows. The data port does not belong to 'self', but to one of self.states.
        If the data port is connected to a data flow, the method checks, whether these connect consistent data types
        of ports.

        :param rafcon.statemachine.data_port.DataPort check_data_port: The port to check
        :return: valid, message
        """
        for data_flow in self.data_flows.itervalues():
            # Check whether the data flow connects the given port
            from_port = self.get_data_port(data_flow.from_state, data_flow.from_key)
            to_port = self.get_data_port(data_flow.to_state, data_flow.to_key)
            if check_data_port is from_port or check_data_port is to_port:
                if not type_inherits_of_type(from_port.data_type, to_port.data_type):
                    return False, "Connection of two non-compatible data types"
        return True, "valid"

    def _check_data_port_id(self, data_port):
        """Checks the validity of a data port id

        Checks whether the id of the given data port is already used by anther data port (input, output, scoped vars)
        within the state.

        :param rafcon.statemachine.data_port.DataPort data_port: The data port to be checked
        :return bool validity, str message: validity is True, when the data port is valid, False else. message gives
            more information especially if the data port is not valid
        """
        # First check inputs and outputs
        valid, message = super(ContainerState, self)._check_data_port_id(data_port)
        if not valid:
            return False, message
        # Container state also has scoped variables
        for scoped_variable_id, scoped_variable in self.scoped_variables.iteritems():
            if data_port.data_port_id == scoped_variable_id and data_port is not scoped_variable:
                return False, "data port id already existing in state"
        return True, message

    def _check_data_port_name(self, data_port):
        """Checks the validity of a data port name

        Checks whether the name of the given data port is already used by anther data port within the state. Names
        must be unique with input data ports, output data ports and scoped variables.

        :param rafcon.statemachine.data_port.DataPort data_port: The data port to be checked
        :return bool validity, str message: validity is True, when the data port is valid, False else. message gives
            more information especially if the data port is not valid
        """
        # First check inputs and outputs
        valid, message = super(ContainerState, self)._check_data_port_name(data_port)
        if not valid:
            return False, message

        if data_port.data_port_id in self.scoped_variables:
            for scoped_variable in self.scoped_variables.itervalues():
                if data_port.name == scoped_variable.name and data_port is not scoped_variable:
                    return False, "scoped variable name already existing in state's scoped variables"

        return True, message

    def _check_data_flow_validity(self, check_data_flow):
        """Checks the validity of a data flow

        Calls further checks to inspect the id, ports and data types.

        :param rafcon.statemachine.data_flow.DataFlow check_data_flow: The data flow to be checked
        :return bool validity, str message: validity is True, when the data flow is valid, False else. message gives
            more information especially if the data flow is not valid
        """
        valid, message = self._check_data_flow_id(check_data_flow)
        if not valid:
            return False, message

        valid, message = self._check_data_flow_ports(check_data_flow)
        if not valid:
            return False, message

        return self._check_data_flow_types(check_data_flow)

    def _check_data_flow_id(self, data_flow):
        """Checks the validity of a data flow id

        Checks whether the id of the given data flow is already by anther data flow used within the state.

        :param rafcon.statemachine.data_flow.DataFlow data_flow: The data flow to be checked
        :return bool validity, str message: validity is True, when the data flow is valid, False else. message gives
            more information especially if the data flow is not valid
        """
        data_flow_id = data_flow.data_flow_id
        if data_flow_id in self.data_flows and data_flow is not self.data_flows[data_flow_id]:
            return False, "data_flow_id already existing"
        return True, "valid"

    def _check_data_flow_ports(self, data_flow):
        """Checks the validity of the ports of a data flow

        Checks whether the ports of a data flow are existing and whether it is allowed to connect these ports.

        :param rafcon.statemachine.data_flow.DataFlow data_flow: The data flow to be checked
        :return bool validity, str message: validity is True, when the data flow is valid, False else. message gives
            more information especially if the data flow is not valid
        """
        from_state_id = data_flow.from_state
        to_state_id = data_flow.to_state
        from_data_port_id = data_flow.from_key
        to_data_port_id = data_flow.to_key

        # Check whether to and from port are existing
        from_data_port = self.get_data_port(from_state_id, from_data_port_id)
        if not from_data_port:
            return False, "Data flow origin not existing -> {0}".format(data_flow)
        to_data_port = self.get_data_port(to_state_id, to_data_port_id)
        if not to_data_port:
            return False, "Data flow target not existing -> {0}".format(data_flow)

        # Data_ports without parents are not allowed to be connected twice
        if not to_data_port.parent:
            return False, "to_data_port does not have a parent -> {0}".format(data_flow)
        if not from_data_port.parent:
            return False, "from_data_port does not have a parent -> {0}".format(data_flow)

        # Check, whether the origin of the data flow is valid
        if from_state_id == self.state_id:  # data_flow originates in container state
            if from_data_port_id not in self.input_data_ports and from_data_port_id not in self.scoped_variables:
                return False, "Data flow origin port must be an input port or scoped variable, when the data flow " \
                              "starts in the parent state -> {0}".format(data_flow)
        else:  # data flow originates in child state
            if from_data_port_id not in from_data_port.parent.output_data_ports:
                return False, "Data flow origin port must be an output port, when the data flow " \
                              "starts in the child state -> {0}".format(data_flow)

        # Check, whether the target of a data flow is valid
        if to_state_id == self.state_id:  # data_flow ends in container state
            if to_data_port_id not in self.output_data_ports and to_data_port_id not in self.scoped_variables:
                return False, "Data flow target port must be an output port or scoped variable, when the data flow " \
                              "goes to the parent state -> {0}".format(data_flow)
        else:  # data_flow ends in child state
            if to_data_port_id not in to_data_port.parent.input_data_ports:
                return False, "Data flow target port must be an input port, when the data flow goes to a child state" \
                              " -> {0}".format(data_flow)

        # Check, whether the target port is already connected
        for existing_data_flow in self.data_flows.itervalues():
            to_data_port_existing = self.get_data_port(existing_data_flow.to_state, existing_data_flow.to_key)
            from_data_port_existing = self.get_data_port(existing_data_flow.from_state, existing_data_flow.from_key)
            if to_data_port is to_data_port_existing and data_flow is not existing_data_flow:
                if from_data_port is from_data_port_existing:
                    return False, "Exactly the same data flow is already existing -> {0}".format(data_flow)

        return True, "valid"

    def _check_data_flow_types(self, check_data_flow):
        """Checks the validity of the data flow connection

        Checks whether the ports of a data flow have matching data types.

        :param rafcon.statemachine.data_flow.DataFlow check_data_flow: The data flow to be checked
        :return bool validity, str message: validity is True, when the data flow is valid, False else. message gives
            more information especially if the data flow is not valid
        """
        # Check whether the data types or origin and target fit
        from_data_port = self.get_data_port(check_data_flow.from_state, check_data_flow.from_key)
        to_data_port = self.get_data_port(check_data_flow.to_state, check_data_flow.to_key)
        if not type_inherits_of_type(from_data_port.data_type, to_data_port.data_type):
            return False, "Data flow origin and target do not have matching data types (from '{0}' to '{1}')".format(
                from_data_port.data_type, to_data_port.data_type)
        return True, "valid"

    def _check_transition_validity(self, check_transition):
        """Checks the validity of a transition

        Calls further checks to inspect the id, origin, target and connection of the transition.

        :param rafcon.statemachine.transition.Transition check_transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """
        valid, message = self._check_transition_id(check_transition)
        if not valid:
            return False, message

        # Separate check for start transitions
        if check_transition.from_state is None:
            return self._check_start_transition(check_transition)

        valid, message = self._check_transition_origin(check_transition)
        if not valid:
            return False, message

        valid, message = self._check_transition_target(check_transition)
        if not valid:
            return False, message

        return self._check_transition_connection(check_transition)

    def _check_transition_id(self, transition):
        """Checks the validity of a transition id

        Checks whether the transition id is already used by another transition within the state

        :param rafcon.statemachine.transition.Transition transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """
        transition_id = transition.transition_id
        if transition_id in self.transitions and transition is not self.transitions[transition_id]:
            return False, "transition_id already existing"
        return True, "valid"

    def _check_start_transition(self, start_transition):
        """Checks the validity of a start transition

        Checks whether the given transition is a start transition a whether it is the only one within the state.

        :param rafcon.statemachine.transition.Transition start_transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """
        for transition in self.transitions.itervalues():
            if transition.from_state is None:
                if start_transition is not transition:
                    return False, "Only one start transition is allowed"

        if start_transition.from_outcome is not None:
            return False, "from_outcome must not be set in start transition"

        return self._check_transition_target(start_transition)

    def _check_transition_target(self, transition):
        """Checks the validity of a transition target

        Checks whether the transition target is valid.

        :param rafcon.statemachine.transition.Transition transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """

        to_state_id = transition.to_state
        to_outcome_id = transition.to_outcome

        if to_state_id == self.state_id:
            if to_outcome_id not in self.outcomes:
                return False, "to_outcome is not existing"
        else:
            if to_state_id not in self.states:
                return False, "to_state is not existing"
            if to_outcome_id is not None:
                return False, "to_outcome must be None as transition goes to child state"

        return True, "valid"

    def _check_transition_origin(self, transition):
        """Checks the validity of a transition origin

        Checks whether the transition origin is valid.

        :param rafcon.statemachine.transition.Transition transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """
        from_state_id = transition.from_state
        from_outcome_id = transition.from_outcome

        if from_state_id != self.state_id and from_state_id not in self.states:
            return False, "from_state not existing"

        from_outcome = self.get_outcome(from_state_id, from_outcome_id)
        if from_outcome is None:
            return False, "from_outcome not existing in from_state"

        return True, "valid"

    def _check_transition_connection(self, check_transition):
        """Checks the validity of a transition connection

        Checks whether the transition is allowed to connect the origin with the target.

        :param rafcon.statemachine.transition.Transition check_transition: The transition to be checked
        :return bool validity, str message: validity is True, when the transition is valid, False else. message gives
            more information especially if the transition is not valid
        """
        from_state_id = check_transition.from_state
        from_outcome_id = check_transition.from_outcome
        to_state_id = check_transition.to_state
        to_outcome_id = check_transition.to_outcome

        # check for connected origin
        for transition in self.transitions.itervalues():
            if transition.from_state == from_state_id:
                if transition.from_outcome == from_outcome_id:
                    if check_transition is not transition:
                        return False, "transition origin already connected to another transition"

        if from_state_id in self.states and to_state_id in self.states and to_outcome_id is not None:
            return False, "no transition from one outcome to another one on the same hierarchy allowed"

        return True, "valid"

    # ---------------------------------------------------------------------------------------------
    # ----------------------------------------- misc ----------------------------------------------
    # ---------------------------------------------------------------------------------------------

    def get_states_statistics(self, hierarchy_level):
        """
        Returns the numer of child states
        :return:
        """
        number_of_all_child_states = 0
        max_child_hierarchy_level = 0
        for s in self.states.itervalues():
            child_hierarchy_level = 0
            number_of_child_states, child_hierarchy_level = s.get_states_statistics(child_hierarchy_level)
            number_of_all_child_states += number_of_child_states
            if child_hierarchy_level > max_child_hierarchy_level:
                max_child_hierarchy_level = child_hierarchy_level

        return number_of_all_child_states + 1, hierarchy_level + max_child_hierarchy_level + 1

    def get_number_of_transitions(self):
        """
        Returns the numer of child states
        :return:
        """
        number_of_all_transitions = 0
        for s in self.states.itervalues():
            number_of_all_transitions += s.get_number_of_transitions()
        return number_of_all_transitions + len(self.transitions)

    # ---------------------------------------------------------------------------------------------
    # ------------ Properties for all class fields that must be observed by gtkmvc ----------------
    # ---------------------------------------------------------------------------------------------

    @property
    def states(self):
        """Property for the _states field

        The setter-method substitute ContainerState.states which is a dict. The method checks if the elements are
        of the right type  or will cancel the operation and recover old outcomes. The method does check validity of
        the elements by calling the parent-setter.

        """
        return self._states

    @states.setter
    @Observable.observed
    def states(self, states):
        """ Setter for _states field

        See property

        :param states: Dictionary of States
        :raises exceptions.TypeError: if the states parameter is of wrong type
        :raises exceptions.AttributeError: if the keys of the dictionary and the state_ids in the dictionary do not match
        """
        if not isinstance(states, dict):
            raise TypeError("states must be of type dict")
        if [state_id for state_id, state in states.iteritems() if not isinstance(state, State)]:
            raise TypeError("element of container_state.states must be of type State")
        if [state_id for state_id, state in states.iteritems() if not state_id == state.state_id]:
            raise AttributeError("The key of the state dictionary and the id of the state do not match")

        old_states = self._states
        self._states = states
        for state_id, state in states.iteritems():
            try:
                state.parent = self
            except ValueError:
                self._states = old_states
                raise

    @property
    def transitions(self):
        """Property for the _transitions field

        The setter-method substitute ContainerState._transitions with a handed dictionary. The method checks if the
        elements are of the right type and the keys consistent (Transition.transition_id==key). The method does check
        validity of the elements by calling the parent-setter and in case of failure cancel the operation and
        recover old _transitions dictionary.

        :return: Dictionary transitions[transition_id] of :class:`rafcon.statemachine.transition.Transition`
        :rtype: dict
        """
        return self._transitions

    @transitions.setter
    @Observable.observed
    def transitions(self, transitions):
        """ Setter for _transitions field

        See property

        :param: transitions: Dictionary transitions[transition_id] of :class:`rafcon.statemachine.transition.Transition`
        :raises exceptions.TypeError: if the transitions parameter has the wrong type
        :raises exceptions.AttributeError: if the keys of the transitions dictionary and the transition_ids of the
                                            transitions in the dictionary do not match
        """
        if not isinstance(transitions, dict):
            raise TypeError("transitions must be of type dict")
        if [t_id for t_id, transition in transitions.iteritems() if not isinstance(transition, Transition)]:
            raise TypeError("element of transitions must be of type Transition")
        if [t_id for t_id, transition in transitions.iteritems() if not t_id == transition.transition_id]:
            raise AttributeError("The key of the transition dictionary and the id of the transition do not match")

        old_transitions = self._transitions
        self._transitions = transitions
        for transition_id, transition in transitions.iteritems():
            try:
                transition.parent = self
            except ValueError:
                self._transitions = old_transitions
                raise

    @property
    def data_flows(self):
        """Property for the _data_flows field

        The setter-method substitute ContainerState._data_flows with handed dictionary. The method checks if the
        elements are of the right type and the keys consistent (DataFlow.data_flow_id==key). The method does check
        validity of the elements by calling the parent-setter and in case of failure cancel the operation and
        recover old _data_flows dictionary.

        :return: Dictionary data_flows[data_flow_id] of :class:`rafcon.statemachine.data_flow.DataFlow`
        :rtype: dict
        """
        return self._data_flows

    @data_flows.setter
    @Observable.observed
    def data_flows(self, data_flows):
        """ Setter for _data_flows field

        See property

        :param dict data_flows: Dictionary data_flows[data_flow_id] of :class:`rafcon.statemachine.data_flow.DataFlow`
        :raises exceptions.TypeError: if the data_flows parameter has the wrong type
        :raises exceptions.AttributeError: if the keys of the data_flows dictionary and the data_flow_ids of the
                                            data flows in the dictionary do not match
        """
        if not isinstance(data_flows, dict):
            raise TypeError("data_flows must be of type dict")
        if [df_id for df_id, data_flow in data_flows.iteritems() if not isinstance(data_flow, DataFlow)]:
            raise TypeError("element of data_flows must be of type DataFlow")
        if [df_id for df_id, data_flow in data_flows.iteritems() if not df_id == data_flow.data_flow_id]:
            raise AttributeError("The key of the data flow dictionary and the id of the data flow do not match")

        old_data_flows = self._data_flows
        self._data_flows = data_flows
        for data_flow_id, data_flow in data_flows.iteritems():
            try:
                data_flow.parent = self
            except ValueError:
                self._data_flows = old_data_flows
                raise

    @property
    def start_state_id(self):
        """ The start state is the state to which the first transition goes to.

        The setter-method creates a unique first transition to the state with the given id.
        Existing first transitions are removed. If the given state id is None, the first transition is removed.

        :return: The id of the start state
        """
        for transition_id in self.transitions:
            if self.transitions[transition_id].from_state is None:
                to_state = self.transitions[transition_id].to_state
                if to_state is not None:
                    return to_state
                else:
                    return self.state_id
        return None

    @start_state_id.setter
    # @Observable.observed
    def start_state_id(self, start_state_id, to_outcome=None):
        """Set the start state of the container state

        See property

        :param start_state_id: The state id of the state which should be executed first in the Container state
        :raises exceptions.ValueError: if the start_state_id does not exist in
                                    :py:attr:`rafcon.statemachine.states.container_state.ContainerState.states`
        """
        if start_state_id is not None and start_state_id not in self.states:
            raise ValueError("start_state_id does not exist")

        if start_state_id is None and to_outcome is not None:  # this is the case if the start state is the state itself
            if to_outcome not in self.outcomes:
                raise ValueError("to_outcome does not exist")
            if start_state_id != self.state_id:
                raise ValueError("to_outcome defined but start_state_id is not state_id")

        # First we remove the transition to the start state
        for transition_id in self.transitions:
            if self.transitions[transition_id].from_state is None:
                # If the current start state is the same as the old one, we don't have to do anything
                if self.transitions[transition_id].to_state == start_state_id:
                    return
                self.remove_transition(transition_id)
                break
        if start_state_id is not None:
            self.add_transition(None, None, start_state_id, to_outcome)

    @property
    def scoped_variables(self):
        """Property for the _scoped_variables field

        The setter-method ContainerState._scoped_variables with a handed dictionary. The method checks if the elements
        are of the right type and the keys consistent (Transition.transition_id==key). The method does check validity
        of the elements by calling the parent-setter and in case of failure cancel the operation and recover old
        _scoped_variables dictionary.

        :return: Dictionary scoped_variables[data_port_id] of :class:`rafcon.statemachine.scope.ScopedVariable`
        :rtype: dict
        """
        return self._scoped_variables

    @scoped_variables.setter
    @Observable.observed
    def scoped_variables(self, scoped_variables):
        """ Setter for _scoped_variables field

        See property

        :param dict scoped_variables: Dictionary scoped_variables[data_port_id] of :class:`rafcon.statemachine.scope.ScopedVariable`
        :raises exceptions.TypeError: if the scoped_variables parameter has the wrong type
        :raises exceptions.AttributeError: if the keys of the scoped_variables dictionary and the ids
                                            of the scoped variables in the dictionary do not match
        """
        if not isinstance(scoped_variables, dict):
            raise TypeError("scoped_variables must be of type dict")
        if [sv_id for sv_id, sv in scoped_variables.iteritems() if not isinstance(sv, ScopedVariable)]:
            raise TypeError("element of scope variable must be of type ScopedVariable")
        if [sv_id for sv_id, sv in scoped_variables.iteritems() if not sv_id == sv.data_port_id]:
            raise AttributeError("The key of the scope variable dictionary and "
                                 "the id of the scope variable do not match")

        old_scoped_variables = self._scoped_variables
        self._scoped_variables = scoped_variables
        for port_id, scoped_variable in scoped_variables.iteritems():
            try:
                scoped_variable.parent = self
            except ValueError:
                self._scoped_variables = old_scoped_variables
                raise

    @property
    def scoped_data(self):
        """Property for the _scoped_data field

        """
        return self._scoped_data

    @scoped_data.setter
    #@Observable.observed
    def scoped_data(self, scoped_data):
        if not isinstance(scoped_data, dict):
            raise TypeError("scoped_results must be of type dict")
        for key, s in scoped_data.iteritems():
            if not isinstance(s, ScopedData):
                raise TypeError("element of scoped_data must be of type ScopedData")
        self._scoped_data = scoped_data

    @property
    def v_checker(self):
        """Property for the _v_checker field

        """
        return self._v_checker

    @v_checker.setter
    #@Observable.observed
    def v_checker(self, v_checker):
        if not isinstance(v_checker, ValidityChecker):
            raise TypeError("validity_check must be of type ValidityChecker")
        self._v_checker = v_checker

    @property
    def child_execution(self):
        """Property for the _child_execution field
        """
        if self.state_execution_status is StateExecutionState.EXECUTE_CHILDREN:
            return True
        else:
            return False
