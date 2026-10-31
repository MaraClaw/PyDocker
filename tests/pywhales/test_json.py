import hashlib
import importlib
import inspect
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import NoReturn
from unittest.mock import Mock

import orjson
import pytest
from pydantic import JsonValue

from pywhales.convergence._wire import ServiceWire, canonical, fingerprint
from pywhales.reconciliation import DesiredDescription, InputReference, SecretReference
from pywhales.reconciliation._intent import ResourceClass

type Render = Callable[[object], str]
type Text = str | bytes | bytearray

HUGE = 10**30
HUGE_TEXT = "1" + "0" * 30


@dataclass(frozen=True, slots=True)
class Profile:
    compat: Render
    oracle: Render


PROFILES = {
    "defaults": Profile(
        lambda value: utility().dumps_compat(value),
        lambda value: json.dumps(value),
    ),
    "sorted": Profile(
        lambda value: utility().dumps_compat(value, sort_keys=True),
        lambda value: json.dumps(value, sort_keys=True),
    ),
    "compact": Profile(
        lambda value: utility().dumps_compat(value, separators=(",", ":")),
        lambda value: json.dumps(value, separators=(",", ":")),
    ),
    "indent_2": Profile(
        lambda value: utility().dumps_compat(value, indent=2),
        lambda value: json.dumps(value, indent=2),
    ),
    "convergence_canonical": Profile(
        lambda value: utility().dumps_compat(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        lambda value: json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ),
    ),
    "reconciliation_canonical": Profile(
        lambda value: utility().dumps_compat(
            value, sort_keys=True, separators=(",", ":")
        ),
        lambda value: json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ),
    ),
}

VALUES: dict[str, object] = {
    "null": None,
    "booleans": [True, False],
    "integers": [0, -1, 7, 2**63, 2**64 - 1, 2**64, HUGE, -HUGE],
    "floats": [
        0.0,
        -0.0,
        1.0,
        0.1,
        1e16,
        1e22,
        1e-7,
        5e-324,
        1.7976931348623157e308,
        123456789.123456789,
    ],
    "nested": {"b": [1, 2, {"d": None, "c": True}], "a": {}, "e": []},
    "unicode": {"k\u00e9y": "caf\u00e9 \u2603 \U0001f433 \u2028\u2029 \x7f"},
    "escapes": 'quote " backslash \\ \b\f\n\r\t \x00\x01\x1f / </script>',
    "lone_surrogate": "\ud800",
    "empties": ["", [], {}, {"": 1}],
    "tuple": ("a", 1, (2.5,)),
    "long": ["x" * 10_000],
}

NATIVE_ROUND_TRIPS: list[object] = [
    {"b": [1, 2, {"d": None, "c": True}], "a": {}, "e": []},
    {"k\u00e9y": "caf\u00e9 \u2603 \U0001f433 \u2028\u2029 \x7f"},
    'quote " backslash \\ \b\f\n\r\t \x00\x01\x1f / </script>',
    [0.0, 1.0, 0.1, 1e22, 1e-7, 5e-324, 1.7976931348623157e308],
    [2**63, 2**64 - 1],
    ["", " ", "x" * 10_000],
]

NATIVE_DOCUMENTS = [
    '{"a": [1, 2.5, "x", null, true, false], "b": {}}',
    "  [1,\n 2]\t",
    r'"\u00e9\u2603\ud83d\udc33"',
    '"\u00e9\u2603\U0001f433"',
    r'"\"\\\/\b\f\n\r\t\u0001"',
    "[]",
    "{}",
    "0",
    "-1",
    "1.5",
    "1e2",
]

COMPAT_DOCUMENTS = [
    *NATIVE_DOCUMENTS,
    '{"a": 1, "a": 2}',
    HUGE_TEXT,
    "-" + HUGE_TEXT,
    f"[{HUGE_TEXT}, 18446744073709551616, 18446744073709551615]",
    "123456789012345678901234567890.5",
    "1e400",
    "-1e400",
    "1E5",
    "-0",
    "-0.0",
    "NaN",
    "Infinity",
    "-Infinity",
    "[NaN, Infinity, -Infinity]",
    r'"\ud800"',
    r'"\ud83d\udc33"',
    r'"\udc33\ud83d"',
    r'{"\ud800": "\udfff"}',
    "1" * 5000,
]

