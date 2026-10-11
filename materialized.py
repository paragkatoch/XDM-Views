"""
Materialized views: store a view's full result as a SQLite table and expose it
as a relational entity in MetaSchema.xml.

Tables live in their own SQLite file, declared in MetaSchema.xml as the "MV"
database, so the source databases are never written.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sources import resolve_path, resolve_settings

MATERIALIZED_DATABASE_ID = "MV"
MATERIALIZED_DATABASE_NAME = "MaterializedDB"
CATALOG_TABLE = "_materialized_views"
DEFAULT_CONNECTION_URL = "sqlite:///dummy_data/materialized.db"

SQL_RESERVED = {
    "all", "and", "as", "asc", "by", "case", "check", "column", "create", "default",
    "delete", "desc", "distinct", "drop", "else", "end", "from", "group", "having",
    "in", "index", "insert", "into", "is", "join", "key", "limit", "not", "null",
    "offset", "on", "or", "order", "primary", "references", "select", "set", "table",
    "then", "to", "union", "unique", "update", "values", "when", "where",
}


def entity_name_for_view(view_name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", view_name)
    pascal = "".join(word[:1].upper() + word[1:] for word in words)
    if not pascal:
        raise ValueError("The view name needs at least one letter or digit.")
    if pascal[0].isdigit():
        pascal = f"View{pascal}"
    return f"{pascal}MV"


def column_names(columns: List[str]) -> List[str]:
    names: List[str] = []
    for column in columns:
        name = re.sub(r"\W+", "_", column).strip("_").lower() or "column"
        if name[0].isdigit():
            name = f"c_{name}"
        if name in SQL_RESERVED:
            name = f"{name}_value"
        candidate = name
        suffix = 2
        while candidate in names:
            candidate = f"{name}_{suffix}"
            suffix += 1
        names.append(candidate)
    return names


def column_type(values: List[Any]) -> str:
    present = [value for value in values if value is not None]
    if present and all(isinstance(value, int) and not isinstance(value, bool) for value in present):
        return "int"
    if present and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in present):
        return "float"
    return "string"


def storable(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


SQLITE_TYPES = {"int": "INTEGER", "float": "REAL", "string": "TEXT"}


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS "{CATALOG_TABLE}" ('
        "entity TEXT PRIMARY KEY, view_name TEXT NOT NULL, "
        "refreshed_at TEXT NOT NULL, row_count INTEGER NOT NULL)"
    )
    return conn


def store_rows(
    db_path: Path,
    entity: str,
    view_name: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not columns:
        raise ValueError(f"View '{view_name}' returned no columns to store.")

    names = column_names(columns)
    types = [column_type([row.get(column) for row in rows]) for column in columns]
    refreshed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(f'DROP TABLE IF EXISTS "{entity}"')
            definitions = ", ".join(
                f'"{name}" {SQLITE_TYPES[kind]}' for name, kind in zip(names, types)
            )
            conn.execute(f'CREATE TABLE "{entity}" ({definitions})')
            placeholders = ", ".join("?" for _ in names)
            conn.executemany(
                f'INSERT INTO "{entity}" VALUES ({placeholders})',
                [[storable(row.get(column)) for column in columns] for row in rows],
            )
            conn.execute(
                f'INSERT OR REPLACE INTO "{CATALOG_TABLE}" VALUES (?, ?, ?, ?)',
                (entity, view_name, refreshed_at, len(rows)),
            )
    finally:
        conn.close()

    return {
        "entity": entity,
        "viewName": view_name,
        "refreshedAt": refreshed_at,
        "rowCount": len(rows),
        "attributes": [
            {"name": name, "type": kind, "source": column}
            for name, kind, column in zip(names, types, columns)
        ],
    }


def drop_table(db_path: Path, entity: str) -> None:
    if not db_path.exists():
        return
    conn = _connect(db_path)
    try:
        with conn:
            conn.execute(f'DROP TABLE IF EXISTS "{entity}"')
            conn.execute(f'DELETE FROM "{CATALOG_TABLE}" WHERE entity = ?', (entity,))
    finally:
        conn.close()


def catalog(db_path: Path) -> Dict[str, Dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f'SELECT entity, view_name, refreshed_at, row_count FROM "{CATALOG_TABLE}"'
        ).fetchall()
    finally:
        conn.close()
    return {
        entity: {"viewName": view_name, "refreshedAt": refreshed_at, "rowCount": row_count}
        for entity, view_name, refreshed_at, row_count in rows
    }


def database_path(databases: Dict[str, Dict[str, Any]]) -> Path:
    connection = (databases.get(MATERIALIZED_DATABASE_ID) or {}).get("connection") or {}
    url = resolve_settings(connection.get("url")) or DEFAULT_CONNECTION_URL
    if not url.startswith("sqlite:///"):
        raise ValueError("Materialized views need a sqlite:/// connection on the MV database.")
    return resolve_path(url[len("sqlite:///"):])


def _xml_attr(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def _entity_pattern(entity: str) -> re.Pattern:
    return re.compile(
        rf'[ \t]*<Entity\s+name="{re.escape(_xml_attr(entity))}"[^>]*>[\s\S]*?</Entity>\n?'
    )


def _with_materialized_database(metaschema_xml: str) -> str:
    connection = f'      <Connection url="{DEFAULT_CONNECTION_URL}"/>\n'
    database = re.search(
        rf'<Database\s+id="{MATERIALIZED_DATABASE_ID}"[^>]*>[\s\S]*?</Database>', metaschema_xml
    )
    if database:
        if "<Connection" in database.group(0):
            return metaschema_xml
        block = re.sub(r"([ \t]*)</Database>$", lambda m: f"{connection}{m.group(1)}</Database>", database.group(0))
        return metaschema_xml[: database.start()] + block + metaschema_xml[database.end():]

    if "</Databases>" not in metaschema_xml:
        raise ValueError("MetaSchema XML has no <Databases> section.")
    database_block = (
        f'    <Database id="{MATERIALIZED_DATABASE_ID}">\n'
        f"      <Name>{MATERIALIZED_DATABASE_NAME}</Name>\n"
        "      <Type>Relational</Type>\n"
        f"{connection}"
        "    </Database>\n"
    )
    return re.sub(r"([ \t]*)</Databases>", lambda m: f"\n{database_block}{m.group(1)}</Databases>", metaschema_xml, count=1)


def metaschema_with_entity(metaschema_xml: str, entity: str, attributes: List[Dict[str, Any]]) -> str:
    xml = _with_materialized_database(metaschema_xml)

    lines = "\n".join(
        f'        <Attribute name="{_xml_attr(attr["name"])}" type="{attr["type"]}"/>'
        for attr in attributes
    )
    entity_block = (
        f'    <Entity name="{_xml_attr(entity)}">\n'
        f"      <DatabaseRef>{MATERIALIZED_DATABASE_ID}</DatabaseRef>\n"
        "      <Attributes>\n"
        f"{lines}\n"
        "      </Attributes>\n"
        "    </Entity>\n"
    )

    pattern = _entity_pattern(entity)
    if pattern.search(xml):
        return pattern.sub(lambda _: entity_block, xml, count=1)
    if "</Entities>" not in xml:
        raise ValueError("MetaSchema XML has no <Entities> section.")
    return re.sub(r"([ \t]*)</Entities>", lambda m: f"\n{entity_block}\n{m.group(1)}</Entities>", xml, count=1)


def metaschema_without_entity(metaschema_xml: str, entity: str) -> str:
    return _entity_pattern(entity).sub("", metaschema_xml, count=1)
