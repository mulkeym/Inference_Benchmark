try:
    import tiktoken
    _enc = tiktoken.get_encoding("o200k_base")
except Exception:
    _enc = None


def count_tokens(text: str) -> int:
    if _enc is not None:
        return max(1, len(_enc.encode(text)))
    return max(1, len(text) // 4)