MALFORMED_DOCUMENTS = [
    "",
    " ",
    "{",
    "[1,]",
    '{"a": }',
    '{"a" 1}',
    "nul",
    '{"a": 1} x',
    "{'a': 1}",
    r'"\x"',
    "[1 2]",
    '"unterminated',
    "\ufeff{}",
    "01",
    "1.",
    "-",
    '{"a":1,}',
    "\n\n  [",
]

ENCODINGS = [
    "utf-8",
    "utf-8-sig",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "utf-32",
    "utf-32-le",
    "utf-32-be",
]


def utility() -> ModuleType:
    return importlib.import_module("pywhales._json")


def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("the wrong JSON backend was invoked")


def outcome[T](function: Callable[[T], object], argument: T) -> tuple[object, ...]:
    try:
        return ("value", repr(function(argument)))
    except Exception as error:
        return ("error", type(error), str(error))


def stdlib_decode_error(document: str) -> json.JSONDecodeError:
    try:
        json.loads(document)
    except json.JSONDecodeError as error:
        return error
    raise AssertionError("fixture must be malformed")


def test_native_dumps_invokes_orjson_and_not_the_stdlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Mock(wraps=orjson.dumps)
    monkeypatch.setattr(orjson, "dumps", spy)
    monkeypatch.setattr(json, "dumps", forbidden)
    value = {"b": [1, None], "a": "x"}
    result = utility().dumps(value)
    assert result == b'{"b":[1,null],"a":"x"}'
    spy.assert_called_once()
    assert spy.call_args.args[0] == value


def test_native_dumps_returns_compact_unsorted_utf8_bytes() -> None:
    result = utility().dumps({"b": 1, "a": [True, None, "\u00e9"]})
    assert type(result) is bytes
    assert result == b'{"b":1,"a":[true,null,"\xc3\xa9"]}'


def test_native_dumps_writes_unicode_as_utf8_without_escapes() -> None:
    assert utility().dumps("\u00e9\u2603\U0001f433") == (
        b'"\xc3\xa9\xe2\x98\x83\xf0\x9f\x90\xb3"'
    )


@pytest.mark.parametrize("value", NATIVE_ROUND_TRIPS)
def test_native_dumps_round_trips_through_the_stdlib(value: object) -> None:
    assert json.loads(utility().dumps(value)) == value


def test_native_dumps_serializes_nonfinite_floats_as_null() -> None:
    assert utility().dumps([math.nan, math.inf, -math.inf]) == b"[null,null,null]"


def test_native_dumps_accepts_the_64_bit_integer_range() -> None:
    assert utility().dumps([2**64 - 1, -(2**63 - 1)]) == (
        b"[18446744073709551615,-9223372036854775807]"
    )


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(HUGE, id="integer_beyond_64_bits"),
        pytest.param(2**64, id="first_integer_beyond_unsigned_64_bits"),
        pytest.param(-(2**63) - 1, id="integer_below_signed_64_bits"),
        pytest.param([{"n": 2**64}], id="nested_large_integer"),
        pytest.param("\ud800", id="lone_surrogate"),
        pytest.param({"k": "\udfff"}, id="nested_lone_surrogate"),
        pytest.param({1: "a"}, id="integer_key"),
        pytest.param({2.5: "a"}, id="float_key"),
        pytest.param({None: "a"}, id="none_key"),
    ],
)
def test_native_dumps_rejects_values_the_stdlib_encodes(value: object) -> None:
    assert isinstance(json.dumps(value), str)
    with pytest.raises(orjson.JSONEncodeError) as caught:
        utility().dumps(value)
    assert isinstance(caught.value, TypeError)


def test_native_dumps_rejects_unsupported_types() -> None:
    with pytest.raises(orjson.JSONEncodeError):
        utility().dumps(object())


def test_native_loads_invokes_orjson_and_not_the_stdlib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Mock(wraps=orjson.loads)
    monkeypatch.setattr(orjson, "loads", spy)
    monkeypatch.setattr(json, "loads", forbidden)
    assert utility().loads(b'{"a": [1, "\xc3\xa9"]}') == {"a": [1, "\u00e9"]}
    spy.assert_called_once()
    assert spy.call_args.args[0] == b'{"a": [1, "\xc3\xa9"]}'


