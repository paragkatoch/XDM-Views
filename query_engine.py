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
    attribute: str = None
    operator: str = None
    value: Any = None
    query: str = None  # Direct XPath/SQL query for new filter format


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
        for db_elem in self.root.findall('.//Databases/Database'):
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
        for entity_elem in self.root.findall('.//Entities/Entity'):
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
        for rel_elem in self.root.findall('.//Relationships/Relationship'):
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
            
            # Parse filters - new format with Query element or traditional format
            filters = {}
            
            # Try new format first: <Filters><Filter>... (can be SQL or XML)
            filters_container = view_elem.find('.//Filters')
            if filters_container is not None:
                for filter_elem in filters_container.findall('Filter'):
                    entity_elem = filter_elem.find('Entity')
                    query_elem = filter_elem.find('Query')
                    attribute_elem = filter_elem.find('Attribute')
                    operator_elem = filter_elem.find('Operator')
                    value_elem = filter_elem.find('Value')
                    
                    if entity_elem is not None:
                        entity = entity_elem.text
                        
                        # Case 1: Filter has Query element (XML query)
                        if query_elem is not None:
                            query = query_elem.text
                            filters[entity] = QueryFilter(entity=entity, query=query)
                        
                        # Case 2: Filter has Attribute/Operator/Value (SQL filter)
                        elif attribute_elem is not None and operator_elem is not None and value_elem is not None:
                            attribute = attribute_elem.text
                            operator = operator_elem.text
                            value = value_elem.text
                            filters[entity] = QueryFilter(entity=entity, attribute=attribute, 
                                                         operator=operator, value=value)
            
            # Fallback to old format: <Filter><Entity>... or <Filter><Entity><Query>
            if not filters:
                filter_elem = view_elem.find('.//Filter')
                if filter_elem is not None:
                    entity_elem = filter_elem.find('Entity')
                    
                    if entity_elem is not None:
                        entity = entity_elem.text
                        
                        # Check if it has Query (new XPath format in old Filter container)
                        query_elem = filter_elem.find('Query')
                        if query_elem is not None:
                            query = query_elem.text
                            filters[entity] = QueryFilter(entity=entity, query=query)
                        else:
                            # Traditional attribute/operator/value format
                            attribute_elem = filter_elem.find('Attribute')
                            operator_elem = filter_elem.find('Operator')
                            value_elem = filter_elem.find('Value')
                            
                            attribute = attribute_elem.text if attribute_elem is not None else None
                            operator = operator_elem.text if operator_elem is not None else None
                            value = value_elem.text if value_elem is not None else None
                            filters[entity] = QueryFilter(entity=entity, attribute=attribute, 
                                                          operator=operator, value=value)
            
            self.views[view_name] = {
                'name': view_name,
                'projection': projection,
                'base_entities': base_entities,
                'relationship_ref': relationship_ref,
                'filters': filters  # Now a dict of entity -> QueryFilter
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
        
        New Logic:
        - If only XML filter: apply XPath, then join with SQL if baseEntities specifies SQL entity
        - If only SQL filter: apply SQL, then join with XML if baseEntities specifies XML entity
        - If both filters: apply both separately, then join them
        
        Args:
            view_name: Name of the view to execute
            
        Returns:
            List of result rows as dictionaries
        """
        view = self.views.views[view_name]
        
        # Get entities and determine types
        base_entities = view['base_entities']
        projection = view['projection']
        filters = view['filters']  # dict of entity -> QueryFilter
        relationship_ref = view['relationship_ref']
        
        # Categorize entities by database type
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
        
        # Determine which entities have filters
        sql_filter = filters.get(relational_entity) if relational_entity else None
        xml_filter = filters.get(xml_entity) if xml_entity else None
        
        results = {}
        
        # Case 1: Only XML filter
        if xml_filter and not sql_filter:
            print(f"\n[CASE 1] XML Filter Only")
            entity_meta = self.metaschema.entities[xml_entity]
            xml_results = self._query_xml_with_xquery(xml_entity, entity_meta, 
                                                      projection.get(xml_entity), xml_filter)
            results[xml_entity] = xml_results
            
            # Join with SQL table if relational_entity is in baseEntities
            if relational_entity:
                customer_ids = self._extract_join_keys(xml_results, 'customer_id')
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
        
        # Case 2: Only SQL filter
        elif sql_filter and not xml_filter:
            print(f"\n[CASE 2] SQL Filter Only")
            entity_meta = self.metaschema.entities[relational_entity]
            sql_results = self._query_relational_with_sql_filter(
                relational_entity, entity_meta,
                projection.get(relational_entity),
                sql_filter
            )
            results[relational_entity] = sql_results
            
            # Join with XML table if xml_entity is in baseEntities
            if xml_entity:
                customer_ids = self._extract_join_keys(sql_results, 'customer_id')
                if customer_ids:
                    entity_meta = self.metaschema.entities[xml_entity]
                    xml_results = self._query_xml_with_customer_ids(
                        xml_entity, entity_meta,
                        projection.get(xml_entity),
                        customer_ids
                    )
                    results[xml_entity] = xml_results
                else:
                    results[xml_entity] = []
        
        # Case 3: Both filters
        elif sql_filter and xml_filter:
            print(f"\n[CASE 3] Both Filters - Custom Join Query")
            # Apply both filters separately
            entity_meta_sql = self.metaschema.entities[relational_entity]
            sql_results = self._query_relational_with_sql_filter(
                relational_entity, entity_meta_sql,
                projection.get(relational_entity),
                sql_filter
            )
            results[relational_entity] = sql_results
            
            entity_meta_xml = self.metaschema.entities[xml_entity]
            xml_results = self._query_xml_with_xquery(
                xml_entity, entity_meta_xml,
                projection.get(xml_entity),
                xml_filter
            )
            results[xml_entity] = xml_results
        
        # Case 4: No filters
        else:
            print(f"\n[CASE 4] No Filters")
            if relational_entity:
                entity_meta = self.metaschema.entities[relational_entity]
                sql_results = self._query_relational(
                    relational_entity, entity_meta,
                    projection.get(relational_entity)
                )
                results[relational_entity] = sql_results
            
            if xml_entity:
                entity_meta = self.metaschema.entities[xml_entity]
                xml_results = self._query_xml_unfiltered(
                    xml_entity, entity_meta,
                    projection.get(xml_entity)
                )
                results[xml_entity] = xml_results
        
        # Return results - join if multiple entities
        if len(base_entities) == 1:
            for entity_name in base_entities:
                return results.get(entity_name, [])
        else:
            # Multiple entities - return joined results
            return self._join_results(base_entities, results, relationship_ref, projection)
    
    def _extract_join_keys(self, results: List[Dict], key_name: str) -> set:
        """Extract a specific key value from all results"""
        keys = set()
        for row in results:
            val = row.get(key_name)
            if val is not None:
                keys.add(val)
        return keys
    
    def _query_relational(self, entity_name: str, entity_meta: Dict, 
                          projected_attrs: List[str]) -> List[Dict]:
        """Execute unfiltered query on relational database"""
        
        # Determine which attributes to select
        if projected_attrs:
            select_attrs = projected_attrs
        else:
            select_attrs = list(entity_meta['attributes'].keys())
        
        # Build SQL query
        select_clause = ', '.join(select_attrs)
        from_clause = entity_name
        
        query = f"SELECT {select_clause} FROM {from_clause}"
        
        print(f"[SQL Query] {query}")
        
        # Execute and fetch results
        self.db_cursor.execute(query)
        columns = [desc[0] for desc in self.db_cursor.description]
        rows = self.db_cursor.fetchall()
        
        return [dict(zip(columns, row)) for row in rows]
    
    def _query_relational_with_sql_filter(self, entity_name: str, entity_meta: Dict, 
                                          projected_attrs: List[str], sql_filter: QueryFilter) -> List[Dict]:
        """Execute SQL query with filter using attribute/operator/value"""
        
        # Determine which attributes to select
        if projected_attrs:
            select_attrs = projected_attrs
        else:
            select_attrs = list(entity_meta['attributes'].keys())
        
        # Build SQL query with WHERE clause
        select_clause = ', '.join(select_attrs)
        from_clause = entity_name
        
        # SQL filters use traditional attribute/operator/value format
        where_clause = f"WHERE {sql_filter.attribute} {sql_filter.operator} '{sql_filter.value}'"
        query = f"SELECT {select_clause} FROM {from_clause} {where_clause}"
        
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
    
    def _query_xml_with_customer_ids(self, entity_name: str, entity_meta: Dict,
                                     projected_attrs: List[str], customer_ids: set) -> List[Dict]:
        """Execute query on XML database filtered by specific customer IDs"""
        
        base_path = entity_meta['base_path']
        attributes = entity_meta['attributes']
        
        # Convert absolute path to relative path for ElementTree
        path_parts = base_path.lstrip('/').split('/')
        element_name = path_parts[-1]
        
        print(f"[XPath Query] {base_path} [customer_id IN {sorted(customer_ids)}]")
        
        # Find all elements matching the element name
        elements = self.xml_root.findall(element_name)
        
        # Filter by customer_ids
        filtered_elements = []
        for elem in elements:
            cid_elem = elem.find('customer_id')
            if cid_elem is not None:
                try:
                    cid = int(cid_elem.text)
                    if cid in customer_ids or str(cid) in customer_ids:
                        filtered_elements.append(elem)
                except (ValueError, TypeError):
                    pass
        
        elements = filtered_elements
        
        # Extract attributes from results
        if projected_attrs is None:
            projected_attrs = list(attributes.keys())
        
        results = []
        for elem in elements:
            row = {}
            for attr_name in projected_attrs:
                # Handle nested attributes like "item" which maps to item_name and item_category
                if attr_name == "item" and attr_name not in attributes:
                    # Special handling for composite "item" attribute
                    item_data = {}
                    if "item_name" in attributes:
                        attr_info = attributes["item_name"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['name'] = value
                    
                    if "item_category" in attributes:
                        attr_info = attributes["item_category"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['category'] = value
                    
                    row['item'] = item_data
                else:
                    attr_info = attributes.get(attr_name)
                    if attr_info:
                        attr_path = attr_info['path']
                        
                        # Navigate using path
                        value = self._get_value_from_xml_element(elem, attr_path)
                        row[attr_name] = value
            
            results.append(row)
        
        return results
    
    def _query_xml_unfiltered(self, entity_name: str, entity_meta: Dict, 
                              projected_attrs: List[str]) -> List[Dict]:
        """Execute unfiltered query on XML database"""
        
        base_path = entity_meta['base_path']
        attributes = entity_meta['attributes']
        
        # Convert absolute path to relative path
        path_parts = base_path.lstrip('/').split('/')
        element_name = path_parts[-1]
        
        print(f"[XPath Query] {base_path}")
        
        # Find all elements matching the element name
        elements = self.xml_root.findall(element_name)
        
        # Extract attributes from results
        if projected_attrs is None:
            projected_attrs = list(attributes.keys())
        
        results = []
        for elem in elements:
            row = {}
            for attr_name in projected_attrs:
                # Handle nested attributes like "item" which maps to item_name and item_category
                if attr_name == "item" and attr_name not in attributes:
                    # Special handling for composite "item" attribute
                    item_data = {}
                    if "item_name" in attributes:
                        attr_info = attributes["item_name"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['name'] = value
                    
                    if "item_category" in attributes:
                        attr_info = attributes["item_category"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['category'] = value
                    
                    row['item'] = item_data
                else:
                    attr_info = attributes.get(attr_name)
                    if attr_info:
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        row[attr_name] = value
            results.append(row)
        
        return results
    
    def _query_xml_with_xquery(self, entity_name: str, entity_meta: Dict, 
                               projected_attrs: List[str], xml_filter: QueryFilter) -> List[Dict]:
        """Execute XML query using XPath/XQuery from filter"""
        
        base_path = entity_meta['base_path']
        attributes = entity_meta['attributes']
        
        # Convert absolute path to relative path
        path_parts = base_path.lstrip('/').split('/')
        element_name = path_parts[-1]
        
        # Extract the query from filter (e.g., "/PurchaseOrders/PurchaseOrder [amount > 10000]")
        xquery = xml_filter.query
        print(f"[XPath Query] {xquery}")
        
        # Find all elements matching the element name
        elements = self.xml_root.findall(element_name)
        
        # Parse the query to extract conditions
        # Format: /path/to/element [condition]
        filtered_elements = []
        
        if '[' in xquery and ']' in xquery:
            # Extract the condition part: condition inside [ ]
            condition_str = xquery[xquery.index('[') + 1 : xquery.index(']')]
            filtered_elements = self._filter_xml_elements(elements, condition_str, attributes)
        else:
            filtered_elements = elements
        
        elements = filtered_elements
        
        # Extract attributes from results
        if projected_attrs is None:
            projected_attrs = list(attributes.keys())
        
        results = []
        for elem in elements:
            row = {}
            for attr_name in projected_attrs:
                # Handle nested attributes like "item" which maps to item_name and item_category
                if attr_name == "item" and attr_name not in attributes:
                    # Special handling for composite "item" attribute
                    item_data = {}
                    if "item_name" in attributes:
                        attr_info = attributes["item_name"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['name'] = value
                    
                    if "item_category" in attributes:
                        attr_info = attributes["item_category"]
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        item_data['category'] = value
                    
                    row['item'] = item_data
                else:
                    attr_info = attributes.get(attr_name)
                    if attr_info:
                        attr_path = attr_info['path']
                        value = self._get_value_from_xml_element(elem, attr_path)
                        row[attr_name] = value
            
            results.append(row)
        
        return results
    
    def _filter_xml_elements(self, elements: List[ET.Element], condition_str: str, 
                             attributes: Dict) -> List[ET.Element]:
        """
        Filter XML elements based on condition string
        Handles conditions like: "amount > 10000", "item/item_name = Laptop", "customer_id = 5"
        """
        filtered = []
        
        # Parse condition: supports patterns like "attr OP value" or "xpath OP value"
        # Handle comparisons: >, <, >=, <=, =, !=
        operators = ['>=', '<=', '!=', '>', '<', '=']
        
        for op in operators:
            if f' {op} ' in condition_str:
                parts = condition_str.split(f' {op} ')
                if len(parts) == 2:
                    attr_path = parts[0].strip()
                    filter_value = parts[1].strip()
                    
                    # Try to convert value type
                    try:
                        if '.' in filter_value:
                            filter_val = float(filter_value)
                        else:
                            filter_val = int(filter_value)
                    except (ValueError, TypeError):
                        filter_val = str(filter_value)
                    
                    for elem in elements:
                        elem_value = self._get_value_from_xml_element(elem, attr_path)
                        
                        if elem_value is None:
                            continue
                        
                        # Convert element value for comparison
                        try:
                            if '.' in str(filter_val):
                                elem_val = float(elem_value)
                            else:
                                elem_val = int(elem_value)
                        except (ValueError, TypeError):
                            elem_val = str(elem_value)
                        
                        # Apply operator
                        match = False
                        if op == '>' and elem_val > filter_val:
                            match = True
                        elif op == '<' and elem_val < filter_val:
                            match = True
                        elif op == '>=' and elem_val >= filter_val:
                            match = True
                        elif op == '<=' and elem_val <= filter_val:
                            match = True
                        elif op == '=' and elem_val == filter_val:
                            match = True
                        elif op == '!=' and elem_val != filter_val:
                            match = True
                        
                        if match:
                            filtered.append(elem)
                break
        
        return filtered
    
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
    """Print query results in JSON format"""
    import json
    
    print(f"\n{'=' * 80}")
    print(f"VIEW: {view_name}")
    print(f"{'=' * 80}\n")
    
    if not results:
        print("No results\n")
        return
    
    # Output as formatted JSON
    output = {
        "view": view_name,
        "total_rows": len(results),
        "data": results
    }
    
    print(json.dumps(output, indent=2))


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
