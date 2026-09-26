"""
XDM Views Query Engine
Executes federated queries across relational (SQL) and XML databases using XPath
"""

import re
import time
import xml.etree.ElementTree as ET
import sqlite3
import os
from typing import List, Dict, Tuple, Any
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()
ENV_base_path = os.getenv("ENV_BASE_PATH")

JOIN_TYPES = ('INNER', 'LEFT', 'RIGHT', 'FULL', 'SEMI', 'ANTI')


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
            
            # Parse relationship refs in declaration order. Type defaults to INNER.
            relationship_refs = []
            for rel_elem in view_elem.findall('.//RelationshipRef'):
                rel_name = (rel_elem.text or '').strip()
                if not rel_name:
                    continue
                join_type = (rel_elem.get('type') or 'INNER').strip().upper()
                if join_type not in JOIN_TYPES:
                    allowed = ", ".join(JOIN_TYPES)
                    raise ValueError(
                        f"Join type '{join_type}' on {rel_name} in view '{view_name}' "
                        f"must be one of: {allowed}."
                    )
                relationship_refs.append({'name': rel_name, 'type': join_type})
            parameters = self._parse_parameters(view_elem, view_name)
            
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
            
            shape = self._parse_result_shape(view_elem, view_name)
            self.views[view_name] = {
                'name': view_name,
                'projection': projection,
                'base_entities': base_entities,
                'relationship_refs': relationship_refs,
                'parameters': parameters,
                'filters': filters,  # Now a dict of entity -> QueryFilter
                **shape,
            }

    def _parse_parameters(self, view_elem, view_name: str) -> List[Dict[str, Any]]:
        """Read <Parameters><Parameter name type default/></Parameters> in order."""
        parameters = []
        seen = set()
        container = view_elem.find('Parameters')
        if container is None:
            return parameters

        for elem in container.findall('Parameter'):
            name = (elem.get('name') or '').strip()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise ValueError(f"Parameter name in view '{view_name}' must be an identifier.")
            if name in seen:
                raise ValueError(f"Duplicate parameter '{name}' in view '{view_name}'.")
            seen.add(name)

            param_type = (elem.get('type') or 'string').strip()
            if param_type not in ('int', 'float', 'string'):
                raise ValueError(
                    f"Parameter '{name}' in view '{view_name}' must be int, float, or string."
                )

            parameters.append({
                'name': name,
                'type': param_type,
                'default': elem.get('default'),
            })

        return parameters

    def _parse_result_shape(self, view_elem, view_name: str) -> Dict[str, Any]:
        """Read grouping, measures, having, sort, and page bounds from a view."""
        group_by = []
        measures = []
        having = []
        aggregation = view_elem.find('Aggregation')
        if aggregation is not None:
            group_container = aggregation.find('GroupBy')
            if group_container is not None:
                for attr_elem in group_container.findall('Attribute'):
                    attribute = (attr_elem.text or '').strip()
                    if not attribute:
                        raise ValueError(f"Group by attribute in view '{view_name}' is empty.")
                    group_by.append({
                        'entity': attr_elem.get('entity'),
                        'attribute': attribute,
                    })
            for measure_elem in aggregation.findall('Measures/Measure'):
                function = (measure_elem.get('fn') or '').strip().lower()
                alias = (measure_elem.get('alias') or '').strip()
                if function not in ('count', 'sum', 'avg', 'min', 'max'):
                    raise ValueError(
                        f"Measure '{alias or function}' in view '{view_name}' "
                        "must be count, sum, avg, min, or max."
                    )
                if not alias:
                    raise ValueError(f"Measure in view '{view_name}' needs an alias.")
                measures.append({
                    'fn': function,
                    'entity': measure_elem.get('entity'),
                    'attribute': measure_elem.get('attribute'),
                    'alias': alias,
                })
            for having_elem in aggregation.findall('Having/Filter'):
                alias = (having_elem.get('alias') or '').strip()
                operator = (having_elem.get('operator') or '=').strip()
                if not alias:
                    raise ValueError(f"Having filter in view '{view_name}' needs an alias.")
                having.append({
                    'alias': alias,
                    'operator': operator,
                    'value': having_elem.get('value'),
                })

        order_by = []
        order_container = view_elem.find('OrderBy')
        if order_container is not None:
            for sort_elem in order_container.findall('Sort'):
                direction = (sort_elem.get('direction') or 'asc').strip().lower()
                if direction not in ('asc', 'desc'):
                    raise ValueError(f"Sort direction in view '{view_name}' must be asc or desc.")
                order_by.append({
                    'entity': sort_elem.get('entity'),
                    'attribute': sort_elem.get('attribute'),
                    'alias': sort_elem.get('alias'),
                    'direction': direction,
                })

        return {
            'groupBy': group_by,
            'measures': measures,
            'having': having,
            'orderBy': order_by,
            'limit': self._optional_int(view_elem.findtext('Limit'), view_name, 'Limit'),
            'offset': self._optional_int(view_elem.findtext('Offset'), view_name, 'Offset') or 0,
        }

    def _optional_int(self, raw: str, view_name: str, label: str):
        if raw is None or str(raw).strip() == '':
            return None
        try:
            return int(str(raw).strip())
        except ValueError as exc:
            raise ValueError(f"{label} in view '{view_name}' must be an integer.") from exc


