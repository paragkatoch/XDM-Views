"""
Data sources declared in MetaSchema.xml.

Each <Database> can carry a <Connection>. Values may reference settings with
${NAME}; they are read from the environment (and .env), so secrets stay out of
the XML. Relative file paths resolve against ENV_BASE_PATH, or the working
directory when it is not set.

    <Connection url="sqlite:///dummy_data/customers.db"/>
    <Connection url="mysql://${CRM_HOST}:3306/crm" username="${CRM_USER}" password="${CRM_PASSWORD}"/>
    <Connection path="dummy_data/purchaseorders.xml"/>
"""

from __future__ import annotations

import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SQL_OPERATORS = ("=", "!=", "<>", ">", "<", ">=", "<=")


class SourceError(ValueError):
    pass


def resolve_settings(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    missing = [name for name in PLACEHOLDER.findall(value) if os.getenv(name) is None]
    if missing:
        raise SourceError(f"{', '.join(sorted(set(missing)))} not set in .env.")
    return PLACEHOLDER.sub(lambda match: os.environ[match.group(1)], value)


def base_dir() -> Path:
    return Path(os.getenv("ENV_BASE_PATH") or os.getcwd())


def resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else base_dir() / path


class RelationalSource:
    placeholder = "?"
    kind = "Relational"

    def __init__(self, connection: Any, label: str):
        self.connection = connection
        self.label = label

    def quote(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def select(
        self,
        table: str,
        columns: List[str],
        condition: Optional[Tuple[str, str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        sql = f"SELECT {', '.join(self.quote(column) for column in columns)} FROM {self.quote(table)}"
        params: List[Any] = []
        if condition:
            attribute, operator, value = condition
            if operator not in SQL_OPERATORS:
                raise SourceError(f"Filter operator '{operator}' is not supported.")
            sql += f" WHERE {self.quote(attribute)} {operator} {self.placeholder}"
            params.append(value)

        print(f"[SQL Query] {sql} {params if params else ''}".rstrip())
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            names = [description[0] for description in cursor.description]
            return [dict(zip(names, row)) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def check(self) -> str:
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.fetchall()
        finally:
            cursor.close()
        return f"Connected to {self.label}"

    def close(self) -> None:
        self.connection.close()


class SqliteSource(RelationalSource):
    def __init__(self, path: Path):
        if not path.exists():
            raise SourceError(f"SQLite file not found: {path}")
        super().__init__(sqlite3.connect(path, check_same_thread=False), f"SQLite {path.name}")


class MySQLSource(RelationalSource):
    placeholder = "%s"

    def __init__(self, url: str, username: Optional[str], password: Optional[str]):
        try:
            import mysql.connector
        except ImportError as exc:
            raise SourceError("Install mysql-connector-python to use MySQL sources.") from exc

        parts = urlsplit(url)
        database = parts.path.lstrip("/")
        if not parts.hostname or not database:
            raise SourceError("MySQL url must look like mysql://host:3306/database.")
        connection = mysql.connector.connect(
            host=parts.hostname,
            port=parts.port or 3306,
            database=database,
            user=username or unquote(parts.username or ""),
            password=password if password is not None else unquote(parts.password or ""),
        )
        super().__init__(connection, f"MySQL {parts.hostname}/{database}")

    def quote(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"


class XmlSource:
    kind = "XML"

    def __init__(self, path: Path):
        if not path.exists():
            raise SourceError(f"XML file not found: {path}")
        self.root = ET.parse(path).getroot()
        self.label = f"XML {path.name}"

    def check(self) -> str:
        return f"Read {self.label}: {len(self.root)} records under <{self.root.tag}>"

    def close(self) -> None:
        return None


def open_source(database: Dict[str, Any], fallback_xml_path: Optional[str] = None):
    name = database.get("name") or database.get("id")
    connection = database.get("connection") or {}
    db_type = database.get("type")

    missing = sorted({
        setting
        for value in connection.values()
        for setting in PLACEHOLDER.findall(value or "")
        if os.getenv(setting) is None
    })
    if missing:
        raise SourceError(f"Database {name}: {', '.join(missing)} not set in .env.")

    try:
        if db_type == "XML":
            raw_path = resolve_settings(connection.get("path")) or fallback_xml_path
            if not raw_path:
                raise SourceError("add <Connection path=\"...\"/>.")
            return XmlSource(resolve_path(raw_path))

        if db_type == "Relational":
            url = resolve_settings(connection.get("url"))
            if not url:
                raise SourceError("add <Connection url=\"sqlite:///file.db\"/> or a mysql:// url.")
            if url.startswith("sqlite:///"):
                return SqliteSource(resolve_path(url[len("sqlite:///"):]))
            if url.startswith("mysql://"):
                return MySQLSource(
                    url,
                    resolve_settings(connection.get("username")),
                    resolve_settings(connection.get("password")),
                )
            raise SourceError(f"unsupported url '{url}'. Use sqlite:/// or mysql://.")
    except SourceError as exc:
        raise SourceError(f"Database {name}: {exc}") from exc
    except Exception as exc:
        raise SourceError(f"Database {name}: could not connect ({exc}).") from exc

    raise SourceError(f"Database {name}: unsupported type '{db_type}'.")
