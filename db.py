import sqlite3

def get_db():
    conn = sqlite3.connect("calls.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Remove WAL mode to avoid potential visibility issues
    # conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        call_sid TEXT,
        user_text TEXT,
        reply TEXT,
        status TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        latency REAL,
        decision TEXT,
        language TEXT,
        confidence TEXT DEFAULT 'unknown'
    )
    """)
    conn.commit()

    # Backfill columns for older databases that were created before schema expansion.
    existing_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(calls)").fetchall()
    }

    required_columns = {
        "latency": "ALTER TABLE calls ADD COLUMN latency REAL",
        "decision": "ALTER TABLE calls ADD COLUMN decision TEXT",
        "language": "ALTER TABLE calls ADD COLUMN language TEXT",
        "confidence": "ALTER TABLE calls ADD COLUMN confidence TEXT DEFAULT 'unknown'",
    }

    for col_name, ddl in required_columns.items():
        if col_name not in existing_cols:
            conn.execute(ddl)

    conn.commit()
    
    # 2) Create indexes for snappy filters
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls(created_at DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_status ON calls(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_sid ON calls(call_sid);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_decision ON calls(decision);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_calls_language ON calls(language);")
    conn.commit()
    conn.close()
