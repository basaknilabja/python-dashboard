import os
import psycopg2
from psycopg2.extras import RealDictCursor

from dotenv import load_dotenv

load_dotenv()  # 👈 THIS IS THE KEY LINE


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    conn = psycopg2.connect(database_url)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO public")

    return conn
