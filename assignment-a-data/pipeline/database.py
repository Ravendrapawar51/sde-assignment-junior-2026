"""Database schema and connection management for the pipeline."""

import sqlite3
import hashlib
from contextlib import contextmanager
from typing import Optional
from datetime import datetime


class Database:
    """SQLite database with schema for leads, events, and reporting."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _hash_phone(self, phone: str) -> str:
        """Hash a phone number for PII protection."""
        return hashlib.sha256(phone.encode('utf-8')).hexdigest()
    
    def _hash_string(self, value: str) -> str:
        """Hash a string value for PII protection."""
        return hashlib.sha256(value.encode('utf-8')).hexdigest()
    
    def _init_schema(self):
        """Initialize all database tables."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Track ingested drops for idempotency
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ingestion_state (
                    drop_id TEXT PRIMARY KEY,
                    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Phone lookup: hashed phone -> canonical phone (for event linking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS phone_lookup (
                    phone_hash TEXT PRIMARY KEY,
                    canonical_phone TEXT NOT NULL
                )
            """)
            
            # Leads table with PII hashed
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    phone_hash TEXT NOT NULL,
                    customer_name_hash TEXT,
                    external_ref TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    policy_expiry DATE,
                    premium_amount DECIMAL(10,2),
                    preferred_language TEXT,
                    product TEXT,
                    order_date DATE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(workflow_id, phone_hash)
                )
            """)
            
            # Rejections table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rejections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    row_number INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    drop_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Append-only event ledger
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    call_id TEXT NOT NULL,
                    occurred_at TIMESTAMP NOT NULL,
                    phone_hash TEXT,
                    lead_id INTEGER,
                    payload TEXT,
                    is_orphan BOOLEAN DEFAULT 0,
                    drop_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Dispositions cache
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dispositions (
                    workflow_id TEXT NOT NULL,
                    code TEXT NOT NULL,
                    label TEXT,
                    is_terminal BOOLEAN,
                    is_success BOOLEAN,
                    PRIMARY KEY (workflow_id, code)
                )
            """)
            
            # Metadata table for tracking_stats
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # Indexes for performance
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_workflow ON leads(workflow_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_batch ON leads(batch_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_workflow ON events(workflow_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_lead ON events(lead_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_orphan ON events(is_orphan)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rejections_drop ON rejections(drop_id)")
            
            conn.commit()
    
    def is_drop_ingested(self, drop_id: str) -> bool:
        """Check if a drop has already been ingested."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM ingestion_state WHERE drop_id = ?", (drop_id,))
            return cursor.fetchone() is not None
    
    def mark_drop_ingested(self, drop_id: str):
        """Mark a drop as ingested."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO ingestion_state (drop_id) VALUES (?)",
                (drop_id,)
            )
    
    def store_phone_lookup(self, phone_hash: str, canonical_phone: str):
        """Store phone hash to canonical mapping."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO phone_lookup (phone_hash, canonical_phone) VALUES (?, ?)",
                (phone_hash, canonical_phone)
            )
    
    def get_canonical_phone(self, phone_hash: str) -> Optional[str]:
        """Get canonical phone from hash."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT canonical_phone FROM phone_lookup WHERE phone_hash = ?",
                (phone_hash,)
            )
            row = cursor.fetchone()
            return row['canonical_phone'] if row else None
    
    def upsert_disposition(self, workflow_id: str, code: str, label: str, 
                           is_terminal: bool, is_success: bool):
        """Insert or update disposition."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO dispositions 
                (workflow_id, code, label, is_terminal, is_success)
                VALUES (?, ?, ?, ?, ?)
            """, (workflow_id, code, label, is_terminal, is_success))
    
    def get_disposition(self, workflow_id: str, code: str) -> Optional[sqlite3.Row]:
        """Get disposition info."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM dispositions 
                WHERE workflow_id = ? AND code = ?
            """, (workflow_id, code))
            return cursor.fetchone()
    
    def set_metadata(self, key: str, value: str):
        """Store a metadata value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)
            """, (key, value))
    
    def get_metadata(self, key: str) -> Optional[str]:
        """Retrieve a metadata value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM metadata WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row['value'] if row else None
    
    def increment_metadata(self, key: str, delta: int = 1):
        """Increment a numeric metadata value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + ?
            """, (key, delta, delta))
