import copy

import pytest

from strip_strings import remove_substring


@pytest.mark.parametrize(
    "data, target, expected",
    [
        # values only
        ({"a": "hello world", "b": "worldwide"}, "world", {"a": "hello ", "b": "wide"}),
        # keys get replaced too
        ({"foo_name": "foo_bob", "id": 1}, "foo_", {"name": "bob", "id": 1}),
        # multiple occurrences in one string
        ({"a,b": "a,b,c,d"}, ",", {"ab": "abcd"}),
        # case sensitive
        ({"Foo foo": "Foo foo FOO"}, "foo", {"Foo ": "Foo  FOO"}),
        # special characters are literal, not regex
        ({"a.*b": "c.*d"}, ".*", {"ab": "cd"}),
        ({"C:\\tmp": "C:\\x"}, "\\", {"C:tmp": "C:x"}),
        # empty dict
        ({}, "x", {}),
    ],
)
def test_basic_replacements(data, target, expected):
    assert remove_substring(data, target) == expected


@pytest.mark.parametrize(
    "data, target, expected",
    [
        # plain string input
        ("foo_bob", "foo_", "bob"),
        ("no match here", "zzz", "no match here"),
        ("", "x", ""),
        # list input
        (["foo_a", "b", "foo_foo_c"], "foo_", ["a", "b", "c"]),
        ([1, None, "x1"], "1", [1, None, "x"]),
        ([], "x", []),
        # list containing dicts and nested lists
        (
            ["foo_a", {"foo_k": ["foo_v", {"foo_deep": "foo_x"}]}, [["foo_z"]]],
            "foo_",
            ["a", {"k": ["v", {"deep": "x"}]}, [["z"]]],
        ),
    ],
)
def test_string_and_list_inputs(data, target, expected):
    result = remove_substring(data, target)
    assert result == expected
    assert type(result) is type(data)


def test_list_input_not_mutated():
    data = ["rm_a", {"rm_b": ["rm_c"]}]
    original = copy.deepcopy(data)
    remove_substring(data, "rm_")
    assert data == original


def test_nested_keys_and_values():
    data = {"x_l1": {"x_l2": {"x_l3": {"x_l4": "x_leaf"}}}}
    assert remove_substring(data, "x_") == {"l1": {"l2": {"l3": {"l4": "leaf"}}}}


def test_lists_inside_dicts():
    data = {
        "foo_users": [
            {"foo_name": "foo-bob", "tags": ["foo", "bar-foo", ["nested-foo"]]},
            {"name": "alice", "meta": {"foo_notes": ["foofoo", "ok"]}},
        ]
    }
    expected = {
        "_users": [
            {"_name": "-bob", "tags": ["", "bar-", ["nested-"]]},
            {"name": "alice", "meta": {"_notes": ["", "ok"]}},
        ]
    }
    assert remove_substring(data, "foo") == expected


def test_non_string_keys_and_values_untouched():
    data = {1: "a1a", "n1": None, "f": 2.5, "b": True, "i": 1}
    assert remove_substring(data, "1") == {1: "aa", "n": None, "f": 2.5, "b": True, "i": 1}


def test_no_match_unchanged():
    data = {"a": "abc", "b": ["def"], "c": {"d": "e"}}
    assert remove_substring(data, "zzz") == data


def test_input_not_mutated():
    data = {"rm_a": ["rm_b", {"rm_c": "rm_d"}]}
    original = copy.deepcopy(data)
    remove_substring(data, "rm_")
    assert data == original


def _all_containers(obj):
    """Yield every dict/list/set inside obj (including obj itself)."""
    if isinstance(obj, (dict, list, set)):
        yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _all_containers(value)
    elif isinstance(obj, (list, set)):
        for item in obj:
            yield from _all_containers(item)


def test_result_shares_no_containers_with_input():
    data = {"a": [1, {"b": ["c", {"d": []}]}], "e": {}, "s": {1, 2}, "x": [[[]]]}
    result = remove_substring(data, "zzz")  # no match on purpose: result looks identical

    assert result == data
    original_ids = {id(c) for c in _all_containers(data)}
    result_ids = {id(c) for c in _all_containers(result)}
    assert original_ids.isdisjoint(result_ids)


def test_mutating_result_does_not_touch_original():
    data = {"foo_a": [1, {"foo_b": ["foo_c"]}], "tags": {"x", "y"}}
    original = copy.deepcopy(data)

    result = remove_substring(data, "foo_")
    result["a"].append(99)
    result["a"][1]["b"].append("new")
    result["a"][1]["b"][0] = "changed"
    result["tags"].add("z")
    result["brand_new_key"] = "v"

    assert data == original


@pytest.mark.parametrize(
    "data",
    [{"foo_k": "foo_v"}, ["foo_a", ["foo_b"]], "foo_s"],
)
def test_returns_same_type_and_new_object(data):
    result = remove_substring(data, "foo_")
    assert type(result) is type(data)
    if not isinstance(data, str):  # equal strings may be interned by Python; strings can't be mutated anyway
        assert result is not data


def test_very_deep_nesting():
    data = leaf = {}
    for _ in range(200):
        leaf["DEEPn"] = {}
        leaf = leaf["DEEPn"]
    leaf["DEEPv"] = "DEEPvalue"

    node = remove_substring(data, "DEEP")
    for _ in range(200):
        node = node["n"]
    assert node == {"v": "value"}
