import duckdb

conn = duckdb.connect("output/payments.duckdb", read_only=True)

tables = conn.execute("SHOW TABLES").fetchall()

print("Available tables:")
for table in tables:
    print(table[0])

conn.close()