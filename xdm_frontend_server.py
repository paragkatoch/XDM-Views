#!/usr/bin/env python3
"""
Lightweight API server for the XDM query engine frontend.

This keeps the original query_engine.py untouched while exposing:
- bundled metaschema/views bootstrap data
- metadata inspection for edited XML
- query execution for a selected logical view
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import traceback
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple

import materialized
from exporters import EXPORT_FORMATS, build_native_document, render_export
from query_engine import JOIN_TYPES, MetaSchemaLoader, QueryExecutor, ViewLoader
from sources import SourceError, open_source
from view_builder import CUSTOM_VIEW_NAME, build_custom_view_xml


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_METASCHEMA_PATH = BASE_DIR / "MetaSchema.xml"
DEFAULT_VIEWS_PATH = BASE_DIR / "views" / "views.xml"
DEFAULT_XML_DATA_PATH = BASE_DIR / "dummy_data" / "purchaseorders.xml"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_temp_xml(temp_dir: str, name: str, content: str) -> str:
    path = Path(temp_dir) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def serialize_loader_state(
    metaschema: MetaSchemaLoader,
    views: ViewLoader,
) -> Dict[str, Any]:
    try:
        materialized_entities = materialized.catalog(materialized.database_path(metaschema.databases))
    except ValueError:
        materialized_entities = {}
    entity_summaries = []
    for name, entity in metaschema.entities.items():
        database = metaschema.databases.get(entity["database_ref"]) or {}
        entity_summaries.append(
            {
                "name": name,
                "databaseRef": entity["database_ref"],
                "databaseName": database.get("name"),
                "sourceType": database.get("type"),
                "basePath": entity["base_path"],
                "attributes": list(entity["attributes"].keys()),
                "attributeDetails": [
                    {
                        "name": attr["name"],
                        "type": attr.get("type"),
                        "key": attr.get("key"),
                        "path": attr.get("path"),
                    }
                    for attr in entity["attributes"].values()
                ],
                "materialized": materialized_entities.get(name),
            }
        )

    view_summaries = []
    for name, view in views.views.items():
        filter_summaries = []
        for filter_value in view["filters"].values():
            filter_summaries.append(asdict(filter_value))

        view_summaries.append(
            {
                "name": name,
                "baseEntities": view["base_entities"],
                "relationshipRefs": view.get("relationship_refs") or [],
                "projection": view["projection"],
                "parameters": view.get("parameters") or [],
                "groupBy": view.get("groupBy") or [],
                "measures": view.get("measures") or [],
                "having": view.get("having") or [],
                "orderBy": view.get("orderBy") or [],
                "limit": view.get("limit"),
                "offset": view.get("offset") or 0,
                "filters": filter_summaries,
            }
        )

    return {
        "counts": {
            "databases": len(metaschema.databases),
            "entities": len(metaschema.entities),
            "relationships": len(metaschema.relationships),
            "views": len(views.views),
        },
        "databases": list(metaschema.databases.values()),
        "entities": entity_summaries,
        "relationships": list(metaschema.relationships.values()),
        "views": view_summaries,
        "viewNames": sorted(views.views.keys()),
    }


def load_from_xml_strings(
    metaschema_xml: str,
    views_xml: str,
) -> Tuple[MetaSchemaLoader, ViewLoader]:
    with tempfile.TemporaryDirectory(prefix="xdm_frontend_") as temp_dir:
        metaschema_path = write_temp_xml(temp_dir, "MetaSchema.xml", metaschema_xml)
        views_dir = Path(temp_dir) / "views"
        views_dir.mkdir(parents=True, exist_ok=True)
        views_path = write_temp_xml(str(views_dir), "views.xml", views_xml)
        metaschema = MetaSchemaLoader(metaschema_path)
        views = ViewLoader(views_path)
        return metaschema, views


def inspect_payload(metaschema_xml: str, views_xml: str) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    return serialize_loader_state(metaschema, views)


def save_views_payload(metaschema_xml: str, views_xml: str) -> Dict[str, Any]:
    summary = inspect_payload(metaschema_xml, views_xml)
    DEFAULT_VIEWS_PATH.write_text(views_xml.rstrip() + "\n", encoding="utf-8")
    return summary


def test_connection_payload(metaschema_xml: str, database_id: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="xdm_frontend_") as temp_dir:
        metaschema = MetaSchemaLoader(write_temp_xml(temp_dir, "MetaSchema.xml", metaschema_xml))
    database = metaschema.databases.get(database_id)
    if database is None:
        raise ValueError(f"Database '{database_id}' is not declared in the MetaSchema.")

    try:
        source = open_source(database, str(DEFAULT_XML_DATA_PATH))
    except SourceError as exc:
        return {"ok": False, "message": str(exc)}
    try:
        return {"ok": True, "message": source.check()}
    except Exception as exc:
        return {"ok": False, "message": f"Opened {source.label} but the check failed: {exc}"}
    finally:
        source.close()


def relationship_problems(metaschema: MetaSchemaLoader) -> List[str]:
    problems = []
    for name, relationship in metaschema.relationships.items():
        condition = relationship.get("condition") or {}
        sides = [
            (relationship.get("left_entity"), condition.get("left_attribute")),
            (relationship.get("right_entity"), condition.get("right_attribute")),
        ]
        for entity, attribute in sides:
            if entity not in metaschema.entities:
                problems.append(f"{name}: entity '{entity}' does not exist.")
            elif attribute not in metaschema.entities[entity]["attributes"]:
                problems.append(f"{name}: '{entity}' has no column '{attribute}'.")
    return problems


def save_metaschema_payload(metaschema_xml: str, views_xml: str) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    problems = relationship_problems(metaschema)
    missing = sorted(
        f"{view_name} uses '{ref['name']}'"
        for view_name, view in views.views.items()
        for ref in view.get("relationship_refs") or []
        if ref["name"] not in metaschema.relationships
    )
    if missing:
        problems.append(f"Relationships used by views are missing: {'; '.join(missing)}.")
    if problems:
        raise ValueError(" ".join(problems))

    summary = serialize_loader_state(metaschema, views)
    DEFAULT_METASCHEMA_PATH.write_text(metaschema_xml.rstrip() + "\n", encoding="utf-8")
    return summary


def materialize_payload(
    metaschema_xml: str,
    views_xml: str,
    view_name: str,
    parameters: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    if view_name not in views.views:
        raise ValueError(f"View '{view_name}' does not exist.")

    entity = materialized.entity_name_for_view(view_name)
    db_path = materialized.database_path(metaschema.databases)
    known = materialized.catalog(db_path)
    if entity in metaschema.entities and entity not in known:
        raise ValueError(f"Entity '{entity}' already exists and is not a materialized view.")

    executor = QueryExecutor(metaschema, views, str(DEFAULT_XML_DATA_PATH))
    try:
        report = executor.execute_view_report(view_name, parameters, None, {"limit": None, "offset": 0})
    finally:
        executor.close()

    rows = report["rows"]
    stored = materialized.store_rows(
        db_path, entity, view_name, collect_columns(rows), rows
    )
    next_metaschema = materialized.metaschema_with_entity(metaschema_xml, entity, stored["attributes"])
    summary = inspect_payload(next_metaschema, views_xml)
    DEFAULT_METASCHEMA_PATH.write_text(next_metaschema.rstrip() + "\n", encoding="utf-8")
    return {"metaschemaXml": next_metaschema, "summary": summary, "materialized": stored}


def drop_materialized_payload(metaschema_xml: str, views_xml: str, entity: str) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    db_path = materialized.database_path(metaschema.databases)
    if entity not in materialized.catalog(db_path):
        raise ValueError(f"'{entity}' is not a materialized view.")

    users = sorted(name for name, view in views.views.items() if entity in view["base_entities"])
    if users:
        raise ValueError(f"Remove '{entity}' from these views first: {', '.join(users)}.")

    next_metaschema = materialized.metaschema_without_entity(metaschema_xml, entity)
    summary = inspect_payload(next_metaschema, views_xml)
    materialized.drop_table(db_path, entity)
    DEFAULT_METASCHEMA_PATH.write_text(next_metaschema.rstrip() + "\n", encoding="utf-8")
    return {"metaschemaXml": next_metaschema, "summary": summary}


def get_mysql_source_details() -> Dict[str, str]:
    required_keys = ["ENV_HOST", "ENV_USER", "ENV_DATABASE"]
    missing = [key for key in required_keys if not os.getenv(key)]
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            f"Missing required database settings in .env: {missing_text}"
        )

    host = os.getenv("ENV_HOST", "")
    user = os.getenv("ENV_USER", "")
    database = os.getenv("ENV_DATABASE", "")

    return {
        "display": f"{user}@{host}/{database}",
    }


def execute_payload(
    metaschema_xml: str,
    views_xml: str,
    view_name: str,
    export_format: str = "",
    parameters: Dict[str, Any] | None = None,
    join_types: List[str] | None = None,
    shape: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    started_at = time.perf_counter()
    executor = QueryExecutor(
        metaschema,
        views,
        str(DEFAULT_XML_DATA_PATH),
    )

    try:
        report = executor.execute_view_report(view_name, parameters, join_types, shape)
    finally:
        executor.close()

    rows = report["rows"]
    columns = collect_columns(rows)
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    native = build_native_document(view_name, report["sources"], columns, rows)
    if export_format:
        rendered = render_export(export_format, view_name, columns, rows, native)
        rendered["elapsedMs"] = elapsed_ms
        return rendered

    return {
        "kind": "json",
        "body": {
            "viewName": view_name,
            "rowCount": len(rows),
            "totalCount": report["totalCount"],
            "limit": report["limit"],
            "offset": report["offset"],
            "elapsedMs": elapsed_ms,
            "columns": columns,
            "rows": rows,
            "parameters": report["parameters"],
            "joins": report["joins"],
            "native": native,
        },
    }


def export_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    export_format = (payload.get("format") or "").strip().lower()
    if export_format not in EXPORT_FORMATS:
        allowed = ", ".join(EXPORT_FORMATS)
        raise ValueError(f"format must be one of: {allowed}")

    view_name = (payload.get("viewName") or "").strip()
    columns = payload.get("columns")
    rows = payload.get("rows")
    if not view_name or not isinstance(columns, list) or not isinstance(rows, list):
        raise ValueError("viewName, columns, and rows are required.")

    native = payload.get("native")
    if export_format == "native" and not isinstance(native, dict):
        raise ValueError("native export requires the native document from the query result.")

    return render_export(export_format, view_name, columns, rows, native)


def collect_columns(rows: List[Dict[str, Any]]) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                ordered.append(key)
                seen.add(key)
    return ordered


class XDMRequestHandler(BaseHTTPRequestHandler):
    server_version = "XDMFrontendHTTP/1.0"

    def do_OPTIONS(self) -> None:
        self._send_empty_response(HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        if self.path == "/api/bootstrap":
            mysql_source = get_mysql_source_details()
            payload = {
                "metaschemaXml": read_text(DEFAULT_METASCHEMA_PATH),
                "viewsXml": read_text(DEFAULT_VIEWS_PATH),
                "dataSources": {
                    "relationalSource": mysql_source["display"],
                    "xmlPath": str(DEFAULT_XML_DATA_PATH),
                },
            }
            payload["summary"] = inspect_payload(
                payload["metaschemaXml"],
                payload["viewsXml"],
            )
            self._send_json(payload)
            return

        if self.path == "/api/health":
            self._send_json({"ok": True})
            return

        self._send_json(
            {"error": "Not found", "path": self.path},
            status=HTTPStatus.NOT_FOUND,
        )

    def do_POST(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        metaschema_xml = (payload.get("metaschemaXml") or "").strip()
        views_xml = (payload.get("viewsXml") or "").strip()

        if self.path == "/api/inspect":
            if not metaschema_xml or not views_xml:
                self._send_json(
                    {"error": "Both metaschemaXml and viewsXml are required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                self._send_json(inspect_payload(metaschema_xml, views_xml))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path == "/api/save-views":
            if not metaschema_xml or not views_xml:
                self._send_json(
                    {"error": "Both metaschemaXml and viewsXml are required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                self._send_json(save_views_payload(metaschema_xml, views_xml))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path == "/api/test-connection":
            database_id = (payload.get("databaseId") or "").strip()
            if not metaschema_xml or not database_id:
                self._send_json(
                    {"error": "metaschemaXml and databaseId are required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                self._send_json(test_connection_payload(metaschema_xml, database_id))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path == "/api/save-metaschema":
            if not metaschema_xml or not views_xml:
                self._send_json(
                    {"error": "Both metaschemaXml and viewsXml are required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                self._send_json(save_metaschema_payload(metaschema_xml, views_xml))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path in ("/api/materialize", "/api/materialize/drop"):
            if not metaschema_xml or not views_xml:
                self._send_json(
                    {"error": "Both metaschemaXml and viewsXml are required."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            try:
                if self.path == "/api/materialize":
                    view_name = (payload.get("viewName") or "").strip()
                    parameters = payload.get("parameters") or {}
                    if not view_name or not isinstance(parameters, dict):
                        raise ValueError("viewName is required and parameters must be an object.")
                    self._send_json(materialize_payload(metaschema_xml, views_xml, view_name, parameters))
                else:
                    entity = (payload.get("entity") or "").strip()
                    if not entity:
                        raise ValueError("entity is required.")
                    self._send_json(drop_materialized_payload(metaschema_xml, views_xml, entity))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path == "/api/execute":
            view_name = (payload.get("viewName") or "").strip()
            export_format = (payload.get("format") or "").strip().lower()
            built_view = payload.get("builtView")
            if built_view:
                try:
                    views_xml = build_custom_view_xml(built_view)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                view_name = CUSTOM_VIEW_NAME
            if not metaschema_xml or not views_xml or not view_name:
                self._send_json(
                    {
                        "error": (
                            "metaschemaXml, viewsXml, and viewName are all required."
                        )
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            if export_format and export_format not in EXPORT_FORMATS:
                allowed = ", ".join(EXPORT_FORMATS)
                self._send_json(
                    {"error": f"format must be one of: {allowed}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return

            parameters = payload.get("parameters") or {}
            if not isinstance(parameters, dict):
                self._send_json(
                    {"error": "parameters must be an object of name to value."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            join_types = payload.get("joinTypes") or []
            if not isinstance(join_types, list):
                self._send_json(
                    {"error": f"joinTypes must be a list of {', '.join(JOIN_TYPES)}."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            shape = {
                key: payload[key]
                for key in ("groupBy", "measures", "having", "orderBy", "limit", "offset")
                if key in payload
            }

            try:
                self._send_rendered(
                    execute_payload(
                        metaschema_xml,
                        views_xml,
                        view_name,
                        export_format,
                        parameters,
                        join_types,
                        shape,
                    )
                )
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        if self.path == "/api/export":
            try:
                self._send_rendered(export_payload(payload))
            except Exception as exc:  # pragma: no cover - surfaced to UI
                self._send_exception(exc)
            return

        self._send_json(
            {"error": "Not found", "path": self.path},
            status=HTTPStatus.NOT_FOUND,
        )

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json_body(self) -> Dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0

        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"

        try:
            return json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            self._send_json(
                {"error": "Request body must be valid JSON."},
                status=HTTPStatus.BAD_REQUEST,
            )
            return None

    def _send_empty_response(self, status: HTTPStatus) -> None:
        self.send_response(status)
        self._send_cors_headers()
        self.end_headers()

    def _send_rendered(self, rendered: Dict[str, Any]) -> None:
        if rendered.get("kind") == "file":
            self._send_bytes(
                rendered["body"],
                rendered["contentType"],
                rendered.get("filename"),
            )
            return

        self._send_json(
            rendered["body"],
            filename=rendered.get("filename") if rendered.get("attachment") else None,
        )

    def _send_json(
        self,
        payload: Dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
        filename: str | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str, indent=2 if filename else None).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str, filename: str | None = None) -> None:
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Expose-Headers", "Content-Disposition")

    def _send_exception(self, exc: Exception) -> None:
        self._send_json(
            {
                "error": str(exc),
                "details": traceback.format_exc(limit=8),
            },
            status=HTTPStatus.BAD_REQUEST,
        )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the XDM frontend API server.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", default=8000, type=int, help="Bind port")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    os.chdir(BASE_DIR)
    server = ThreadingHTTPServer((args.host, args.port), XDMRequestHandler)
    print(f"XDM frontend API running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
