"""Checks connection settings, ${NAME} placeholders, and parameterized SQL filters."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ["ENV_BASE_PATH"] = str(ROOT)
sys.path.insert(0, str(ROOT))

from dummy_data.init_database import init_database
from sources import SourceError, SqliteSource, XmlSource, open_source, resolve_settings


def setUpModule():
    if not (ROOT / "dummy_data" / "customers.db").exists():
        init_database()


class SettingsTests(unittest.TestCase):
    def test_placeholders_read_from_environment(self):
        with mock.patch.dict(os.environ, {"CRM_HOST": "db.local", "CRM_PORT": "3307"}):
            self.assertEqual(resolve_settings("mysql://${CRM_HOST}:${CRM_PORT}/crm"), "mysql://db.local:3307/crm")

    def test_missing_setting_is_named(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDM_MISSING_PASSWORD", None)
            with self.assertRaisesRegex(SourceError, "XDM_MISSING_PASSWORD not set in .env"):
                open_source({
                    "id": "DB9",
                    "name": "CRM",
                    "type": "Relational",
                    "connection": {"url": "mysql://localhost/crm", "password": "${XDM_MISSING_PASSWORD}"},
                })


class OpenSourceTests(unittest.TestCase):
    def test_relative_sqlite_and_xml_paths(self):
        sqlite = open_source({"type": "Relational", "connection": {"url": "sqlite:///dummy_data/customers.db"}})
        xml = open_source({"type": "XML", "connection": {"path": "dummy_data/purchaseorders.xml"}})
        try:
            self.assertIsInstance(sqlite, SqliteSource)
            self.assertIsInstance(xml, XmlSource)
            self.assertEqual(xml.root.tag, "PurchaseOrders")
        finally:
            sqlite.close()

    def test_missing_connection_and_bad_url_are_explained(self):
        with self.assertRaisesRegex(SourceError, "Database CustomerDB: add <Connection"):
            open_source({"name": "CustomerDB", "type": "Relational", "connection": None})
        with self.assertRaisesRegex(SourceError, "unsupported url"):
            open_source({"name": "X", "type": "Relational", "connection": {"url": "oracle://x"}})
        with self.assertRaisesRegex(SourceError, "SQLite file not found"):
            open_source({"name": "X", "type": "Relational", "connection": {"url": "sqlite:///nope.db"}})


class SqlFilterTests(unittest.TestCase):
    def setUp(self):
        self.source = SqliteSource(ROOT / "dummy_data" / "customers.db")

    def tearDown(self):
        self.source.close()

    def test_value_is_bound_not_pasted(self):
        rows = self.source.select("Customer", ["name"], ("city", "=", "x' OR '1'='1"))
        self.assertEqual(rows, [])

    def test_numeric_filter(self):
        rows = self.source.select("Customer", ["customer_id"], ("customer_id", "<=", 3))
        self.assertEqual(sorted(row["customer_id"] for row in rows), [1, 2, 3])

    def test_unknown_operator_rejected(self):
        with self.assertRaises(SourceError):
            self.source.select("Customer", ["name"], ("city", "; DROP", "x"))


if __name__ == "__main__":
    unittest.main()
