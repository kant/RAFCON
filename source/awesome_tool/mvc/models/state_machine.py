from gtkmvc import ModelMT, Observable
from enum import Enum

from awesome_tool.statemachine.state_machine import StateMachine
from awesome_tool.statemachine.states.container_state import ContainerState
from awesome_tool.mvc.models import ContainerStateModel, StateModel, TransitionModel, DataFlowModel

from awesome_tool.utils.vividict import Vividict

from awesome_tool.mvc.history import History


class StateMachineModel(ModelMT):
    """This model class manages a :class:`awesome_tool.statemachine.state_machine.StateMachine`

    The model class is part of the MVC architecture. It holds the data to be shown (in this case a state machine).

    :param StateMachine state_machine: The state machine to be controlled and modified
    """

    state_machine = None
    selection = None
    root_state = None

    __observables__ = ("state_machine", "root_state", "selection")

    def __init__(self, state_machine, meta=None):
        """Constructor
        """
        ModelMT.__init__(self)  # pass columns as separate parameters

        assert isinstance(state_machine, StateMachine)

        self.state_machine = state_machine

        root_state = self.state_machine.root_state
        if isinstance(root_state, ContainerState):
            self.root_state = ContainerStateModel(root_state)
        else:
            self.root_state = StateModel(root_state)

        self.root_state.register_observer(self)

        self.selection = Selection()
        
        self.history = History(self)

        if isinstance(meta, Vividict):
            self.meta = meta
        else:
            self.meta = Vividict()

    @ModelMT.observe("state", before=True)
    @ModelMT.observe("outcomes", before=True)
    @ModelMT.observe("is_start", before=True)
    @ModelMT.observe("states", before=True)
    @ModelMT.observe("transitions", before=True)
    @ModelMT.observe("data_flows", before=True)
    @ModelMT.observe("input_data_ports", before=True)
    @ModelMT.observe("scoped_variables", before=True)
    def root_state_model_before_change(self, model, prop_name, info):
        if not self._list_modified(prop_name, info):
            self.state_machine.root_state_before_change(model=info['model'], prop_name=info['prop_name'],
                                                        instance=info['instance'],
                                                        method_name=info['method_name'], args=info['args'],
                                                        kwargs=info['kwargs'])


    @ModelMT.observe("state", after=True)
    @ModelMT.observe("outcomes", after=True)
    @ModelMT.observe("is_start", after=True)
    @ModelMT.observe("states", after=True)
    @ModelMT.observe("transitions", after=True)
    @ModelMT.observe("data_flows", after=True)
    @ModelMT.observe("input_data_ports", after=True)
    @ModelMT.observe("output_data_ports", after=True)
    @ModelMT.observe("scoped_variables", after=True)
    def root_state_model_after_change(self, model, prop_name, info):
        if not self._list_modified(prop_name, info):
            self.state_machine.root_state_after_change(model=info['model'], prop_name=info['prop_name'],
                                                       instance=info['instance'],
                                                       method_name=info['method_name'], result=info['result'],
                                                       args=info['args'], info=info['kwargs'])

    @staticmethod
    def _list_modified(prop_name, info):
        """Check whether the given operation is a list operation

        The function checks whether the property that has been changed is a list. If so, the operation is investigated
        further. If the operation is a basic list operation, the function return True.
        :param prop_name: The property that was changed
        :param info: Dictionary with information about the operation
        :return: True if the operation was a list operation, False else
        """
        if prop_name in ["states", "transitions", "data_flows", "input_data_ports", "output_data_ports",
                         "scoped_variables"]:
            if info['method_name'] in ["append", "extend", "insert", "pop", "remove", "reverse", "sort",
                                       "__delitem__", "__setitem__"]:
                return True
        return False