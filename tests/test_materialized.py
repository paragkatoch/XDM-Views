"""Checks that a view's result can be stored and then queried as an entity."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["ENV_BASE_PATH"] = str(ROOT)
sys.path.insert(0, str(ROOT))

import materialized
from dummy_data.init_database import init_database
from query_engine import MetaSchemaLoader, QueryExecutor, ViewLoader
from sources import SqliteSource

VIEWS_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<Views>
  <View name="Big cities">
    <Projection>
      <Entity name="{entity}">
        <Attribute>city</Attribute>
        <Attribute>order_count</Attribute>
      </Entity>
    </Projection>
    <BaseEntities>
      <Entity>{entity}</Entity>
    </BaseEntities>
    <Filters>
      <Filter>
        <Entity>{entity}</Entity>
        <Attribute>order_count</Attribute>
        <Operator>&gt;=</Operator>
        <Value>2</Value>
      </Filter>
    </Filters>
  </View>
</Views>
"""


def write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


class NamingTests(unittest.TestCase):
    def test_entity_name_is_pascal_case_with_suffix(self):
        self.assertEqual(materialized.entity_name_for_view("Orders by city"), "OrdersByCityMV")
        self.assertEqual(materialized.entity_name_for_view("2024 sales"), "View2024SalesMV")

    def test_column_names_are_safe_and_unique(self):
        names = materialized.column_names(["Customer.customer_id", "customer_id", "order", "1st"])
        self.assertEqual(names, ["customer_customer_id", "customer_id", "order_value", "c_1st"])


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "dummy_data" / "customers.db").exists():
            init_database()
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.db_path = self.dir / "materialized.db"
        self.metaschema_xml = (ROOT / "MetaSchema.xml").read_text(encoding="utf-8")
        self.metaschema_xml = materialized.metaschema_without_entity(self.metaschema_xml, "OrdersByCityMV")

    def tearDown(self):
        self.temp.cleanup()

    def executor(self, metaschema_xml: str, views_xml: str) -> QueryExecutor:
        sources = {"MV": SqliteSource(self.db_path)} if self.db_path.exists() else {}
        return QueryExecutor(
            MetaSchemaLoader(write(self.dir / "MetaSchema.xml", metaschema_xml)),
            ViewLoader(write(self.dir / "views.xml", views_xml)),
            sources=sources,
        )

    def test_stored_view_can_be_queried_as_an_entity(self):
        views_xml = (ROOT / "views" / "views.xml").read_text(encoding="utf-8")
        source = self.executor(self.metaschema_xml, views_xml)
        try:
            rows = source.execute_view_report("Orders by city", shape={"limit": None, "offset": 0})["rows"]
        finally:
            source.close()

        columns = list(rows[0].keys())
        stored = materialized.store_rows(self.db_path, "OrdersByCityMV", "Orders by city", columns, rows)
        self.assertEqual(stored["rowCount"], len(rows))
        self.assertEqual(materialized.catalog(self.db_path)["OrdersByCityMV"]["viewName"], "Orders by city")

        metaschema_xml = materialized.metaschema_with_entity(
            self.metaschema_xml, "OrdersByCityMV", stored["attributes"]
        )
        self.assertEqual(metaschema_xml.count('<Database id="MV">'), 1)
        self.assertEqual(metaschema_xml.count('<Connection url="sqlite:///dummy_data/materialized.db"/>'), 1)
        again = materialized.metaschema_with_entity(metaschema_xml, "OrdersByCityMV", stored["attributes"])
        self.assertEqual(again.count('<Entity name="OrdersByCityMV">'), 1)

        reader = self.executor(metaschema_xml, VIEWS_XML_TEMPLATE.format(entity="OrdersByCityMV"))
        try:
            result = reader.execute_view_report("Big cities")["rows"]
        finally:
            reader.close()

        expected = sorted(row["city"] for row in rows if row["order_count"] >= 2)
        self.assertEqual(sorted(row["city"] for row in result), expected)
        self.assertTrue(expected)

    def test_drop_removes_table_and_entity(self):
        stored = materialized.store_rows(self.db_path, "TinyMV", "Tiny", ["a"], [{"a": 1}])
        metaschema_xml = materialized.metaschema_with_entity(self.metaschema_xml, "TinyMV", stored["attributes"])

        materialized.drop_table(self.db_path, "TinyMV")
        self.assertEqual(materialized.catalog(self.db_path), {})
        self.assertNotIn("TinyMV", materialized.metaschema_without_entity(metaschema_xml, "TinyMV"))


if __name__ == "__main__":
    unittest.main()
