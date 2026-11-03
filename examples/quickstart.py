from originchain import Client

with Client.from_env() as db:
    result = db.sql.run("SELECT $1 AS value", [7])
    print(result.rows)
