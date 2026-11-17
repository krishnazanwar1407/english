import copy


def remove_substring(data: dict | list | str, target: str) -> dict | list | str:
    """Return a NEW object with ``target`` removed from every string, at any depth.

    ``data`` can be a dict, a list or a plain string. Inside dicts, both keys
    and values are cleaned. The original ``data`` is never modified, and the
    result shares no mutable objects with it.
    """

    def _walk(node):
        if isinstance(node, str):
            return node.replace(target, "")  # str.replace always returns a new string
        if isinstance(node, dict):
            return {_walk(key): _walk(value) for key, value in node.items()}  # brand-new dict
        if isinstance(node, list):
            return [_walk(item) for item in node]  # brand-new list
        return copy.deepcopy(node)  # anything else: copy it so nothing is shared

    return _walk(data)