@pytest.mark.parametrize("document", NATIVE_DOCUMENTS)
def test_native_loads_decodes_text_and_binary_inputs_like_the_stdlib(
    document: str,
) -> None:
    expected = repr(json.loads(document))
    assert repr(utility().loads(document)) == expected
    assert repr(utility().loads(document.encode())) == expected
    assert repr(utility().loads(bytearray(document.encode()))) == expected
    assert repr(utility().loads(memoryview(document.encode()))) == expected


def test_native_loads_decodes_integers_beyond_64_bits_as_floats() -> None:
    assert utility().loads(str(2**64 - 1)) == 2**64 - 1
    result = utility().loads(HUGE_TEXT)
    assert type(result) is float
    assert result == float(HUGE)


@pytest.mark.parametrize(
    "document",
    [
        pytest.param("NaN", id="nan_literal"),
        pytest.param("Infinity", id="infinity_literal"),
        pytest.param("-Infinity", id="negative_infinity_literal"),
        pytest.param(r'"\ud800"', id="lone_surrogate_escape"),
        pytest.param(b'"\xed\xa0\x80"', id="encoded_surrogate"),
        pytest.param(b"\xef\xbb\xbf{}", id="utf8_bom"),
        pytest.param("[1]".encode("utf-16"), id="utf16_bytes"),
        pytest.param("[1]".encode("utf-16-le"), id="utf16_little_endian_bytes"),
        pytest.param("[1]".encode("utf-32"), id="utf32_bytes"),
    ],
)
def test_native_loads_rejects_documents_the_stdlib_decodes(document: Text) -> None:
    json.loads(document)
    with pytest.raises(orjson.JSONDecodeError) as caught:
        utility().loads(document)
    assert isinstance(caught.value, json.JSONDecodeError)
    assert isinstance(caught.value, ValueError)


@pytest.mark.parametrize("document", [*MALFORMED_DOCUMENTS, b'"\xff"'])
def test_native_loads_raises_decode_errors_for_malformed_documents(
    document: Text,
) -> None:
    with pytest.raises(orjson.JSONDecodeError):
        utility().loads(document)


@pytest.mark.parametrize("profile", list(PROFILES.values()), ids=list(PROFILES))
@pytest.mark.parametrize("value", list(VALUES.values()), ids=list(VALUES))
def test_dumps_compat_matches_stdlib_output_exactly(
    profile: Profile, value: object
) -> None:
    rendered = profile.compat(value)
    assert type(rendered) is str
    assert rendered == profile.oracle(value)


def test_dumps_compat_never_invokes_orjson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orjson, "dumps", forbidden)
    monkeypatch.setattr(orjson, "loads", forbidden)
    assert utility().dumps_compat({"b": HUGE, "a": 0.1}, sort_keys=True) == (
        '{"a": 0.1, "b": ' + HUGE_TEXT + "}"
    )


def test_dumps_compat_escapes_unicode_and_keeps_stdlib_spacing() -> None:
    value = {"b": "caf\u00e9 \u2603 \U0001f433", "a": [1, 2]}
    assert utility().dumps_compat(value) == (
        r'{"b": "caf\u00e9 \u2603 \ud83d\udc33", "a": [1, 2]}'
    )


def test_dumps_compat_output_is_always_ascii() -> None:
    for name, value in VALUES.items():
        rendered = utility().dumps_compat(value)
        assert rendered.isascii(), name


def test_dumps_compat_accepts_only_keyword_options_it_supports() -> None:
    with pytest.raises(TypeError):
        utility().dumps_compat({"a": 1}, True)
    with pytest.raises(TypeError):
        utility().dumps_compat({"a": 1}, ensure_ascii=False)


def test_dumps_compat_escapes_control_characters_and_delete() -> None:
    assert utility().dumps_compat('a"b\\c\n\t\b\f\r\x01\x7f/') == (
        r'"a\"b\\c\n\t\b\f\r\u0001\u007f/"'
    )


