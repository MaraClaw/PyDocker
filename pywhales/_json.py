"""Shared JSON encoding and decoding with explicit native and compatible paths.

The native pair uses orjson and accepts only what orjson accepts. The compatible
pair delegates to the standard library and keeps its output format, number
handling, and error types for persisted hashes and diagnostic files. Callers
choose a path explicitly; neither falls back to the other.
"""

import json
from typing import Any

import orjson


def dumps(value: object) -> bytes:
    """Encode a value as compact JSON with orjson.

    Args:
        value: Object to encode.

    Returns:
        UTF-8 JSON bytes with no spaces, keys in insertion order, and non-finite
        floats written as null.

    Raises:
        orjson.JSONEncodeError: If the value holds an integer beyond 64 bits, a
            lone surrogate, a non-string key, or a type orjson can't encode.
    """
    return orjson.dumps(value)


# The decoder stub returns arbitrary JSON; concrete callers validate its shape.
def loads(payload: str | bytes | bytearray | memoryview) -> Any:  # noqa: ANN401
    """Decode a JSON document with orjson.

    Args:
        payload: Text, or UTF-8 bytes without a byte order mark.

    Returns:
        The decoded JSON value. Integers beyond 64 bits decode as floats.

    Raises:
        orjson.JSONDecodeError: If the payload is malformed, isn't valid UTF-8, or
            holds NaN, Infinity, or a lone surrogate escape.
    """
    return orjson.loads(payload)


def dumps_compat(
    value: object,
    *,
    sort_keys: bool = False,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    allow_nan: bool = True,
) -> str:
    """Encode a value as JSON text exactly as the standard library does.

    Output is always ASCII, with every non-ASCII character escaped.

    Args:
        value: Object to encode.
        sort_keys: Sort object keys by code point when true.
        indent: Spaces per nesting level; None writes a single line.
        separators: Item and key separators; None keeps the standard library
            default, which follows the indent setting.
        allow_nan: Write NaN and the infinities when true, reject them when false.

    Returns:
        The JSON text.

    Raises:
        ValueError: If allow_nan is false and the value holds a non-finite float.
        TypeError: If the value holds a type JSON can't represent.
    """
    return json.dumps(
        value,
        sort_keys=sort_keys,
        indent=indent,
        separators=separators,
        allow_nan=allow_nan,
        ensure_ascii=True,
    )


# Match the stdlib decoder stub; concrete callers validate the JSON shape.
def loads_compat(payload: str | bytes | bytearray) -> Any:  # noqa: ANN401
    """Decode a JSON document exactly as the standard library does.

    Args:
        payload: Text, or bytes in UTF-8, UTF-16, or UTF-32.

    Returns:
        The decoded JSON value. Integers keep arbitrary precision, and NaN and the
        infinities decode to floats.

    Raises:
        json.JSONDecodeError: If the payload is malformed.
        UnicodeDecodeError: If bytes can't be decoded in the detected encoding.
    """
    return json.loads(payload)
