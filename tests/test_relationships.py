"""Checks that relationship edits are validated before MetaSchema.xml is written."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["ENV_BASE_PATH"] = str(ROOT)
sys.path.insert(0, str(ROOT))

from xdm_frontend_server import load_from_xml_strings, relationship_problems

BROKEN_JOIN = """
    <Relationship name="BrokenJoin" type="EQUI_JOIN">
      <LeftEntity>Customer</LeftEntity>
      <RightEntity>Nowhere</RightEntity>
      <Condition>
        <Left><Entity>Customer</Entity><Attribute>missing_column</Attribute></Left>
        <Operator>=</Operator>
        <Right><Entity>Nowhere</Entity><Attribute>id</Attribute></Right>
      </Condition>
    </Relationship>
  </Relationships>"""


class RelationshipValidationTests(unittest.TestCase):
    def setUp(self):
        self.metaschema_xml = (ROOT / "MetaSchema.xml").read_text(encoding="utf-8")
        self.views_xml = (ROOT / "views" / "views.xml").read_text(encoding="utf-8")

    def test_sample_relationships_are_valid(self):
        metaschema, _ = load_from_xml_strings(self.metaschema_xml, self.views_xml)
        self.assertEqual(relationship_problems(metaschema), [])

    def test_unknown_entity_and_column_are_reported(self):
        broken = self.metaschema_xml.replace("</Relationships>", BROKEN_JOIN, 1)
        metaschema, _ = load_from_xml_strings(broken, self.views_xml)
        problems = relationship_problems(metaschema)
        self.assertIn("BrokenJoin: 'Customer' has no column 'missing_column'.", problems)
        self.assertIn("BrokenJoin: entity 'Nowhere' does not exist.", problems)


if __name__ == "__main__":
    unittest.main()
