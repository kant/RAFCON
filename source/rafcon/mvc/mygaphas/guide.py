from gaphas.aspect import InMotion
from gaphas.guide import GuidedItemInMotion, Guide

from rafcon.mvc.mygaphas.items.state import StateView, NameView


@InMotion.when_type(StateView)
class GuidedStateInMotion(GuidedItemInMotion):

    MARGIN = 5

    def get_excluded_items(self):
        return set()

    def start_move(self, pos):
        super(GuidedStateInMotion, self).start_move(pos)
        self.item.moving = True

    def move(self, pos):
        super(GuidedStateInMotion, self).move(pos)
        parent_item = self.item.parent
        if parent_item:
            constraint = parent_item.keep_rect_constraints[self.item]
            self.view.canvas.solver.request_resolve_constraint(constraint)

    def stop_move(self):
        super(GuidedStateInMotion, self).stop_move()
        self.item.moving = False

    def find_vertical_guides(self, item_vedges, pdx, height, excluded_items):
        # The root state cannot be aligned
        if not self.item.parent:
            return 0, ()

        states_v = self._get_siblings_and_parent()

        try:
            guides = map(Guide, states_v)
        except TypeError:
            guides = []

        vedges = set()
        for g in guides:
            for x in g.vertical():
                vedges.add(self.view.get_matrix_i2v(g.item).transform_point(x, 0)[0])
        dx, edges_x = self.find_closest(item_vedges, vedges)

        return dx, edges_x

    def find_horizontal_guides(self, item_hedges, pdy, width, excluded_items):
        # The root state cannot be aligned
        if not self.item.parent:
            return 0, ()

        states_v = self._get_siblings_and_parent()

        try:
            guides = map(Guide, states_v)
        except TypeError:
            guides = []

        hedges = set()
        for g in guides:
            for y in g.horizontal():
                hedges.add(self.view.get_matrix_i2v(g.item).transform_point(0, y)[1])

        dy, edges_y = self.find_closest(item_hedges, hedges)
        return dy, edges_y

    def _get_siblings_and_parent(self):
        states_v = []
        parent_state_v = self.item.parent
        states_v.append(parent_state_v)
        for sibling in self.view.canvas.get_children(parent_state_v):
            if isinstance(sibling, StateView) and sibling is not self.item:
                states_v.append(sibling)
        return states_v


@InMotion.when_type(NameView)
class GuidedNameInMotion(GuidedItemInMotion):
    def move(self, pos):
        super(GuidedNameInMotion, self).move(pos)
        parent_item = self.item.parent
        if parent_item:
            constraint = parent_item.keep_rect_constraints[self.item]
            self.view.canvas.solver.request_resolve_constraint(constraint)
