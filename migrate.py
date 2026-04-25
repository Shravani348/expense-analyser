"""
Run this ONCE to set up new schema.
Delete expense.db first, then run:
    python migrate.py
"""
from database import create_tables

print("Creating new database with:")
print("  ✅ Normalized categories table")
print("  ✅ CHECK constraints")
print("  ✅ CASCADE DELETE foreign keys")
print("  ✅ Indexes for fast queries")
print("  ✅ Views for JOIN queries")
print("")

create_tables()

print("")
print("Done! Now run: python app.py")
print("Register a new account to get started.")


