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

from query_engine import MetaSchemaLoader, QueryExecutor, ViewLoader


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
    entity_summaries = []
    for name, entity in metaschema.entities.items():
        entity_summaries.append(
            {
                "name": name,
                "databaseRef": entity["database_ref"],
                "basePath": entity["base_path"],
                "attributes": list(entity["attributes"].keys()),
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
                "relationshipRef": view["relationship_ref"],
                "projection": view["projection"],
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
    password = os.getenv("ENV_PASSWORD", "")

    return {
        "type": "MySQL",
        "host": host,
        "user": user,
        "database": database,
        "passwordConfigured": "yes" if password else "no",
        "display": f"{user}@{host}/{database}",
    }


def execute_payload(
    metaschema_xml: str,
    views_xml: str,
    view_name: str,
) -> Dict[str, Any]:
    metaschema, views = load_from_xml_strings(metaschema_xml, views_xml)
    mysql_source = get_mysql_source_details()
    started_at = time.perf_counter()
    executor = QueryExecutor(
        metaschema,
        views,
        mysql_source["display"],
        str(DEFAULT_XML_DATA_PATH),
    )

    try:
        rows = executor.execute_view(view_name)
    finally:
        executor.close()

    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)
    return {
        "viewName": view_name,
        "rowCount": len(rows),
        "elapsedMs": elapsed_ms,
        "columns": collect_columns(rows),
        "rows": rows,
    }


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
                    "relationalType": mysql_source["type"],
                    "relationalHost": mysql_source["host"],
                    "relationalUser": mysql_source["user"],
                    "relationalDatabase": mysql_source["database"],
                    "passwordConfigured": mysql_source["passwordConfigured"],
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

        if self.path == "/api/execute":
            view_name = (payload.get("viewName") or "").strip()
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

            try:
                self._send_json(execute_payload(metaschema_xml, views_xml, view_name))
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

    def _send_json(
        self,
        payload: Dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

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