class QueryExecutor:
    """Executes federated queries across relational and XML databases"""
    
    def __init__(self, metaschema: MetaSchemaLoader, views: ViewLoader, xml_path: str):
        self.metaschema = metaschema
        self.views = views

        self.xml_root = ET.parse(xml_path).getroot()
        print(f"Loaded {self.xml_root.tag} xml database")

        db_path = os.path.join(ENV_base_path, 'dummy_data', 'customers.db')
        self.db_conn = sqlite3.connect(db_path)
        self.db_cursor = self.db_conn.cursor()
        print(f"Loaded SQLite database from {db_path}")
    
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
        return self.execute_view_report(view_name)["rows"]

    def execute_view_report(self, view_name: str, parameters: Dict[str, Any] = None,
                            join_types: List[str] = None, shape: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute a view and keep each source in its own shape plus the joined rows."""
        view = self.views.views[view_name]
        base_entities = view['base_entities']
        projection = view['projection']
        bound = self._bind_parameters(view.get('parameters') or [], parameters)
        filters = self._filters_with_parameters(view['filters'], bound)
        relationship_refs = self._with_join_types(view.get('relationship_refs') or [], join_types)

        entity_rows = {}
        sources = []
        for entity_name in base_entities:
            projected_attrs = projection.get(entity_name)
            query_attrs = self._attrs_with_join_keys(
                entity_name, projected_attrs, relationship_refs
            )
            entity_filter = filters.get(entity_name)
            rows = self._query_entity(entity_name, query_attrs, entity_filter)
            entity_rows[entity_name] = rows
            sources.append(
                self._source_section(entity_name, projected_attrs, entity_filter, rows)
            )

        if len(base_entities) == 1:
            joined_rows = entity_rows.get(base_entities[0], [])
        else:
            joined_rows = self._join_results(
                base_entities, entity_rows, relationship_refs, projection
            )
        joined_rows = self._keep_projected_columns(joined_rows, projection)
        shaped = self._shape_rows(joined_rows, view, shape or {}, bound)

        return {
            "rows": shaped["rows"],
            "sources": sources,
            "parameters": bound,
            "joins": relationship_refs,
            "totalCount": shaped["totalCount"],
            "limit": shaped["limit"],
            "offset": shaped["offset"],
        }

    def _shape_choice(self, shape: Dict[str, Any], key: str, fallback: Any) -> Any:
        if key in shape:
            return shape[key]
        return fallback

    def _shape_rows(self, rows: List[Dict[str, Any]], view: Dict[str, Any],
                    shape: Dict[str, Any], bound: Dict[str, Any]) -> Dict[str, Any]:
        """Group, filter measures, sort, then page the joined rows."""
        group_by = self._shape_choice(shape, 'groupBy', view.get('groupBy') or []) or []
        if 'groupBy' in shape:
            measures = shape.get('measures') or []
            having = shape.get('having') or []
        else:
            measures = view.get('measures') or []
            having = view.get('having') or []
        order_by = self._shape_choice(shape, 'orderBy', view.get('orderBy') or []) or []
        limit = self._shape_choice(shape, 'limit', view.get('limit'))
        offset = self._shape_choice(shape, 'offset', view.get('offset') or 0) or 0

        if not isinstance(group_by, list) or not isinstance(measures, list):
            raise ValueError("groupBy and measures must be lists.")
        if not isinstance(order_by, list):
            raise ValueError("orderBy must be a list.")
        if limit is not None:
            limit = int(limit)
            if limit < 0:
                raise ValueError("limit must be zero or greater.")
        offset = int(offset)
        if offset < 0:
            raise ValueError("offset must be zero or greater.")

        if group_by:
            if not measures:
                measures = [{'fn': 'count', 'alias': 'row_count'}]
            rows = self._aggregate_rows(rows, group_by, measures)
            rows = self._apply_having(rows, self._resolve_having(having, bound))

        rows = self._sort_rows(rows, order_by)
        total_count = len(rows)
        paged = rows[offset:]
        if limit is not None:
            paged = paged[:limit]
        return {"rows": paged, "totalCount": total_count, "limit": limit, "offset": offset}

    def _result_keys(self, rows: List[Dict[str, Any]]) -> set:
        keys = set()
        for row in rows:
            keys.update(row.keys())
        return keys

    def _column_name(self, keys: set, spec: Dict[str, Any]) -> str:
        alias = spec.get('alias')
        attribute = spec.get('attribute')
        entity = spec.get('entity')
        qualified = f"{entity}.{attribute}" if entity and attribute else None
        if qualified and qualified in keys:
            return qualified
        if attribute and attribute in keys:
            return attribute
        if alias and alias in keys:
            return alias
        label = alias or qualified or attribute or "column"
        raise ValueError(f"Column '{label}' is not in the result.")

    def _aggregate_rows(self, rows: List[Dict[str, Any]], group_by: List[Dict[str, Any]],
                        measures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        keys = self._result_keys(rows)
        group_columns = [self._column_name(keys, spec) for spec in group_by]
        grouped = {}
        order = []
        for row in rows:
            group_key = tuple(row.get(column) for column in group_columns)
            if group_key not in grouped:
                grouped[group_key] = []
                order.append(group_key)
            grouped[group_key].append(row)

        aggregated = []
        for group_key in order:
            bucket = grouped[group_key]
            output = {column: value for column, value in zip(group_columns, group_key)}
            for measure in measures:
                alias = measure.get('alias')
                if not alias:
                    raise ValueError("Each measure needs an alias.")
                output[alias] = self._measure_value(bucket, measure, keys)
            aggregated.append(output)
        return aggregated

    def _measure_value(self, rows: List[Dict[str, Any]], measure: Dict[str, Any], keys: set) -> Any:
        function = (measure.get('fn') or '').strip().lower()
        if function not in ('count', 'sum', 'avg', 'min', 'max'):
            raise ValueError("Measure fn must be count, sum, avg, min, or max.")
        if function == 'count' and not measure.get('attribute'):
            return len(rows)

        column = self._column_name(keys, measure)
        values = [row.get(column) for row in rows if row.get(column) is not None]
        if function == 'count':
            return len(values)

        numbers = [number for number in (self._as_number(value) for value in values) if number is not None]
        if function in ('sum', 'avg'):
            if not numbers:
                return None
            total = sum(numbers)
            if function == 'sum':
                return total
            return total / len(numbers)
        if numbers and len(numbers) == len(values):
            return min(numbers) if function == 'min' else max(numbers)
        if not values:
            return None
        return min(values) if function == 'min' else max(values)

    def _as_number(self, value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        text = str(value).strip()
        if text == '':
            return None
        try:
            return int(text) if '.' not in text else float(text)
        except ValueError:
            return None

    def _resolve_having(self, having: List[Dict[str, Any]], bound: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Replace $name in having values with the bound parameter."""
        resolved = []
        for condition in having or []:
            if not isinstance(condition, dict):
                raise ValueError("Each having condition must be an object.")
            value = condition.get('value')
            if isinstance(value, str):
                value = self._substitute_parameters(value, bound)
            resolved.append({**condition, 'value': value})
        return resolved

    def _apply_having(self, rows: List[Dict[str, Any]], having: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not having:
            return rows
        operators = {
            '>': lambda left, right: left > right,
            '<': lambda left, right: left < right,
            '>=': lambda left, right: left >= right,
            '<=': lambda left, right: left <= right,
            '=': lambda left, right: left == right,
            '!=': lambda left, right: left != right,
        }
        filtered = rows
        for condition in having:
            alias = condition.get('alias')
            operator = condition.get('operator') or '='
            if operator not in operators:
                raise ValueError(f"Having operator '{operator}' is not supported.")
            compare = operators[operator]
            raw_value = condition.get('value')
            kept = []
            for row in filtered:
                left = row.get(alias)
                right = self._as_number(raw_value)
                if right is None:
                    right = '' if raw_value is None else str(raw_value)
                if isinstance(left, str):
                    numeric_left = self._as_number(left)
                    if numeric_left is not None and isinstance(right, (int, float)):
                        left = numeric_left
                if left is None:
                    continue
                try:
                    matched = compare(left, right)
                except TypeError as exc:
                    raise ValueError(
                        f"Having cannot compare '{alias}' with '{raw_value}'."
                    ) from exc
                if matched:
                    kept.append(row)
            filtered = kept
        return filtered

    def _sort_rows(self, rows: List[Dict[str, Any]], order_by: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not order_by or not rows:
            return rows
        keys = self._result_keys(rows)
        ordered = list(rows)
        for spec in reversed(order_by):
            column = self._column_name(keys, spec)
            direction = (spec.get('direction') or 'asc').strip().lower()
            if direction not in ('asc', 'desc'):
                raise ValueError("Sort direction must be asc or desc.")
            present = [row for row in ordered if row.get(column) is not None]
            missing = [row for row in ordered if row.get(column) is None]
            present.sort(key=lambda row: self._sort_value(row.get(column)), reverse=direction == 'desc')
            ordered = present + missing
        return ordered

    def _sort_value(self, value: Any) -> Any:
        number = self._as_number(value)
        if number is not None:
            return (0, number)
        return (1, str(value))

    def _with_join_types(self, relationship_refs: List[Dict[str, str]],
                         join_types: List[str] = None) -> List[Dict[str, str]]:
        """Use selected join types when the caller overrides the view definition."""
        if not join_types:
            return relationship_refs
        if not isinstance(join_types, list):
            allowed = ", ".join(JOIN_TYPES)
            raise ValueError(f"joinTypes must be a list of {allowed}.")

        updated = []
        for index, ref in enumerate(relationship_refs):
            join_type = ref['type']
            if index < len(join_types) and str(join_types[index]).strip():
                join_type = str(join_types[index]).strip().upper()
            if join_type not in JOIN_TYPES:
                allowed = ", ".join(JOIN_TYPES)
                raise ValueError(f"Join type '{join_type}' must be one of: {allowed}.")
            updated.append({'name': ref['name'], 'type': join_type})
        return updated

    def _attrs_with_join_keys(self, entity_name: str, projected_attrs: List[str],
                              relationship_refs: List[Dict[str, str]]) -> List[str]:
        """Include join keys in the entity query even when the view does not project them."""
        if projected_attrs is None:
            return None
        attrs = list(projected_attrs)
        for relationship_ref in relationship_refs:
            relationship = self.metaschema.relationships[relationship_ref['name']]
            left_key, right_key = self._get_join_keys(relationship)
            if relationship['left_entity'] == entity_name and left_key not in attrs:
                attrs.append(left_key)
            if relationship['right_entity'] == entity_name and right_key not in attrs:
                attrs.append(right_key)
        return attrs

    def _keep_projected_columns(self, rows: List[Dict[str, Any]],
                                projection: Dict) -> List[Dict[str, Any]]:
        """Drop join keys that were loaded only for matching."""
        allowed = set()
        for entity_name, attrs in (projection or {}).items():
            for attr in attrs or []:
                allowed.add(attr)
                allowed.add(f"{entity_name}.{attr}")
        if not allowed:
            return rows
        return [
            {key: value for key, value in row.items() if key in allowed}
            for row in rows
        ]

    def _bind_parameters(self, declared: List[Dict[str, Any]],
                         provided: Dict[str, Any] = None) -> Dict[str, Any]:
        """Fill each declared parameter from the request, then from its default."""
        provided = provided or {}
        if not isinstance(provided, dict):
            raise ValueError("parameters must be an object of name to value.")

        declared_names = {item['name'] for item in declared}
        unknown = sorted(name for name in provided if name not in declared_names)
        if unknown:
            names = ", ".join(unknown)
            raise ValueError(f"Unknown parameters: {names}")

        bound = {}
        missing = []
        for item in declared:
            raw = provided.get(item['name'])
            if raw is None or str(raw).strip() == "":
                raw = item.get('default')
            if raw is None or str(raw).strip() == "":
                missing.append(item['name'])
                continue
            bound[item['name']] = self._coerce_parameter(item, raw)

        if missing:
            names = ", ".join(missing)
            raise ValueError(f"Missing parameters: {names}")
        return bound

    def _coerce_parameter(self, spec: Dict[str, Any], raw: Any) -> Any:
        text = str(raw).strip()
        name = spec['name']
        if spec['type'] == 'int':
            try:
                return int(text)
            except ValueError as exc:
                raise ValueError(f"Parameter '{name}' must be an integer.") from exc
        if spec['type'] == 'float':
            try:
                return float(text)
            except ValueError as exc:
                raise ValueError(f"Parameter '{name}' must be a number.") from exc
        return text

    def _substitute_parameters(self, text: str, bound: Dict[str, Any]) -> str:
        if text is None:
            return None

        def replace(match: re.Match) -> str:
            name = match.group(1)
            if name not in bound:
                raise ValueError(f"Unknown parameter ${name}")
            return str(bound[name])

        return re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)', replace, text)

    def _filters_with_parameters(self, filters: Dict[str, QueryFilter],
                                 bound: Dict[str, Any]) -> Dict[str, QueryFilter]:
        bound_filters = {}
        for entity, entity_filter in filters.items():
            bound_filters[entity] = QueryFilter(
                entity=entity_filter.entity,
                attribute=entity_filter.attribute,
                operator=entity_filter.operator,
                value=self._substitute_parameters(entity_filter.value, bound),
                query=self._substitute_parameters(entity_filter.query, bound),
            )
        return bound_filters

    def _source_section(self, entity_name: str, projected_attrs: List[str],
                        entity_filter: QueryFilter, rows: List[Dict]) -> Dict[str, Any]:
        """Describe one entity in the shape of its source system."""
        entity_meta = self.metaschema.entities[entity_name]
        db_info = self.metaschema.databases[entity_meta['database_ref']]

        if db_info['type'] == 'XML':
            xml_filter = self._xml_filter_for_query(entity_name, entity_meta, entity_filter)
            elements = self._select_xml_elements(entity_meta, xml_filter)
            return {
                "entity": entity_name,
                "sourceType": "XML",
                "shape": "hierarchical",
                "tree": [self._element_to_tree(element) for element in elements],
            }

        if rows:
            columns = list(rows[0].keys())
        elif projected_attrs:
            columns = list(projected_attrs)
        else:
            columns = list(entity_meta['attributes'].keys())

        return {
            "entity": entity_name,
            "sourceType": db_info['type'],
            "shape": "tabular",
            "columns": columns,
            "rows": rows,
        }

    def _xml_filter_for_query(self, entity_name: str, entity_meta: Dict,
                              entity_filter: QueryFilter = None) -> QueryFilter:
        if not entity_filter:
            return None
        if not entity_filter.query and entity_filter.attribute and entity_filter.operator:
            return QueryFilter(
                entity=entity_name,
                query=(
                    f"{entity_meta['base_path']} "
                    f"[{entity_filter.attribute} {entity_filter.operator} {entity_filter.value}]"
                ),
            )
        return entity_filter

    def _query_entity(self, entity_name: str, projected_attrs: List[str],
                      entity_filter: QueryFilter = None) -> List[Dict]:
        """Query a single entity using the configured backend and optional filter."""
        entity_meta = self.metaschema.entities[entity_name]
        db_ref = entity_meta['database_ref']
        db_info = self.metaschema.databases[db_ref]

        if db_info['type'] == 'Relational':
            if entity_filter and entity_filter.attribute and entity_filter.operator:
                return self._query_relational_with_sql_filter(
                    entity_name, entity_meta, projected_attrs, entity_filter
                )
            return self._query_relational(entity_name, entity_meta, projected_attrs)

        if db_info['type'] == 'XML':
            xml_filter = self._xml_filter_for_query(entity_name, entity_meta, entity_filter)
            if xml_filter and xml_filter.query:
                return self._query_xml_with_xquery(
                    entity_name, entity_meta, projected_attrs, xml_filter
                )
            return self._query_xml_unfiltered(entity_name, entity_meta, projected_attrs)

        raise ValueError(f"Unsupported database type for entity {entity_name}: {db_info['type']}")

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

    def _select_xml_elements(self, entity_meta: Dict,
                             xml_filter: QueryFilter = None) -> List[ET.Element]:
        """Return XML elements for an entity, applying a bracket filter when present."""
        base_path = entity_meta['base_path'] or ""
        element_name = base_path.lstrip('/').split('/')[-1]
        elements = self.xml_root.findall(element_name)
        if not xml_filter or not xml_filter.query:
            return elements
        if '[' not in xml_filter.query or ']' not in xml_filter.query:
            return elements
        condition_str = xml_filter.query[
            xml_filter.query.index('[') + 1 : xml_filter.query.index(']')
        ]
        return self._filter_xml_elements(elements, condition_str, entity_meta['attributes'])

    def _project_xml_elements(self, elements: List[ET.Element], entity_meta: Dict,
                              projected_attrs: List[str]) -> List[Dict]:
        """Project selected XML elements into flat rows."""
        attributes = entity_meta['attributes']
        if projected_attrs is None:
            projected_attrs = list(attributes.keys())

        results = []
        for elem in elements:
            row = {}
            for attr_name in projected_attrs:
                attr_info = attributes.get(attr_name)
                if attr_info:
                    row[attr_name] = self._get_value_from_xml_element(elem, attr_info['path'])
            results.append(row)
        return results

    def _element_to_tree(self, elem: ET.Element) -> Any:
        """Convert an XML element into a nested document."""
        children = list(elem)
        text = (elem.text or "").strip()
        if not children:
            if elem.attrib:
                node = {"@attributes": dict(elem.attrib)}
                if text:
                    node["#text"] = text
                return node
            return text or None

        node: Dict[str, Any] = {}
        if elem.attrib:
            node["@attributes"] = dict(elem.attrib)
        if text:
            node["#text"] = text
        for child in children:
            value = self._element_to_tree(child)
            if child.tag in node:
                current = node[child.tag]
                if isinstance(current, list):
                    current.append(value)
                else:
                    node[child.tag] = [current, value]
            else:
                node[child.tag] = value
        return node

    def _query_xml_unfiltered(self, entity_name: str, entity_meta: Dict, 
                              projected_attrs: List[str]) -> List[Dict]:
        """Execute unfiltered query on XML database"""
        print(f"[XPath Query] {entity_meta['base_path']}")
        elements = self._select_xml_elements(entity_meta, None)
        return self._project_xml_elements(elements, entity_meta, projected_attrs)
    
    def _query_xml_with_xquery(self, entity_name: str, entity_meta: Dict, 
                               projected_attrs: List[str], xml_filter: QueryFilter) -> List[Dict]:
        """Execute XML query using XPath/XQuery from filter"""
        print(f"[XPath Query] {xml_filter.query}")
        elements = self._select_xml_elements(entity_meta, xml_filter)
        return self._project_xml_elements(elements, entity_meta, projected_attrs)
    
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
                      relationship_refs: List[str], projection: Dict) -> List[Dict]:
        """Join results from multiple entities using the declared relationship chain."""
        if not relationship_refs:
            raise ValueError("Multiple entities require at least one RelationshipRef")

        pending_relationships = list(relationship_refs)
        joined_rows = None
        joined_entities = set()

        while pending_relationships:
            progress_made = False

            for relationship_ref in list(pending_relationships):
                relationship = self.metaschema.relationships[relationship_ref['name']]
                join_type = relationship_ref['type']
                left_entity = relationship['left_entity']
                right_entity = relationship['right_entity']
                left_key, right_key = self._get_join_keys(relationship)

                if joined_rows is None:
                    joined_rows = self._join_row_sets(
                        results.get(left_entity, []),
                        results.get(right_entity, []),
                        left_key,
                        right_key,
                        join_type,
                        left_entity,
                        right_entity,
                        projection,
                    )
                    joined_entities.update({left_entity, right_entity})
                elif left_entity in joined_entities and right_entity not in joined_entities:
                    joined_rows = self._join_row_sets(
                        joined_rows,
                        results.get(right_entity, []),
                        left_key,
                        right_key,
                        join_type,
                        left_entity,
                        right_entity,
                        projection,
                    )
                    joined_entities.add(right_entity)
                elif right_entity in joined_entities and left_entity not in joined_entities:
                    if join_type == 'INNER':
                        joined_rows = self._join_row_sets(
                            joined_rows,
                            results.get(left_entity, []),
                            right_key,
                            left_key,
                            join_type,
                            right_entity,
                            left_entity,
                            projection,
                        )
                    else:
                        joined_rows = self._join_row_sets(
                            results.get(left_entity, []),
                            joined_rows,
                            left_key,
                            right_key,
                            join_type,
                            left_entity,
                            right_entity,
                            projection,
                        )
                    joined_entities.add(left_entity)
                elif left_entity in joined_entities and right_entity in joined_entities:
                    pass
                else:
                    continue

                pending_relationships.remove(relationship_ref)
                progress_made = True
                break

            if not progress_made:
                unresolved = ', '.join(ref['name'] for ref in pending_relationships)
                raise ValueError(
                    f"Could not resolve join path for entities {entities} using relationships: {unresolved}"
                )

        missing_entities = [entity for entity in entities if entity not in joined_entities]
        if missing_entities:
            raise ValueError(f"Entities are not connected by the declared relationships: {missing_entities}")

        return joined_rows or []

    def _get_join_keys(self, relationship: Dict) -> Tuple[str, str]:
        """Resolve the left and right join keys for a relationship."""
        left_entity = relationship['left_entity']
        right_entity = relationship['right_entity']
        condition = relationship['condition']

        if condition:
            left_key = condition['left_attribute']
            right_key = condition['right_attribute']
        else:
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

        return left_key, right_key

    def _read_join_attribute(self, row: Dict[str, Any], entity: str, attribute: str) -> Any:
        """Read a join key from a raw entity row or an already prefixed joined row."""
        qualified = f"{entity}.{attribute}"
        if qualified in row:
            return row[qualified]
        return row.get(attribute)

    def _right_columns(self, right_rows: List[Dict[str, Any]], right_entity: str,
                       projection: Dict) -> List[str]:
        if right_rows:
            return list(right_rows[0].keys())
        projected = projection.get(right_entity) if projection else None
        if projected:
            return list(projected)
        return list(self.metaschema.entities[right_entity]['attributes'].keys())

    def _merge_rows(self, left_row: Dict[str, Any], right_row: Dict[str, Any],
                    left_entity: str, right_entity: str) -> Dict[str, Any]:
        """Combine two rows, prefixing a column only when both sides use that name."""
        collisions = [key for key in right_row if key in left_row]
        merged = dict(left_row)
        for key in collisions:
            merged[f"{left_entity}.{key}"] = merged.pop(key)
            merged[f"{right_entity}.{key}"] = right_row[key]
        for key, value in right_row.items():
            if key not in collisions:
                merged[key] = value
        return merged

    def _join_row_sets(self, left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]],
                       left_key: str, right_key: str, join_type: str,
                       left_entity: str, right_entity: str, projection: Dict) -> List[Dict[str, Any]]:
        """Join two row sets. FULL keeps unmatched rows from both sides."""
        if join_type == 'RIGHT':
            return self._right_join_rows(
                left_rows, right_rows, left_key, right_key, left_entity, right_entity, projection
            )
        if join_type == 'FULL':
            return self._full_join_rows(
                left_rows, right_rows, left_key, right_key, left_entity, right_entity, projection
            )

        right_index = {}
        for right_row in right_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(right_row, right_entity, right_key)
            )
            if join_value is None:
                continue
            right_index.setdefault(join_value, []).append(right_row)

        right_columns = self._right_columns(right_rows, right_entity, projection)
        empty_right = {column: None for column in right_columns}
        joined_rows = []
        for left_row in left_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(left_row, left_entity, left_key)
            )
            matches = right_index.get(join_value, []) if join_value is not None else []

            if join_type == 'SEMI':
                if matches:
                    joined_rows.append(left_row)
            elif join_type == 'ANTI':
                if not matches:
                    joined_rows.append(left_row)
            elif join_type == 'LEFT':
                if matches:
                    for right_row in matches:
                        joined_rows.append(
                            self._merge_rows(left_row, right_row, left_entity, right_entity)
                        )
                else:
                    joined_rows.append(
                        self._merge_rows(left_row, empty_right, left_entity, right_entity)
                    )
            else:
                if join_value is None:
                    continue
                for right_row in matches:
                    joined_rows.append(
                        self._merge_rows(left_row, right_row, left_entity, right_entity)
                    )

        return joined_rows

    def _right_join_rows(self, left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]],
                         left_key: str, right_key: str, left_entity: str, right_entity: str,
                         projection: Dict) -> List[Dict[str, Any]]:
        """Keep every right row, with empty left columns when nothing matches."""
        left_index = {}
        for left_row in left_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(left_row, left_entity, left_key)
            )
            if join_value is None:
                continue
            left_index.setdefault(join_value, []).append(left_row)

        left_columns = self._right_columns(left_rows, left_entity, projection)
        empty_left = {column: None for column in left_columns}
        joined_rows = []
        for right_row in right_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(right_row, right_entity, right_key)
            )
            matches = left_index.get(join_value, []) if join_value is not None else []
            if matches:
                for left_row in matches:
                    joined_rows.append(
                        self._merge_rows(left_row, right_row, left_entity, right_entity)
                    )
            else:
                joined_rows.append(
                    self._merge_rows(empty_left, right_row, left_entity, right_entity)
                )
        return joined_rows

    def _full_join_rows(self, left_rows: List[Dict[str, Any]], right_rows: List[Dict[str, Any]],
                        left_key: str, right_key: str, left_entity: str, right_entity: str,
                        projection: Dict) -> List[Dict[str, Any]]:
        """Keep every left and right row, filling the other side when nothing matches."""
        right_index = {}
        for right_row in right_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(right_row, right_entity, right_key)
            )
            if join_value is None:
                continue
            right_index.setdefault(join_value, []).append(right_row)

        right_columns = self._right_columns(right_rows, right_entity, projection)
        empty_right = {column: None for column in right_columns}
        left_columns = self._right_columns(left_rows, left_entity, projection)
        empty_left = {column: None for column in left_columns}
        matched_right = set()
        joined_rows = []
        for left_row in left_rows:
            join_value = self._normalize_join_value(
                self._read_join_attribute(left_row, left_entity, left_key)
            )
            matches = right_index.get(join_value, []) if join_value is not None else []
            if matches:
                for right_row in matches:
                    matched_right.add(id(right_row))
                    joined_rows.append(
                        self._merge_rows(left_row, right_row, left_entity, right_entity)
                    )
            else:
                joined_rows.append(
                    self._merge_rows(left_row, empty_right, left_entity, right_entity)
                )
        for right_row in right_rows:
            if id(right_row) not in matched_right:
                joined_rows.append(
                    self._merge_rows(empty_left, right_row, left_entity, right_entity)
                )
        return joined_rows

    def _normalize_join_value(self, value: Any) -> Any:
        """Normalize join values so XML strings and SQL ints compare consistently."""
        if value is None:
            return None

        if isinstance(value, str):
            value = value.strip()
            if value == '':
                return value
            try:
                return int(value)
            except (ValueError, TypeError):
                try:
                    return float(value)
                except (ValueError, TypeError):
                    return value

        return value
    
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
