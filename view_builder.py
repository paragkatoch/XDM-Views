"""Build a single view document from a UI selection."""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from query_engine import JOIN_TYPES


CUSTOM_VIEW_NAME = "Custom view"
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_name(value, label: str) -> str:
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise ValueError(f"{label} name '{value}' is not allowed.")
    return value


def build_custom_view_xml(spec: dict) -> str:
    """Turn entities, columns, and joins into the one view the engine runs."""
    if not isinstance(spec, dict):
        raise ValueError("builtView must be an object.")

    entities = spec.get("entities")
    projection = spec.get("projection") or {}
    joins = spec.get("joins") or []
    if not isinstance(entities, list) or not entities:
        raise ValueError("Choose at least one entity.")
    if not isinstance(projection, dict) or not isinstance(joins, list):
        raise ValueError("builtView projection and joins are invalid.")
    if len(entities) > 1 and not joins:
        raise ValueError("Choose a join for the selected entities.")

    view = ET.Element("View", name=CUSTOM_VIEW_NAME)
    projection_el = ET.SubElement(view, "Projection")
    base = ET.SubElement(view, "BaseEntities")

    for entity_name in entities:
        _check_name(entity_name, "Entity")
        columns = projection.get(entity_name)
        if not isinstance(columns, list) or not columns:
            raise ValueError(f"Choose at least one column for {entity_name}.")
        entity_el = ET.SubElement(projection_el, "Entity", name=entity_name)
        for column in columns:
            _check_name(column, "Column")
            ET.SubElement(entity_el, "Attribute").text = column
        ET.SubElement(base, "Entity").text = entity_name

    for join in joins:
        if not isinstance(join, dict):
            raise ValueError("Each join must include a name and type.")
        name = _check_name(join.get("name"), "Relationship")
        join_type = str(join.get("type") or "INNER").strip().upper()
        if join_type not in JOIN_TYPES:
            allowed = ", ".join(JOIN_TYPES)
            raise ValueError(f"Join type '{join_type}' must be one of: {allowed}.")
        ref = ET.SubElement(view, "RelationshipRef", type=join_type)
        ref.text = name

    root = ET.Element("Views")
    root.append(view)
    return ET.tostring(root, encoding="unicode")
