# XDM Views Query Engine

A federated query engine that integrates **Relational (SQL)** and **XML** data sources using a metadata-driven approach.
It allows users to define views in XML and execute them dynamically without changing code.

---

## 📁 Project Structure

```
XDM-Views-proto4/
│
├── dummy_data/
│   ├── create_database.sql      # SQL script to create database
│   ├── customers.db             # Sample DB (if used)
│   ├── init_database.py         # DB initialization script
│   └── purchaseorders.xml       # XML data source
│
├── views/
│   └── views.xml                # View definitions
│
├── MetaSchema.xml               # Metadata schema
├── query_engine.py              # Main query engine
├── .env                         # Environment configuration
└── README.md
```

---

## ⚙️ Setup Instructions

### 1️⃣ Install Dependencies

```bash
pip install mysql-connector-python python-dotenv
```

---

### 2️⃣ Configure `.env` File

Create or update `.env` in root directory:

```
ENV_HOST=localhost
ENV_USER=root
ENV_PASSWORD=your_password
ENV_DATABASE=customer_db
ENV_BASE_PATH=C:\path\to\XDM-Views-proto4
```

---

### 3️⃣ Setup Database

Run the SQL file:

```bash
mysql -u root -p < dummy_data/create_database.sql
```

Or manually execute it inside MySQL.

---

### 4️⃣ Run the Query Engine

```bash
python query_engine.py
```
