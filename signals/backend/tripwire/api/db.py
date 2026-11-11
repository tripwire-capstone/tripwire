"""Database connections for the API: one short-lived connection per request."""

import os
import psycopg

def get_conn():
    """Opens a connection for one request and closes it afterwards. Tests replace this."""
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        yield conn