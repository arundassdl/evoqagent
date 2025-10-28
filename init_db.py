from agent import models

# Connect to SQLite
models.db.connect()

# Create tables if they don't exist
models.db.create_tables([models.JobModel, models.ServerModel, models.WorkerModel], safe=True)

print("Tables created successfully!")

# Close connection
models.db.close()
