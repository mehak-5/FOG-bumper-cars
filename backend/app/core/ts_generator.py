"""Generates TypeScript types from the Pydantic wire models (no third-party generator)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.app.core.models import CLIENT_MESSAGES, SERVER_MESSAGES, WIRE_MODELS

HEADER = """\
// AUTO-GENERATED from backend/app/core/models.py by tools/gen_ts_types.py - DO NOT EDIT.
// Run `python tools/gen_ts_types.py` after changing the Pydantic models.
"""


def _literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def ts_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return _literal(schema["const"])
    if "enum" in schema:
        return " | ".join(_literal(v) for v in schema["enum"])
    if "anyOf" in schema or "oneOf" in schema:
        parts = [ts_type(s) for s in schema.get("anyOf", schema.get("oneOf", []))]
        return " | ".join(dict.fromkeys(parts))
    t = schema.get("type")
    if t == "string":
        return "string"
    if t in ("integer", "number"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        item = ts_type(schema.get("items", {}))
        return f"({item})[]" if "|" in item else f"{item}[]"
    if t == "object" or "properties" in schema or "additionalProperties" in schema:
        extra = schema.get("additionalProperties")
        if extra and "properties" not in schema:
            return f"Record<string, {ts_type(extra)}>"
        if "properties" not in schema:
            return "Record<string, unknown>"
        return _object_body(schema, inline=True)
    return "unknown"


def _object_body(schema: dict[str, Any], *, inline: bool = False) -> str:
    required = set(schema.get("required", []))
    props = schema.get("properties", {})
    lines = [
        f"  {name}{'' if name in required else '?'}: {ts_type(sub)};" for name, sub in props.items()
    ]
    if inline:
        return "{ " + " ".join(line.strip() for line in lines) + " }"
    return "{\n" + "\n".join(lines) + ("\n" if lines else "") + "}"


def _collect_defs() -> dict[str, dict[str, Any]]:
    defs: dict[str, dict[str, Any]] = {}
    for model in WIRE_MODELS:
        schema = model.model_json_schema(mode="serialization")
        for name, sub in schema.pop("$defs", {}).items():
            defs[name] = sub
        schema.pop("title", None)
        defs[model.__name__] = schema
    return defs


def _emit_interface(name: str, schema: dict[str, Any]) -> str:
    if "properties" in schema or schema.get("type") == "object":
        return f"export interface {name} {_object_body(schema)}\n"
    return f"export type {name} = {ts_type(schema)};\n"


def _emit_map(name: str, messages: dict[str, type[BaseModel]]) -> str:
    rows = "\n".join(f'  "{k}": {m.__name__};' for k, m in messages.items())
    return f"export interface {name} {{\n{rows}\n}}\n"


def generate() -> str:
    defs = _collect_defs()
    out = [HEADER]
    # Literal aliases first so interfaces read top-down.
    for name in sorted(defs):
        out.append(_emit_interface(name, defs[name]))
    out.append(_emit_map("ClientPayloads", CLIENT_MESSAGES))
    out.append(_emit_map("ServerPayloads", SERVER_MESSAGES))
    out.append(
        """\
export interface Envelope<T extends string, P> {
  type: T;
  id: string;
  ts: string;
  payload: P;
}

export type ClientMessageType = keyof ClientPayloads;
export type ServerMessageType = keyof ServerPayloads;
export type ClientMessage = {
  [K in ClientMessageType]: Envelope<K, ClientPayloads[K]>;
}[ClientMessageType];
export type ServerMessage = {
  [K in ServerMessageType]: Envelope<K, ServerPayloads[K]>;
}[ServerMessageType];
"""
    )
    return "\n".join(out)
