"""Shared JSON object shape for Docker metadata."""

from pydantic import JsonValue

type JsonObject = dict[str, JsonValue]
