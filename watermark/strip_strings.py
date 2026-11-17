def remove_substring(data: dict | list | str, target: str) -> dict | list | str:
    """Return a copy of ``data`` with ``target`` removed from every string, at any depth.

    ``data`` can be a dict, a list or a plain string. Inside dicts, both keys
    and values are cleaned.
    """

    def _walk(node):
        if isinstance(node, str):
            return node.replace(target, "")
        if isinstance(node, dict):
            return {_walk(key): _walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(data)
