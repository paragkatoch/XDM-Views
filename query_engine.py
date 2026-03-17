"""
XDM Views Query Engine
Executes federated queries across relational (SQL) and XML databases using XPath
"""

import time
import xml.etree.ElementTree as ET
import sqlite3
import os
from typing import List, Dict, Tuple, Any
from dataclasses import dataclass
import mysql.connector
from dotenv import load_dotenv
import os


# ENV_host="localhost"
# ENV_user="root"
# ENV_password="qwertyuiop"
# ENV_database="customerdb"
# ENV_base_path = '/Users/paragkatoch/Downloads/downloads/term 2/subs/DM/proj/phase1'

load_dotenv()
ENV_host = os.getenv("ENV_HOST")
ENV_user = os.getenv("ENV_USER")
ENV_password = os.getenv("ENV_PASSWORD")
ENV_database = os.getenv("ENV_DATABASE")
ENV_base_path = os.getenv("ENV_BASE_PATH")

@dataclass
class QueryFilter:
    """Represents a filter condition"""
    entity: str
    attribute: str
    operator: str
    value: Any


class MetaSchemaLoader:
    """Loads and parses MetaSchema.xml"""
    
    def __init__(self, metaschema_path: str):
        self.tree = ET.parse(metaschema_path)
        self.root = self.tree.getroot()
        self.databases = {}
        self.entities = {}
        self.relationships = {}
        self._parse()
    
    def _parse(self):
        """Parse MetaSchema.xml"""
        # Parse databases
        for db_elem in self.root.findall('.//Database'):
            db_id = db_elem.get('id')
            name_elem = db_elem.find('Name')
            type_elem = db_elem.find('Type')
            name = name_elem.text if name_elem is not None else None
            db_type = type_elem.text if type_elem is not None else None
            self.databases[db_id] = {
                'id': db_id,
                'name': name,
                'type': db_type
            }
        
        # Parse entities
        for entity_elem in self.root.findall('.//Entity'):
            entity_name = entity_elem.get('name')
            db_ref_elem = entity_elem.find('DatabaseRef')
            db_ref = db_ref_elem.text if db_ref_elem is not None else None
            
            # Get BasePath if it exists (for XML entities)
            base_path_elem = entity_elem.find('BasePath')
            base_path = base_path_elem.text if base_path_elem is not None else None
            
            # Parse attributes
            attributes = {}
            attrs_container = entity_elem.find('Attributes')
            if attrs_container is not None:
                for attr_elem in attrs_container.findall('Attribute'):
                    attr_name = attr_elem.get('name')
                    attr_type = attr_elem.get('type')
                    attr_path = attr_elem.get('path')  # For XML entities
                    attr_key = attr_elem.get('key')  # primary key indicator
                    
                    attributes[attr_name] = {
                        'name': attr_name,
                        'type': attr_type,
                        'path': attr_path,
                        'key': attr_key
                    }
            
            self.entities[entity_name] = {
                'name': entity_name,
                'database_ref': db_ref,
                'base_path': base_path,
                'attributes': attributes
            }
        
        # Parse relationships
        for rel_elem in self.root.findall('.//Relationship'):
            rel_name = rel_elem.get('name')
            rel_type = rel_elem.get('type')
            left_entity_elem = rel_elem.find('LeftEntity')
            right_entity_elem = rel_elem.find('RightEntity')
            left_entity = left_entity_elem.text if left_entity_elem is not None else None
            right_entity = right_entity_elem.text if right_entity_elem is not None else None
            
            # Parse condition element
            condition_elem = rel_elem.find('Condition')
            condition = None
            if condition_elem is not None:
                left_elem = condition_elem.find('Left')
                right_elem = condition_elem.find('Right')
                operator_elem = condition_elem.find('Operator')
                
                if left_elem is not None and right_elem is not None:
                    left_entity_cond = left_elem.find('Entity').text if left_elem.find('Entity') is not None else None
                    left_attr_cond = left_elem.find('Attribute').text if left_elem.find('Attribute') is not None else None
                    right_entity_cond = right_elem.find('Entity').text if right_elem.find('Entity') is not None else None
                    right_attr_cond = right_elem.find('Attribute').text if right_elem.find('Attribute') is not None else None
                    operator = operator_elem.text if operator_elem is not None else '='
                    
                    condition = {
                        'left_entity': left_entity_cond,
                        'left_attribute': left_attr_cond,
                        'operator': operator,
                        'right_entity': right_entity_cond,
                        'right_attribute': right_attr_cond
                    }
            
            self.relationships[rel_name] = {
                'name': rel_name,
                'type': rel_type,
                'left_entity': left_entity,
                'right_entity': right_entity,
                'condition': condition
            }


