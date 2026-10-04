"""Checks for join types, aggregation, and export using the sample data."""

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["ENV_BASE_PATH"] = str(ROOT)
sys.path.insert(0, str(ROOT))

from dummy_data.init_database import init_database
from exporters import build_native_document, render_export
from query_engine import MetaSchemaLoader, QueryExecutor, ViewLoader
from view_builder import CUSTOM_VIEW_NAME, build_custom_view_xml


def ensure_sample_database():
    database = ROOT / "dummy_data" / "customers.db"
    if not database.exists():
        init_database()


class QueryEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_sample_database()
        cls.executor = QueryExecutor(
            MetaSchemaLoader(str(ROOT / "MetaSchema.xml")),
            ViewLoader(str(ROOT / "views" / "views.xml")),
            str(ROOT / "dummy_data" / "purchaseorders.xml"),
        )

    @classmethod
    def tearDownClass(cls):
        cls.executor.close()

    def test_left_join_keeps_customers_without_loyalty(self):
        rows = self.executor.execute_view_report("Customers with loyalty")["rows"]
        by_id = {row["Customer.customer_id"]: row for row in rows}

        self.assertEqual(len(rows), 10)
        self.assertIsNone(by_id[3]["membership_tier"])
        self.assertEqual(by_id[1]["membership_tier"], "Gold")
        self.assertEqual(by_id[1]["reward_points"], 4200)

    def test_semi_join_returns_only_matching_customers(self):
        rows = self.executor.execute_view_report("Customers who have loyalty")["rows"]
        ids = [row["customer_id"] for row in rows]

        self.assertEqual(ids, [1, 2, 4, 5, 7, 8, 10])
        self.assertNotIn("membership_tier", rows[0])

    def test_anti_join_returns_customers_with_no_loyalty(self):
        rows = self.executor.execute_view_report("Customers without loyalty")["rows"]
        names = [row["name"] for row in rows]

        self.assertEqual(names, ["Charlie Brown", "Frank Miller", "Isaac Newton"])

    def test_join_type_can_be_overridden_to_inner(self):
        rows = self.executor.execute_view_report(
            "Customers with loyalty",
            join_types=["INNER"],
        )["rows"]

        self.assertEqual(len(rows), 7)
        self.assertTrue(all(row["membership_tier"] for row in rows))

    def test_unknown_join_type_is_rejected(self):
        with self.assertRaises(ValueError):
            self.executor.execute_view_report(
                "Customers with loyalty",
                join_types=["OUTER"],
            )

    def test_orders_by_city_aggregates_sorts_and_pages(self):
        report = self.executor.execute_view_report("Orders by city")
        rows = report["rows"]
        amounts = [row["total_amount"] for row in rows]

        self.assertEqual(report["totalCount"], 10)
        self.assertEqual(report["limit"], 5)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], {
            "city": "Chicago",
            "order_count": 1,
            "total_amount": 120000,
        })
        self.assertEqual(amounts, sorted(amounts, reverse=True))

    def test_having_uses_the_view_parameter(self):
        rows = self.executor.execute_view_report(
            "Orders by city",
            parameters={"min_orders": 2},
        )["rows"]

        self.assertEqual(rows, [{
            "city": "Phoenix",
            "order_count": 3,
            "total_amount": 92000,
        }])

    def test_runtime_sort_and_pagination(self):
        report = self.executor.execute_view_report(
            "Customers without loyalty",
            shape={
                "orderBy": [{
                    "entity": "Customer",
                    "attribute": "customer_id",
                    "direction": "asc",
                }],
                "limit": 1,
                "offset": 1,
            },
        )

        self.assertEqual(report["totalCount"], 3)
        self.assertEqual(report["rows"], [{
            "customer_id": 6,
            "name": "Frank Miller",
            "city": "Philadelphia",
        }])


class CustomViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_sample_database()
        cls.metaschema = MetaSchemaLoader(str(ROOT / "MetaSchema.xml"))
        cls.xml_path = str(ROOT / "dummy_data" / "purchaseorders.xml")

    def _execute(self, spec):
        views_xml = build_custom_view_xml(spec)
        with tempfile.TemporaryDirectory() as temp_dir:
            views_path = Path(temp_dir) / "views.xml"
            views_path.write_text(views_xml, encoding="utf-8")
            views = ViewLoader(str(views_path))
        executor = QueryExecutor(self.metaschema, views, self.xml_path)
        try:
            return executor.execute_view_report(CUSTOM_VIEW_NAME)
        finally:
            executor.close()

    def test_custom_left_join_keeps_customers_without_loyalty(self):
        report = self._execute({
            "entities": ["Customer", "CustomerLoyalty"],
            "projection": {
                "Customer": ["customer_id", "name", "city"],
                "CustomerLoyalty": ["customer_id", "membership_tier", "reward_points"],
            },
            "joins": [{"name": "CustomerLoyaltyJoin", "type": "LEFT"}],
        })
        by_id = {row["Customer.customer_id"]: row for row in report["rows"]}

        self.assertEqual(len(report["rows"]), 10)
        self.assertIsNone(by_id[3]["membership_tier"])
        self.assertEqual(by_id[1]["membership_tier"], "Gold")

    def test_custom_view_rejects_an_unknown_join_type(self):
        with self.assertRaises(ValueError):
            build_custom_view_xml({
                "entities": ["Customer"],
                "projection": {"Customer": ["name"]},
                "joins": [{"name": "CustomerLoyaltyJoin", "type": "OUTER"}],
            })

    def test_custom_view_requires_a_column(self):
        with self.assertRaises(ValueError):
            build_custom_view_xml({
                "entities": ["Customer"],
                "projection": {"Customer": []},
                "joins": [],
            })


class ExportTests(unittest.TestCase):
    def test_json_csv_excel_and_native_exports(self):
        columns = ["city", "order_count"]
        rows = [{"city": "Chicago", "order_count": 1}]
        sources = [
            {
                "entity": "PurchaseOrder",
                "sourceType": "XML",
                "shape": "hierarchical",
                "tree": [{"order_id": "1"}],
            }
        ]
        native = build_native_document("Orders by city", sources, columns, rows)

        json_export = render_export("json", "Orders by city", columns, rows)
        csv_export = render_export("csv", "Orders by city", columns, rows)
        excel_export = render_export("xlsx", "Orders by city", columns, rows)
        native_export = render_export("native", "Orders by city", columns, rows, native)

        self.assertEqual(json_export["filename"], "Orders_by_city.json")
        self.assertEqual(json_export["body"]["rows"], rows)

        self.assertEqual(
            csv_export["body"].decode("utf-8"),
            "city,order_count\nChicago,1\n",
        )

        with zipfile.ZipFile(io.BytesIO(excel_export["body"])) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Chicago", sheet)
        self.assertEqual(
            excel_export["filename"],
            "Orders_by_city.xlsx",
        )

        self.assertEqual(native_export["body"]["sources"][0]["shape"], "hierarchical")
        self.assertEqual(native_export["body"]["joined"]["rows"], rows)
        self.assertEqual(native_export["filename"], "Orders_by_city.native.json")

    def test_native_export_requires_a_document(self):
        with self.assertRaises(ValueError):
            render_export("native", "Orders by city", ["city"], [])


if __name__ == "__main__":
    unittest.main()