def test_dumps_compat_applies_sorting_separators_and_indent() -> None:
    value = {"b": 1, "a": [1, 2], "c": {}}
    assert utility().dumps_compat(value) == '{"b": 1, "a": [1, 2], "c": {}}'
    assert utility().dumps_compat(value, sort_keys=True) == (
        '{"a": [1, 2], "b": 1, "c": {}}'
    )
    assert utility().dumps_compat(value, sort_keys=True, separators=(",", ":")) == (
        '{"a":[1,2],"b":1,"c":{}}'
    )
    assert utility().dumps_compat(value, indent=2) == (
        '{\n  "b": 1,\n  "a": [\n    1,\n    2\n  ],\n  "c": {}\n}'
    )


def test_dumps_compat_keeps_integers_beyond_64_bits_exact() -> None:
    assert utility().dumps_compat([HUGE, -(2**64), 2**64 - 1]) == (
        "[" + HUGE_TEXT + ", -18446744073709551616, 18446744073709551615]"
    )


def test_dumps_compat_keeps_stdlib_float_spelling() -> None:
    value = [1.0, 0.1, 1e16, 1e22, 1e-6, 1e-7, -0.0, 5e-324]
    assert utility().dumps_compat(value) == (
        "[1.0, 0.1, 1e+16, 1e+22, 1e-06, 1e-07, -0.0, 5e-324]"
    )


def test_dumps_compat_writes_nonfinite_floats_by_default() -> None:
    assert utility().dumps_compat([math.nan, math.inf, -math.inf]) == (
        "[NaN, Infinity, -Infinity]"
    )


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf], ids=repr)
def test_dumps_compat_rejects_nonfinite_floats_when_disallowed(number: float) -> None:
    value = {"a": [number]}
    with pytest.raises(ValueError) as expected:
        json.dumps(value, allow_nan=False)
    with pytest.raises(ValueError) as caught:
        utility().dumps_compat(value, allow_nan=False)
    assert type(caught.value) is type(expected.value)
    assert str(caught.value) == str(expected.value)


def test_dumps_compat_converts_non_string_keys_like_the_stdlib() -> None:
    assert utility().dumps_compat({1: "a", 2.5: "b", None: "c", False: "d"}) == (
        '{"1": "a", "2.5": "b", "null": "c", "false": "d"}'
    )


def test_dumps_compat_keeps_stdlib_errors() -> None:
    circular: list[object] = []
    circular.append(circular)
    for value in (object(), circular, {"a": {1, 2}}):
        assert outcome(utility().dumps_compat, value) == outcome(json.dumps, value)
        assert outcome(utility().dumps_compat, value)[0] == "error"


@pytest.mark.parametrize("document", COMPAT_DOCUMENTS)
def test_loads_compat_matches_stdlib_for_str_bytes_and_bytearray(
    document: str,
) -> None:
    assert outcome(utility().loads_compat, document) == outcome(json.loads, document)
    encoded = document.encode("utf-8", "surrogatepass")
    assert outcome(utility().loads_compat, encoded) == outcome(json.loads, encoded)
    mutable = bytearray(encoded)
    assert outcome(utility().loads_compat, mutable) == outcome(json.loads, mutable)


@pytest.mark.parametrize("encoding", ENCODINGS)
def test_loads_compat_detects_utf8_utf16_and_utf32_bytes(encoding: str) -> None:
    document = '{"k": ["\u00e9", "\u2603", "\U0001f433"]}'.encode(encoding)
    expected = {"k": ["\u00e9", "\u2603", "\U0001f433"]}
    assert utility().loads_compat(document) == expected
    assert outcome(utility().loads_compat, document) == outcome(json.loads, document)


def test_loads_compat_never_invokes_orjson(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orjson, "dumps", forbidden)
    monkeypatch.setattr(orjson, "loads", forbidden)
    assert utility().loads_compat("[" + HUGE_TEXT + ", NaN]")[0] == HUGE


def test_loads_compat_keeps_integers_beyond_64_bits_exact() -> None:
    for number in (HUGE, -HUGE, 2**64, 2**64 - 1):
        result = utility().loads_compat(str(number))
        assert type(result) is int
        assert result == number


def test_loads_compat_keeps_stdlib_float_and_nonfinite_decoding() -> None:
    decoded = utility().loads_compat("[1.5, 1e400, -1e400, NaN, Infinity, -Infinity]")
    assert decoded[:3] == [1.5, math.inf, -math.inf]
    assert math.isnan(decoded[3])
    assert decoded[4:] == [math.inf, -math.inf]
    assert type(utility().loads_compat("-0")) is int
    assert math.copysign(1.0, utility().loads_compat("-0.0")) == -1.0