class ViewLoader:
    """Loads and parses views.xml"""
    
    def __init__(self, views_path: str):
        self.tree = ET.parse(views_path)
        self.root = self.tree.getroot()
        self.views = {}
        self._parse()
    
    def _parse(self):
        """Parse views.xml"""
        for view_elem in self.root.findall('.//View'):
            view_name = view_elem.get('name')
            
            # Parse projection
            projection = {}
            for proj_entity in view_elem.findall('.//Projection/Entity'):
                entity_name = proj_entity.get('name')
                attributes = [attr.text for attr in proj_entity.findall('Attribute')]
                projection[entity_name] = attributes
            
            # Parse base entities
            base_entities = [entity.text for entity in view_elem.findall('.//BaseEntities/Entity')]
            
            # Parse relationship ref
            rel_ref_elem = view_elem.find('.//RelationshipRef')
            relationship_ref = rel_ref_elem.text if rel_ref_elem is not None else None
            
            # Parse filter
            filter_elem = view_elem.find('.//Filter')
            view_filter = None
            if filter_elem is not None:
                entity = filter_elem.find('Entity').text
                attribute = filter_elem.find('Attribute').text
                operator = filter_elem.find('Operator').text
                value = filter_elem.find('Value').text
                view_filter = QueryFilter(entity, attribute, operator, value)
            
            self.views[view_name] = {
                'name': view_name,
                'projection': projection,
                'base_entities': base_entities,
                'relationship_ref': relationship_ref,
                'filter': view_filter
            }


