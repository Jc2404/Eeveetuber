# Database migrations

The SQLAlchemy models are the runtime mapping; Alembic revisions are the durable
schema history. Configure an Alembic `sqlalchemy.url` for the selected local data
file, then run `alembic upgrade head`. Application startup may call
`SqliteStore.initialize()` for an empty developer/test database, but deployed
databases should be advanced with Alembic before the runtime opens them.

The initial revision enables SQLite FTS5 virtual tables. A platform SQLite build
without FTS5 can still use the canonical tables through runtime initialization,
but the migration should be adapted or FTS5 supplied before deployment.

For a non-default database, set `EEVEETUBER_DATABASE_URL` before running Alembic. Example:

```powershell
$env:EEVEETUBER_DATABASE_URL = "sqlite+pysqlite:///D:/path/to/eeveetuber.db"
uv run alembic upgrade head
```
