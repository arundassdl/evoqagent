#!/usr/bin/env python3
import os
import sys
import json
from peewee import SqliteDatabase, Model, CharField, TextField, IntegerField, BooleanField, DateTimeField

# --- Setup SQLite ---
DB_FILE = os.path.join(os.path.dirname(__file__), "jobs.sqlite3")
db = SqliteDatabase(DB_FILE)

# --- Define models based on Press Agent defaults ---
class BaseModel(Model):
    class Meta:
        database = db

class JobModel(BaseModel):
    agent_job_id = CharField(unique=True)
    name = CharField()
    job_type = CharField()
    status = CharField()
    created_at = DateTimeField()
    updated_at = DateTimeField()

class ServerModel(BaseModel):
    name = CharField(unique=True)
    server_type = CharField()
    last_seen = DateTimeField()

class WorkerModel(BaseModel):
    worker_id = CharField(unique=True)
    server_name = CharField()
    status = CharField()
    last_seen = DateTimeField()

# --- Connect and create tables ---
db.connect()
db.create_tables([JobModel, ServerModel, WorkerModel], safe=True)
db.close()

print(f"SQLite tables created successfully in {DB_FILE}")
