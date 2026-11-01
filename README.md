# XDM Views Query Engine

A federated query engine that joins **relational (SQL)** and **XML** data through one metadata layer.
Data sources, entities, and relationships are described in `MetaSchema.xml`; logical views are described in `views/views.xml`.
A web workbench lets you browse the schema, build and edit views without writing XML, run them, and export the results.

---

## Features

- **Federated joins** across SQL and XML sources: `INNER`, `LEFT`, `RIGHT`, `FULL`, `SEMI`, and `ANTI`.
- **Parameterized views** with filters (column/operator/value for SQL, XPath for XML).
- **Aggregation, sorting, and pagination**: `count`, `sum`, `avg`, `min`, `max`, `HAVING`, sort, limit, and offset.
- **Export** of the full result to CSV, Excel (`.xlsx`), JSON, or a native file (XML stays a tree, SQL stays a table).
- **No-code view builder**: create, edit, rename, and delete views and relationships from the UI.
- **Materialized views**: store a view's full result as a table and use it as an entity in other views.
- **Configurable data sources**: each database declares its own connection in `MetaSchema.xml`, with secrets read from `.env`.

---

## Project structure

```
XDM-Views/
├── dummy_data/
│   ├── create_database.sql      # SQL script for the sample database (MySQL)
│   ├── customers.db             # Sample SQLite database
│   ├── init_database.py         # Rebuilds customers.db
│   └── purchaseorders.xml       # Sample XML data source
├── views/
│   └── views.xml                # View definitions
├── frontend/                    # React + Vite workbench
├── tests/                       # Unit tests
├── MetaSchema.xml               # Databases, connections, entities, relationships
├── query_engine.py              # Loads the XML definitions and runs views
├── sources.py                   # SQLite, MySQL, and XML data sources
├── materialized.py              # Stores view results as tables
├── exporters.py                 # CSV, Excel, JSON, and native export
├── xdm_frontend_server.py       # HTTP API used by the workbench
└── .env                         # Local settings (not committed)
```

---

## Setup

### 1. Python

Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install python-dotenv
```

Add `mysql-connector-python` only if a database in `MetaSchema.xml` uses a `mysql://` connection.

### 2. `.env`

Create `.env` in the project root (see `.env.example`):

```
ENV_BASE_PATH=/absolute/path/to/XDM-Views
ENV_HOST=localhost
ENV_USER=root
ENV_PASSWORD=your_password
ENV_DATABASE=customerdb
```

- `ENV_BASE_PATH` is required. Relative paths in `MetaSchema.xml` connections resolve against it.
- `ENV_HOST`, `ENV_USER`, and `ENV_DATABASE` are required by the API server and shown in the workbench as the relational source.
- Add any other names you reference from `MetaSchema.xml` with `${NAME}` (see below).

### 3. Sample data

`dummy_data/customers.db` is included. To rebuild it:

```bash
.venv/bin/python dummy_data/init_database.py
```

### 4. Frontend

Node.js 18 or newer.

```bash
cd frontend
npm install
```

---

## Running

Start the API (port 8000):

```bash
.venv/bin/python xdm_frontend_server.py --port 8000
```

Start the workbench in a second terminal (port 5173, proxies `/api` to the API):

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

The engine also has a command-line menu:

```bash
.venv/bin/python query_engine.py
```

---

## Using the workbench

1. **Entities**: data sources, entities, and relationships from `MetaSchema.xml`. Click a source to see its connection and test it, an entity to see its columns, or a relationship to edit it. **+ Relationship** adds one. Changes here save straight to `MetaSchema.xml`.
2. **Views**: pick a view to see its entities, joins, filters, parameters, and grouping. **Edit view** opens the editor; **+ New view** creates one; **Materialize** stores the result. Click **Save to views.xml** to keep view changes.
3. **Parameters and shape**: parameter values, grouping, measures, sort, and page size for this run.
4. **Summary and SQL**: a plain-language summary and the equivalent SQL before running.
5. **Results and export**: joined rows, each source in its own shape, and downloads.

---

## Data sources

Each `<Database>` in `MetaSchema.xml` has a `<Connection>`:

```xml
<Database id="DB1">
  <Name>CustomerDB</Name>
  <Type>Relational</Type>
  <Connection url="sqlite:///dummy_data/customers.db"/>
</Database>

<Database id="DB2">
  <Name>PurchaseOrderDB</Name>
  <Type>XML</Type>
  <Connection path="dummy_data/purchaseorders.xml"/>
</Database>
```

For MySQL, keep secrets in `.env` and reference them with `${NAME}`:

```xml
<Connection url="mysql://${CRM_HOST}:3306/crm" username="${CRM_USER}" password="${CRM_PASSWORD}"/>
```

Supported connections are `sqlite:///path`, `mysql://host:port/database`, and `path="file.xml"` for XML.
If a referenced setting is missing, the engine names it, for example `CRM_PASSWORD not set in .env`.
Each source opens only when a view reads from it, and joins across sources happen in the engine.

---

## Materialized views

**Materialize** runs a view without paging and writes the rows to `dummy_data/materialized.db`.
The table is added to `MetaSchema.xml` as an entity named after the view (for example `Orders by city` becomes `OrdersByCityMV`) under the `MV` database.
Use **Refresh** to re-run the view and replace the rows, or **Remove** to drop the table and the entity. Removal is blocked while a view still uses it.

---

## Tests

```bash
.venv/bin/python -m unittest tests.test_query_and_export tests.test_materialized tests.test_relationships tests.test_sources
```
