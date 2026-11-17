def remove_substring(data: dict, target: str) -> dict:
    """Return a copy of ``data`` with ``target`` removed from every string key and value, at any depth."""

    def _walk(node):
        if isinstance(node, str):
            return node.replace(target, "")
        if isinstance(node, dict):
            return {_walk(key): _walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [_walk(item) for item in node]
        return node

    return _walk(data)
