from rafcon.gui.mygaphas.utils.cache.value_cache import ValueCache


def test_value_retrieval():
    cache = ValueCache()

    cache.store_value("a", 1, {})
    assert 1 == cache.get_value("a", {})
    assert 1 == cache.get_value("a")
    assert None is cache.get_value("a", {"par": 2})

    cache.store_value("b", 2, {"x": 1, "y": 2})
    assert 2 == cache.get_value("b", {"x": 1, "y": 2})
    assert 2 == cache.get_value("b")
    assert None is cache.get_value("b", {})
    assert None is cache.get_value("b", {"par": 2})
    assert None is cache.get_value("b", {"x": 2, "y": 2})
    assert None is cache.get_value("b", {"x": 1, "y": 2, "z": 3})
    assert None is cache.get_value("b", {"x": 1})

    cache.store_value("b", 3, {"x": 1, "y": 2})
    assert 3 == cache.get_value("b", {"x": 1, "y": 2})
    cache.store_value("b", 4, {"x": 2, "y": 1})
    assert 4 == cache.get_value("b", {"x": 2, "y": 1})
    assert None is cache.get_value("b", {"x": 1, "y": 2})

    cache.empty()
    assert None is cache.get_value("a")
    assert None is cache.get_value("b")