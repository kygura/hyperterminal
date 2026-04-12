from sqlmodel import SQLModel, create_engine, Session
from pathlib import Path
import os

# Get DATABASE_URL from environment (Turso in production)
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
    # Production: Use Turso with sqlalchemy-libsql dialect
    # Format: sqlite+libsql://[your-database-url]?authToken=[your-token]
    
    # Remove any protocol prefix from TURSO_DATABASE_URL if present
    db_url = TURSO_DATABASE_URL.replace("libsql://", "").replace("https://", "")
    
    # Construct the SQLAlchemy connection string
    connection_string = f"sqlite+libsql://{db_url}?authToken={TURSO_AUTH_TOKEN}"
    
    engine = create_engine(connection_string, echo=True, connect_args={"check_same_thread": False})
    print(f"Using Turso database: {db_url}")
else:
    # Development: Fall back to local SQLite file (shared with raw store)
    backend_dir = Path(__file__).parent.parent
    sqlite_file_name = backend_dir / "data.db"
    sqlite_url = f"sqlite:///{sqlite_file_name}"
    connect_args = {"check_same_thread": False}
    engine = create_engine(sqlite_url, echo=True, connect_args=connect_args)
    print(f"Using local SQLite: {sqlite_file_name}")

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
