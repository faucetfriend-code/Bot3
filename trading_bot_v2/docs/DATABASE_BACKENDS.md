# Database Backend Integration Guide

## Overview

`database.py` now supports both **SQLite** (default) and **PostgreSQL + TimescaleDB** backends. The switch is controlled by the `DATABASE_BACKEND` environment variable.

All existing code continues to work unchanged -- the backend switching is transparent.

## Quick Start

### SQLite (Default - Zero Config)

```bash
# No configuration needed
python -m trading_bot_v2.api_server
```

### PostgreSQL

```bash
# Set environment variables
export DATABASE_BACKEND=postgres
export PG_HOST=localhost
export PG_PORT=5432
export PG_DATABASE=trading_bot
export PG_USER=postgres
export PG_PASSWORD=your_password

# Optional: connection pool settings
export PG_POOL_MIN=2
export PG_POOL_MAX=10
export PG_POOL_TIMEOUT=30

# Start the bot
python -m trading_bot_v2.api_server
```

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_BACKEND` | `sqlite` | Backend: `sqlite` or `postgres` |
| `DATABASE_PATH` | `data/trading_bot.db` | SQLite file path (ignored for PostgreSQL) |
| `PG_HOST` | `localhost` | PostgreSQL host |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_DATABASE` | `trading_bot` | PostgreSQL database name |
| `PG_USER` | `postgres` | PostgreSQL user |
| `PG_PASSWORD` | `""` | PostgreSQL password |
| `PG_SCHEMA` | `public` | PostgreSQL schema |
| `PG_POOL_MIN` | `2` | Minimum pool connections |
| `PG_POOL_MAX` | `10` | Maximum pool connections |
| `PG_POOL_TIMEOUT` | `30` | Connection timeout (seconds) |

## Architecture

```
get_db_connection()
    |
    +-- SQLite Backend
    |     _ConnectionPool (thread-safe)
    |     _ConnectionWrapper (? placeholders)
    |     _CursorWrapper (lastrowid via sqlite3)
    |
    +-- PostgreSQL Backend
          PostgreSQLPool (psycopg2.pool.ThreadedConnectionPool)
          _ConnectionWrapper (? -> %s translation)
          _CursorWrapper (lastrowid via RETURNING id)
```

### SQL Translation Layer

The `_ConnectionWrapper` transparently translates SQLite SQL to PostgreSQL:

| SQLite Syntax | PostgreSQL Translation |
|---------------|----------------------|
| `?` placeholders | `%s` placeholders |
| `INSERT OR REPLACE INTO` | `INSERT ... ON CONFLICT ... DO UPDATE` |
| `conn.executescript()` | Split into individual `conn.execute()` calls |
| `cursor.lastrowid` | `RETURNING id` + fetch result |
| `conn.row_factory = sqlite3.Row` | Ignored (PostgreSQL returns tuples) |
| `sqlite_master` | `information_schema.tables` |
| `VACUUM INTO` | `pg_dump` or JSON export |

### Connection Wrapper Classes

- **`_ConnectionWrapper`**: Wraps both SQLite and PostgreSQL connections with a unified interface
- **`_CursorWrapper`**: Wraps cursors to provide `lastrowid` for both backends
- **`PostgreSQLPool`**: Thread-safe connection pool using `psycopg2.pool.ThreadedConnectionPool`

## Migration from SQLite to PostgreSQL

### 1. Setup PostgreSQL

```sql
-- Create database
CREATE DATABASE trading_bot;

-- Install TimescaleDB extension (optional but recommended)
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
```

### 2. Run Migration Script

```bash
cd Bot3
python scripts/migrate_sqlite_to_pg.py
```

### 3. Verify Migration

```bash
python scripts/verify_migration.py
```

### 4. Switch Backend

```bash
export DATABASE_BACKEND=postgres
python -m trading_bot_v2.api_server
```

## Backup and Restore

### SQLite

```python
db.backup_database("backups/backup.db")
db.restore_database("backups/backup.db")
```

### PostgreSQL

```python
# Uses pg_dump if available, falls back to JSON export
db.backup_database("backups/backup.sql")

# Restore from pg_dump or JSON
db.restore_database("backups/backup.sql")
```

## API Changes

### New Exports

```python
from trading_bot_v2.database import (
    get_backend,          # Returns 'sqlite' or 'postgres'
    is_postgres,          # Returns True if PostgreSQL backend
    get_db_connection,    # Context manager (unchanged)
    DatabaseManager,      # Class (unchanged API)
)
```

### DatabaseManager Backend Property

```python
db = DatabaseManager()
print(db.backend)  # 'sqlite' or 'postgres'
```

## Testing

### Run with SQLite (default)

```bash
pytest trading_bot_v2/tests/
```

### Run with PostgreSQL

```bash
DATABASE_BACKEND=postgres \
PG_HOST=localhost \
PG_DATABASE=trading_bot_test \
pytest trading_bot_v2/tests/
```

## Troubleshooting

### PostgreSQL Connection Errors

```
ERROR: Failed to create PostgreSQL connection pool: could not connect to server
```

**Solutions:**
1. Verify PostgreSQL is running: `pg_isready -h localhost`
2. Check connection parameters: `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`
3. Verify pg_hba.conf allows local connections
4. Check firewall rules for remote connections

### Schema Initialization Errors

```
ERROR: relation "trades" already exists
```

**Solution:** This is expected during migration. The schema is created with `IF NOT EXISTS` clauses.

### Performance Issues

- Ensure TimescaleDB is installed for PostgreSQL
- Verify hypertables are created: `SELECT * FROM timescaledb_information.hypertables;`
- Check compression policies: `SELECT * FROM timescaledb_information.compression_policies;`
