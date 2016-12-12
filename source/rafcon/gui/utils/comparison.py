
def compare_variables(tree_model, iter1, iter2, user_data=None):
        """Triggered upon updating the list of global variables

        Helper method to sort global variables alphabetically.

        :param tree_model: Tree model implementing the gtk.TreeSortable interface.
        :param iter1: Points at a row.
        :param iter2: Points at a row.
        """
        path1 = tree_model.get_path(iter1)[0]
        path2 = tree_model.get_path(iter2)[0]
        # get key of first variable
        name1 = tree_model[path1][0][0]
        # get key of second variable
        name2 = tree_model[path2][0][0]
        name1_as_bits = ' '.join(format(ord(x), 'b') for x in name1)
        name2_as_bits = ' '.join(format(ord(x), 'b') for x in name2)
        if name1_as_bits == name2_as_bits:
            return 0
        elif name1_as_bits > name2_as_bits:
            return 1
        else:
            return -1