class QueryExecutor:
    """Executes federated queries across relational and XML databases"""
    
    def __init__(self, metaschema: MetaSchemaLoader, views: ViewLoader,
                 db_path: str, xml_path: str):
        self.metaschema = metaschema
        self.views = views
        self.db_path = db_path
        self.xml_path = xml_path
        
        # Load XML database
        self.xml_tree = ET.parse(xml_path)
        self.xml_root = self.xml_tree.getroot()
        
        print(f"Loaded {self.xml_root.tag} xml database")
        
        # Connect to relational database
        # self.db_conn = sqlite3.connect(db_path)
        self.db_conn = mysql.connector.connect(
            host=ENV_host,
            user=ENV_user,
            password=ENV_password,
            database=ENV_database
        )
        self.db_cursor = self.db_conn.cursor()
        
        print(f"Loaded {self.db_conn.database} sql database")
    
    def execute_view(self, view_name: str) -> List[Dict[str, Any]]:
        """
        Execute a view query and return results as list of dictionaries
        
        Args:
            view_name: Name of the view to execute
            
        Returns:
            List of result rows as dictionaries
        """
        view = self.views.views[view_name]
        
        # Get entities involved
        base_entities = view['base_entities']
        projection = view['projection']
        view_filter = view['filter']
        relationship_ref = view['relationship_ref']
        
        results = {}
        
        # Determine which entity is relational and which is XML
        relational_entity = None
        xml_entity = None
        
        for entity_name in base_entities:
            entity_meta = self.metaschema.entities[entity_name]
            db_ref = entity_meta['database_ref']
            db_info = self.metaschema.databases[db_ref]
            
            if db_info['type'] == 'Relational':
                relational_entity = entity_name
            elif db_info['type'] == 'XML':
                xml_entity = entity_name
        
        # Execute XML query first (with filter)
        if xml_entity:
            entity_meta = self.metaschema.entities[xml_entity]
            xml_filter = view_filter if view_filter and view_filter.entity == xml_entity else None
            xml_results = self._query_xml(xml_entity, entity_meta, None, xml_filter)
            results[xml_entity] = xml_results
            
            # Extract customer_ids from XML results for SQL query
            if xml_results and relational_entity and relationship_ref:
                customer_ids = set()
                for row in xml_results:
                    cid = row.get('customer_id')
                    if cid is not None:
                        customer_ids.add(cid)
                
                # Execute SQL query with filtered customer_ids
                if customer_ids:
                    entity_meta = self.metaschema.entities[relational_entity]
                    sql_results = self._query_relational_with_ids(
                        relational_entity, entity_meta, 
                        projection.get(relational_entity),
                        customer_ids
                    )
                    results[relational_entity] = sql_results
                else:
                    results[relational_entity] = []
            elif relational_entity:
                # No filter, just get all from relational DB
                entity_meta = self.metaschema.entities[relational_entity]
                sql_results = self._query_relational(
                    relational_entity, entity_meta,
                    projection.get(relational_entity),
                    None  # No filter
                )
                results[relational_entity] = sql_results
        
        # Execute queries for relational entity if needed
        elif relational_entity:
            entity_meta = self.metaschema.entities[relational_entity]
            sql_filter = view_filter if view_filter and view_filter.entity == relational_entity else None
            results[relational_entity] = self._query_relational(
                relational_entity, entity_meta,
                projection.get(relational_entity),
                sql_filter
            )
        
        # Return results
        if len(base_entities) == 1:
            for entity_name in base_entities:
                return results.get(entity_name, [])
        else:
            # Multiple entities - return joined results
            return self._join_results(base_entities, results, relationship_ref, projection)
    
    def _query_relational(self, entity_name: str, entity_meta: Dict, 
                          projected_attrs: List[str], view_filter: QueryFilter) -> List[Dict]:
        """Execute query on relational database"""
        
        # Determine which attributes to select
        if projected_attrs:
            select_attrs = projected_attrs
        else:
            select_attrs = list(entity_meta['attributes'].keys())
        
        # Build SQL query
        select_clause = ', '.join(select_attrs)
        from_clause = entity_name
        where_clause = ''
        
        # Add filter if it applies to this entity
        if view_filter and view_filter.entity == entity_name:
            where_clause = f"WHERE {view_filter.attribute} {view_filter.operator} '{view_filter.value}'"
        
        query = f"SELECT {select_clause} FROM {from_clause}"
        if where_clause:
            query += f" {where_clause}"
        
        print(f"[SQL Query] {query}")
        
        # Execute and fetch results
        self.db_cursor.execute(query)
        columns = [desc[0] for desc in self.db_cursor.description]
        rows = self.db_cursor.fetchall()
        
        return [dict(zip(columns, row)) for row in rows]
    
    def _query_relational_with_ids(self, entity_name: str, entity_meta: Dict,
                                   projected_attrs: List[str], customer_ids: set) -> List[Dict]:
        """Execute query on relational database with specific customer IDs"""
        
        # Determine which attributes to select
        if projected_attrs:
            select_attrs = projected_attrs
        else:
            select_attrs = list(entity_meta['attributes'].keys())
        
        # Build SQL query with IN clause
        select_clause = ', '.join(select_attrs)
        from_clause = entity_name
        ids_list = ','.join(str(cid) for cid in sorted(customer_ids))
        where_clause = f"WHERE customer_id IN ({ids_list})"
        
        query = f"SELECT {select_clause} FROM {from_clause} {where_clause}"
        
        print(f"[SQL Query] {query}")
        
        # Execute and fetch results
        self.db_cursor.execute(query)
        columns = [desc[0] for desc in self.db_cursor.description]
        rows = self.db_cursor.fetchall()
        
        return [dict(zip(columns, row)) for row in rows]
    
    def _query_xml(self, entity_name: str, entity_meta: Dict, 
                   projected_attrs: List[str], view_filter: QueryFilter) -> List[Dict]:
        """Execute query on XML database using XPath"""
        
        base_path = entity_meta['base_path']  # e.g., "/PurchaseOrders/PurchaseOrder"
        attributes = entity_meta['attributes']
        
        # Convert absolute path to relative path for ElementTree
        # /PurchaseOrders/PurchaseOrder -> PurchaseOrder (since root is PurchaseOrders)
        path_parts = base_path.lstrip('/').split('/')
        element_name = path_parts[-1]  # Last part is the element we want
        
        print(f"[XPath Query] {base_path}", end="")
        
        # Find all elements matching the element name (root element handles the rest)
        elements = self.xml_root.findall(element_name)
        
        # Apply filter using Python (since ElementTree XPath is limited)
        if view_filter and view_filter.entity == entity_name:
            filter_attr = view_filter.attribute
            operator = view_filter.operator
            filter_value = view_filter.value
            
            # Resolve attribute name (handle "item" mapping to "item_name")
            if filter_attr == 'item':
                # Special case: "item" filter should check item_name
                attr_path = attributes.get('item_name', {}).get('path')
                if not attr_path:
                    attr_path = 'item/item_name'
            else:
                attr_path = attributes.get(filter_attr, {}).get('path', filter_attr)
            
            print(f" [{attr_path} {operator} {filter_value}]")
            
            filtered_elements = []
            for elem in elements:
                elem_value = self._get_value_from_xml_element(elem, attr_path)
                
                if elem_value is None:
                    continue
                
                # Convert to appropriate type for comparison
                try:
                    if '.' in str(filter_value):
                        filter_val = float(filter_value)
                        elem_val = float(elem_value)
                    else:
                        filter_val = int(filter_value)
                        elem_val = int(elem_value)
                except (ValueError, TypeError):
                    filter_val = str(filter_value)
                    elem_val = str(elem_value)
                
                # Apply operator
                if operator == '>' and elem_val > filter_val:
                    filtered_elements.append(elem)
                elif operator == '<' and elem_val < filter_val:
                    filtered_elements.append(elem)
                elif operator == '>=' and elem_val >= filter_val:
                    filtered_elements.append(elem)
                elif operator == '<=' and elem_val <= filter_val:
                    filtered_elements.append(elem)
                elif operator == '=' and elem_val == filter_val:
                    filtered_elements.append(elem)
                elif operator == '!=' and elem_val != filter_val:
                    filtered_elements.append(elem)
            
            elements = filtered_elements
        else:
            print()
        
        # Extract attributes from results
        if projected_attrs is None:
            projected_attrs = list(attributes.keys())
        
        results = []
        for elem in elements:
            row = {}
            for attr_name in projected_attrs:
                attr_info = attributes[attr_name]
                attr_path = attr_info['path']
                
                # Navigate using path
                value = self._get_value_from_xml_element(elem, attr_path)
                row[attr_name] = value
            
            results.append(row)
        
        return results
    
    def _get_value_from_xml_element(self, elem: ET.Element, path: str) -> Any:
        """Extract value from XML element using path"""
        parts = path.split('/')
        current = elem
        
        for part in parts:
            if current is None:
                return None
            current = current.find(part)
        
        return current.text if current is not None else None
    
    def _join_results(self, entities: List[str], results: Dict[str, List[Dict]], 
                      relationship_ref: str, projection: Dict) -> List[Dict]:
        """Join results from multiple entities"""
        
        relationship = self.metaschema.relationships[relationship_ref]
        left_entity = relationship['left_entity']
        right_entity = relationship['right_entity']
        
        # Get join condition
        condition = relationship['condition']
        if condition:
            left_key = condition['left_attribute']
            right_key = condition['right_attribute']
        else:
            # Fallback: find keys automatically
            left_key = None
            right_key = None
            for attr_name in self.metaschema.entities[left_entity]['attributes']:
                if 'id' in attr_name.lower():
                    left_key = attr_name
                    break
            
            for attr_name in self.metaschema.entities[right_entity]['attributes']:
                if 'id' in attr_name.lower() and 'customer' in attr_name.lower():
                    right_key = attr_name
                    break
        
        if not left_key or not right_key:
            raise ValueError("Could not determine join keys")
        
        left_results = results.get(left_entity, [])
        right_results = results.get(right_entity, [])
        
        # Perform equi-join with type conversion
        joined = []
        for left_row in left_results:
            for right_row in right_results:
                left_val = left_row.get(left_key)
                right_val = right_row.get(right_key)
                
                # Convert to same type for comparison (handle string/int mismatch from XML vs SQL)
                try:
                    if isinstance(left_val, str) and isinstance(right_val, int):
                        left_val = int(left_val)
                    elif isinstance(left_val, int) and isinstance(right_val, str):
                        right_val = int(right_val)
                except (ValueError, TypeError):
                    pass
                
                if left_val == right_val:
                    merged_row = {**left_row, **right_row}
                    joined.append(merged_row)
        
        return joined
    
    def close(self):
        """Close database connection"""
        self.db_conn.close()


