"""View-on-view dependency ordering.

A view that selects from another view must be created second, or the load fails.
"""

from lakehouse_schema_extraction.dialects.postgres import order_views


def names(views):
    return [v["view_name"] for v in views]


def V(name):
    return {"view_name": name, "definition": "", "view_kind": "view"}


def test_dependency_is_emitted_before_its_dependent():
    views = [V("b"), V("a")]  # alphabetical order is wrong here
    edges = [{"view_name": "b", "depends_on": "a"}]
    assert names(order_views(views, edges)) == ["a", "b"]


def test_transitive_chain_is_fully_ordered():
    views = [V("c"), V("b"), V("a")]
    edges = [
        {"view_name": "c", "depends_on": "b"},
        {"view_name": "b", "depends_on": "a"},
    ]
    assert names(order_views(views, edges)) == ["a", "b", "c"]


def test_independent_views_keep_their_original_order():
    views = [V("x"), V("y"), V("z")]
    assert names(order_views(views, [])) == ["x", "y", "z"]


def test_edges_to_tables_outside_the_view_set_are_ignored():
    """pg_rewrite also reports table dependencies; those never block a view."""
    views = [V("v")]
    edges = [{"view_name": "v", "depends_on": "some_table"}]
    assert names(order_views(views, edges)) == ["v"]


def test_cycle_degrades_to_original_order_instead_of_hanging():
    views = [V("a"), V("b")]
    edges = [
        {"view_name": "a", "depends_on": "b"},
        {"view_name": "b", "depends_on": "a"},
    ]
    assert names(order_views(views, edges)) == ["a", "b"]


def test_self_reference_is_ignored():
    views = [V("a")]
    assert names(order_views(views, [{"view_name": "a", "depends_on": "a"}])) == ["a"]


def test_every_view_is_emitted_exactly_once():
    views = [V(n) for n in "abcdef"]
    edges = [
        {"view_name": "f", "depends_on": "a"},
        {"view_name": "c", "depends_on": "f"},
        {"view_name": "d", "depends_on": "c"},
    ]
    result = names(order_views(views, edges))
    assert sorted(result) == list("abcdef")
    assert result.index("a") < result.index("f") < result.index("c") < result.index("d")
