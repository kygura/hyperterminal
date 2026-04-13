from sqlmodel import SQLModel, create_engine, Session
import os

from dotenv import load_dotenv

from db.paths import resolve_sqlalchemy_sqlite_url

load_dotenv()

# Get DATABASE_URL from environment (or Turso/libSQL) when configured.
DATABASE_URL = os.getenv("DATABASE_URL")
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if DATABASE_URL:
    engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
    print("Using configured DATABASE_URL")
elif TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
    # Production: Use Turso with sqlalchemy-libsql dialect
    # Format: sqlite+libsql://[your-database-url]?authToken=[your-token]
    
    # Remove any protocol prefix from TURSO_DATABASE_URL if present
    db_url = TURSO_DATABASE_URL.replace("libsql://", "").replace("https://", "")
    
    # Construct the SQLAlchemy connection string
    connection_string = f"sqlite+libsql://{db_url}?authToken={TURSO_AUTH_TOKEN}"
    
    engine = create_engine(connection_string, echo=False, connect_args={"check_same_thread": False})
    print(f"Using Turso database: {db_url}")
else:
    # Development/self-hosting: use the shared SQLite file.
    sqlite_url = resolve_sqlalchemy_sqlite_url()
    connect_args = {"check_same_thread": False}
    engine = create_engine(sqlite_url, echo=False, connect_args=connect_args)
    print(f"Using local SQLite: {sqlite_url}")

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