def print_results(view_name: str, results: list):
    """Pretty print query results"""
    print(f"\n{'=' * 80}")
    print(f"VIEW: {view_name}")
    print(f"{'=' * 80}")
    
    if not results:
        print("No results")
        return
    
    # Get all unique keys from all rows
    all_keys = set()
    for row in results:
        all_keys.update(row.keys())
    
    # Print header
    keys = sorted(all_keys)
    header = " | ".join(f"{k:15}" for k in keys)
    print(header)
    print("-" * len(header))
    
    # Print rows
    for row in results:
        values = [str(row.get(k, '')).rjust(15) for k in keys]
        print(" | ".join(values))
    
    print(f"\nTotal rows: {len(results)}")


def main():
    """Example usage of the query engine"""
    
    print("XDM Views: Launching...")
    print("\nXDM Views: Reading Metaschema....")
    
    # Load metadata
    base_path = ENV_base_path
    metaschema = MetaSchemaLoader(os.path.join(base_path, 'MetaSchema.xml'))
    views = ViewLoader(os.path.join(base_path, 'views/views.xml'))
    
    print(f"Found {len(metaschema.databases)} databases")
    print(f"Found {len(metaschema.entities)} entities")
    print(f"Found {len(views.views)} views")
    
    print("\nXDM Views: Loading Databases....")
    
    # Create executor
    executor = QueryExecutor(
        metaschema,
        views,
        os.path.join(base_path, 'dummy_data/customers.db'),
        os.path.join(base_path, 'dummy_data/purchaseorders.xml')
    )
    
    print("\nXDM Views: Launching....")
    time.sleep(3)
    
    view_list = list(views.views.values())
    print("\n\n\n=======  XDM Views  =======\n\n")
    
    while 1:
        print("Available Views: ")
        
        for i, view in enumerate(view_list, start=1):
            print(f"{i}. {view['name']}")
        print("0. Exit")

        choice = int(input("\nSelect a view: "))
        
        if choice == 0:
            print("\nExiting....")
            break
        
        selected_view = view_list[choice - 1]["name"]
        results = executor.execute_view(selected_view)
        print_results(selected_view, results)
        input("\n")
    
    executor.close()


if __name__ == '__main__':
    main()