def test_loads_compat_keeps_surrogate_escapes() -> None:
    assert utility().loads_compat(r'"\ud800"') == "\ud800"
    assert utility().loads_compat(r'"\ud83d\udc33"') == "\U0001f433"
    assert utility().loads_compat(r'{"\udfff": "\ud800"}') == {"\udfff": "\ud800"}


def test_loads_compat_keeps_the_last_duplicate_key() -> None:
    assert utility().loads_compat('{"a": 1, "b": 2, "a": 3}') == {"a": 3, "b": 2}


@pytest.mark.parametrize("document", MALFORMED_DOCUMENTS)
def test_loads_compat_raises_the_stdlib_decode_error(document: str) -> None:
    expected = stdlib_decode_error(document)
    with pytest.raises(json.JSONDecodeError) as caught:
        utility().loads_compat(document)
    actual = caught.value
    assert type(actual) is json.JSONDecodeError
    assert str(actual) == str(expected)
    assert (actual.msg, actual.doc, actual.pos, actual.lineno, actual.colno) == (
        expected.msg,
        expected.doc,
        expected.pos,
        expected.lineno,
        expected.colno,
    )


@pytest.mark.parametrize(
    "document",
    [b'"\xff"', b"\xff\xfe\xfd", "[1]".encode("utf-16")[:-1], b"\x00\x00\x00"],
)
def test_loads_compat_keeps_undecodable_byte_errors(document: bytes) -> None:
    assert outcome(utility().loads_compat, document) == outcome(json.loads, document)
    assert outcome(utility().loads_compat, document)[0] == "error"


def test_utility_exposes_exactly_four_public_functions() -> None:
    public = {
        name
        for name, member in inspect.getmembers(utility(), inspect.isfunction)
        if not name.startswith("_") and member.__module__ == utility().__name__
    }
    assert public == {"dumps", "loads", "dumps_compat", "loads_compat"}


def test_canonical_golden_bytes_for_sorted_compact_ascii_output() -> None:
    value: dict[str, JsonValue] = {
        "b": 1,
        "a": [True, None, "x"],
        "c": {"z": 0, "y": -1},
    }
    assert canonical(value) == '{"a":[true,null,"x"],"b":1,"c":{"y":-1,"z":0}}'


def test_canonical_golden_bytes_escape_unicode_keys_and_values() -> None:
    assert canonical({"k": "\u00e9\u2603\U0001f433", "\u00e9": 1}) == (
        r'{"k":"\u00e9\u2603\ud83d\udc33","\u00e9":1}'
    )


def test_canonical_golden_bytes_escape_controls_and_delete() -> None:
    assert canonical({"s": 'a"b\\c\n\t\b\f\r\x01\x7f/'}) == (
        r'{"s":"a\"b\\c\n\t\b\f\r\u0001\u007f/"}'
    )


def test_canonical_golden_bytes_sort_keys_by_code_point() -> None:
    value: dict[str, JsonValue] = {
        "\u00e9": 1,
        "z": 2,
        "Z": 3,
        "\U0001f433": 4,
        "\uffff": 5,
    }
    assert canonical(value) == (r'{"Z":3,"z":2,"\u00e9":1,"\uffff":5,"\ud83d\udc33":4}')


def test_canonical_golden_bytes_keep_stdlib_float_spelling() -> None:
    value: dict[str, JsonValue] = {
        "f": [1.0, 0.1, 1e16, 1e22, 1e-6, 1e-7, -0.0, 1.5e300]
    }
    assert canonical(value) == ('{"f":[1.0,0.1,1e+16,1e+22,1e-06,1e-07,-0.0,1.5e+300]}')


def test_canonical_golden_bytes_keep_integers_beyond_64_bits_exact() -> None:
    assert canonical({"n": HUGE, "m": -(2**64)}) == (
        '{"m":-18446744073709551616,"n":' + HUGE_TEXT + "}"
    )


def test_canonical_golden_bytes_keep_lone_surrogates_escaped() -> None:
    assert canonical({"s": "\ud800"}) == r'{"s":"\ud800"}'


