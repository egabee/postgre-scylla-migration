import json
import logging
import uuid
from datetime import datetime

import psycopg2
from cassandra.cluster import Cluster
from cassandra.query import PreparedStatement

from .tables import column_mapping, pg_config, scylla_config, table_names

logging.basicConfig(level=logging.INFO)


# ---------------------------------------------
# ------------- DB's connection ---------------
# ---------------------------------------------
def get_postgres_connection(database, user, password, host, port):
    return psycopg2.connect(
        database=database, user=user, password=password, host=host, port=port
    )


def get_scylla_connection(contact_points, keyspace):
    return Cluster(contact_points).connect(keyspace)


# -----------------------------------------------------------------------------------
# -----------------------------------------------------------------------------------


# ---------------------------------------------
# ------------- get and insert data -----------
# ---------------------------------------------
def fetch_data_from_postgres(pg_cursor, table_name):
    pg_cursor.execute(f"SELECT * FROM {table_name}")
    columns = [desc[0] for desc in pg_cursor.description]
    rows = pg_cursor.fetchall()
    return columns, rows


def prepare_and_insert_data_into_scylla(
    session, table_name, columns, rows, column_mapping
):
    scylla_columns = [column_mapping.get(col, col) for col in columns]

    insert_query = f"INSERT INTO {table_name} ({', '.join(scylla_columns)}) VALUES ({', '.join(['?' for _ in scylla_columns])})"
    prepared = session.prepare(insert_query)

    for row in rows:
        row_data = []
        # do some handeling and checks for data bcz diff types
        for i, value in enumerate(row):
            column = columns[i]
            if isinstance(value, uuid.UUID):
                row_data.append(value)
            elif isinstance(value, str) and len(value) == 36:
                try:
                    row_data.append(uuid.UUID(value))
                except ValueError:
                    row_data.append(value)
            elif isinstance(value, datetime):
                row_data.append(value)
            elif isinstance(value, dict):
                row_data.append(json.dumps(value))
            else:
                row_data.append(value)

        if any(pk is None or pk == "" for pk in row_data):
            logging.warning(f"Skipping row with empty primary key: {row_data}")
            continue

        logging.info(f"Row data to insert: {row_data}")
        session.execute(prepared, row_data)


def migrate_table(pg_cursor, scylla_session, table_name, column_mapping):
    columns, rows = fetch_data_from_postgres(pg_cursor, table_name)
    prepare_and_insert_data_into_scylla(
        scylla_session, table_name, columns, rows, column_mapping
    )
    print(f"Data from table {table_name} migrated successfully.")


def main():
    tables = table_names

    pg_conn = get_postgres_connection(**pg_config)
    pg_cursor = pg_conn.cursor()
    scylla_session = get_scylla_connection(
        scylla_config["contact_points"], scylla_config["keyspace"]
    )

    for table in tables:
        migrate_table(pg_cursor, scylla_session, table, column_mapping.get(table, {}))

    pg_cursor.close()
    pg_conn.close()
    scylla_session.shutdown()
