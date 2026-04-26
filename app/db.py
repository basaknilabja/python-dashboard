import os
import psycopg2
from psycopg2.extras import RealDictCursor

from dotenv import load_dotenv
DATABASE_URL="postgresql://neondb_owner:npg_UqgwEu36Fzrf@ep-young-heart-amdkqvlx-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

load_dotenv()  # 👈 THIS IS THE KEY LINE

def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    conn = psycopg2.connect(database_url)
    with conn.cursor() as cur:
        cur.execute("SET search_path TO public")

    return conn