@pytest.mark.parametrize("number", [math.nan, math.inf, -math.inf], ids=repr)
def test_canonical_rejects_nonfinite_numbers(number: float) -> None:
    with pytest.raises(ValueError):
        canonical({"x": [number]})


def test_canonical_normalizes_only_the_default_runtime_of_a_template() -> None:
    assert canonical({"Runtime": "container", "A": 1}, template=True) == '{"A":1}'
    assert canonical({"Runtime": "container", "A": 1}) == (
        '{"A":1,"Runtime":"container"}'
    )
    assert canonical({"Runtime": "plugin", "A": 1}, template=True) == (
        '{"A":1,"Runtime":"plugin"}'
    )


def service(template: dict[str, object]) -> ServiceWire:
    return ServiceWire.model_validate(
        {
            "ID": "service-a",
            "Version": {"Index": 5},
            "Spec": {
                "Mode": {"Replicated": {"Replicas": 3}},
                "TaskTemplate": template,
            },
        }
    )


FINGERPRINT_TEMPLATE: dict[str, object] = {
    "ContainerSpec": {
        "Image": "caf\u00e9:1",
        "Env": ["A=\u2603", "B=\U0001f433"],
        "Limit": HUGE,
    },
    "Resources": {"Ratio": 0.1, "Big": 1e22, "Tiny": 1e-6, "NegZero": -0.0},
    "Runtime": "container",
    "Placement": {},
    "ForceUpdate": 3,
}

FINGERPRINT_GENERATION = (
    r'{"Mode":{"Replicated":{"Replicas":3}},"TaskTemplate":'
    r'{"ContainerSpec":{"Env":["A=\u2603","B=\ud83d\udc33"],'
    r'"Image":"caf\u00e9:1","Limit":' + HUGE_TEXT + "},"
    r'"ForceUpdate":3,"Placement":{},'
    r'"Resources":{"Big":1e+22,"NegZero":-0.0,"Ratio":0.1,"Tiny":1e-06}}}'
)


def test_fingerprint_hashes_the_exact_canonical_generation_bytes() -> None:
    expected = hashlib.sha256(FINGERPRINT_GENERATION.encode("ascii")).hexdigest()
    assert fingerprint(service(FINGERPRINT_TEMPLATE)) == expected


def test_fingerprint_ignores_the_default_runtime_and_keeps_other_runtimes() -> None:
    plain = {
        key: value for key, value in FINGERPRINT_TEMPLATE.items() if key != "Runtime"
    }
    assert fingerprint(service(FINGERPRINT_TEMPLATE)) == fingerprint(service(plain))
    assert fingerprint(service({**plain, "Runtime": "plugin"})) != fingerprint(
        service(plain)
    )


def desired(
    secrets: tuple[SecretReference, ...], resource_class: ResourceClass
) -> DesiredDescription:
    return DesiredDescription(
        approved_input=InputReference("a" * 64),
        owner="owner-a",
        resource_class=resource_class,
        target="web.1",
        secrets=secrets,
    )


def test_desired_hash_covers_the_exact_canonical_description_bytes() -> None:
    description = desired(
        (
            SecretReference("tls", "version-2"),
            SecretReference("db-password", "version-7"),
        ),
        "persistent",
    )
    golden = (
        r'{"desired":{"approved_input":{"sha256":"' + "a" * 64 + '"},'
        r'"owner":"owner-a","resource_class":"persistent","secrets":['
        r'{"name":"db-password","version":"version-7"},'
        r'{"name":"tls","version":"version-2"}],"target":"web.1"},'
        r'"schema":"pywhales.desired.v1"}'
    )
    digest = hashlib.sha256(golden.encode("ascii")).hexdigest()
    assert description.desired_hash == f"pywhales.desired.v1:sha256:{digest}"


def test_desired_hash_covers_an_empty_secret_set() -> None:
    golden = (
        r'{"desired":{"approved_input":{"sha256":"' + "a" * 64 + '"},'
        r'"owner":"owner-a","resource_class":"rollback-protected","secrets":[],'
        r'"target":"web.1"},"schema":"pywhales.desired.v1"}'
    )
    digest = hashlib.sha256(golden.encode("ascii")).hexdigest()
    assert desired((), "rollback-protected").desired_hash == (
        f"pywhales.desired.v1:sha256:{digest}"
    )
