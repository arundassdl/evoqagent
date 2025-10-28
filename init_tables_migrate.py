#!/usr/bin/env python3
import os

from peewee import SqliteDatabase, Model, CharField,  TextField, DateTimeField, IntegerField, FloatField, OperationalError

# --- Setup SQLite ---
DB_FILE = os.path.join(os.path.dirname(__file__), "jobs.sqlite3")
db = SqliteDatabase(DB_FILE)

# --- Define models ---
class BaseModel(Model):
    class Meta:
        database = db

class JobModel(BaseModel):
    agent_job_id = CharField(unique=True)
    name = CharField(null=True)
    data = TextField(null=True)
    job_type = CharField(null=True)
    enqueue = IntegerField(default=0)
    start = DateTimeField(null=True)
    finished = DateTimeField(null=True)
    error = TextField(null=True)
    worker_id = CharField(null=True)
    duration = FloatField(null=True)
    status = CharField(null=True)
    created_at = DateTimeField(null=True)
    updated_at = DateTimeField(null=True)

class ServerModel(BaseModel):
    name = CharField(unique=True)
    server_type = CharField(null=True)
    last_seen = DateTimeField(null=True)

class WorkerModel(BaseModel):
    worker_id = CharField(unique=True)
    server_name = CharField(null=True)
    status = CharField(null=True)
    last_seen = DateTimeField(null=True)

# --- Helper to add missing columns ---
def migrate_table(model):
    db.connect()
    table_name = model._meta.table_name
    fields = model._meta.fields

    # Create table if it doesn't exist
    db.create_tables([model], safe=True)

    # Get existing columns
    cursor = db.execute_sql(f'PRAGMA table_info({table_name});')
    existing_columns = [row[1] for row in cursor.fetchall()]

    for field_name, field in fields.items():
        if field_name not in existing_columns:
            # Construct SQLite-compatible column SQL
            field_type = ''
            if isinstance(field, CharField):
                field_type = 'VARCHAR'
            elif isinstance(field, DateTimeField):
                field_type = 'DATETIME'
            else:
                field_type = 'TEXT'

            # Nullable?
            nullable = '' if field.null is False else ' NULL'

            try:
                db.execute_sql(f'ALTER TABLE {table_name} ADD COLUMN {field_name} {field_type}{nullable};')
                print(f'Added missing column "{field_name}" to table "{table_name}"')
            except OperationalError as e:
                print(f'Failed to add column "{field_name}" to table "{table_name}": {e}')

    db.close()

# --- Main execution ---
if __name__ == "__main__":
    for model in [JobModel, ServerModel, WorkerModel]:
        migrate_table(model)
    print(f"SQLite tables migrated successfully in {DB_FILE}")

