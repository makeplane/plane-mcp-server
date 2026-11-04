"""Tool-result token sizing for evaluation drivers."""

from __future__ import annotations

from dataclasses import dataclass

TOKEN_ESTIMATE_METHOD = "chars_div_4"
TOKENIZER_ENCODING = "cl100k_base"


@dataclass(frozen=True)
class ResultTokenCount:
    """A tool-result token count and how it was obtained."""

    value: int
    estimated: bool
    method: str
    tokenizer_failed: bool = False


def estimate_result_tokens(result_chars: int) -> int:
    """Deterministically estimate tokens from a recorded character count."""
    chars = max(0, int(result_chars))
    if chars == 0:
        return 0
    return max(1, (chars + 3) // 4)


def count_result_text_tokens(text: str) -> ResultTokenCount:
    """Count serialized result text with tiktoken, or identify an estimate.

    The optional import stays here, in the harness analysis process. The stdlib-
    only recording proxy never imports this module.
    """
    try:
        import tiktoken
    except ImportError:
        return ResultTokenCount(
            estimate_result_tokens(len(text)),
            estimated=True,
            method=TOKEN_ESTIMATE_METHOD,
        )
    except Exception:
        return ResultTokenCount(
            estimate_result_tokens(len(text)),
            estimated=True,
            method=TOKEN_ESTIMATE_METHOD,
            tokenizer_failed=True,
        )

    try:
        encoding = tiktoken.get_encoding(TOKENIZER_ENCODING)
        encode_ordinary = getattr(encoding, "encode_ordinary", None)
        tokens = encode_ordinary(text) if callable(encode_ordinary) else encoding.encode(text)
        return ResultTokenCount(
            len(tokens),
            estimated=False,
            method=f"tiktoken:{TOKENIZER_ENCODING}",
        )
    except Exception:
        return ResultTokenCount(
            estimate_result_tokens(len(text)),
            estimated=True,
            method=TOKEN_ESTIMATE_METHOD,
            tokenizer_failed=True,
        )


__all__ = [
    "TOKEN_ESTIMATE_METHOD",
    "TOKENIZER_ENCODING",
    "ResultTokenCount",
    "count_result_text_tokens",
    "estimate_result_tokens",
]
