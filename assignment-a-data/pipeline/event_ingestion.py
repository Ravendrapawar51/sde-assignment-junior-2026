"""Event ingestion with deduplication, linking, and orphan handling."""

import json
from pathlib import Path
from typing import Dict, Optional, Set, Tuple
from datetime import datetime

from .database import Database
from .validators import normalize_phone, parse_timestamp, to_ist, ist_date


class EventIngestion:
    """Handle event file ingestion with dedupe and linking."""
    
    def __init__(self, db: Database, dispositions_path: str):
        self.db = db
        self.dispositions_path = dispositions_path
        self.stats = {
            'events_total': 0,
            'malformed_events': 0,
            'duplicate_events_dropped': 0,
            'orphan_events': 0,
            'unknown_disposition_events': 0
        }
        self._load_dispositions()
    
    def _load_dispositions(self):
        """Load disposition catalogue from CSV."""
        import csv
        with open(self.dispositions_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.db.upsert_disposition(
                    workflow_id=row['workflow_id'],
                    code=row['code'],
                    label=row['label'],
                    is_terminal=row['is_terminal'].lower() == 'true',
                    is_success=row['is_success'].lower() == 'true'
                )
    
    def _find_lead_by_phone(self, workflow_id: str, phone: str) -> Optional[int]:
        """Find lead ID by workflow and phone number."""
        # Normalize phone
        canonical, is_valid = normalize_phone(phone)
        if not is_valid:
            return None
        
        phone_hash = self.db._hash_phone(canonical)
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id FROM leads 
                WHERE workflow_id = ? AND phone_hash = ?
            """, (workflow_id, phone_hash))
            row = cursor.fetchone()
            return row['id'] if row else None
    
    def _find_lead_by_call_id(self, call_id: str) -> Optional[int]:
        """Find lead ID by call_id (for disposition.override events)."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT lead_id FROM events 
                WHERE call_id = ? AND lead_id IS NOT NULL
                LIMIT 1
            """, (call_id,))
            row = cursor.fetchone()
            return row['lead_id'] if row else None
    
    def _is_event_duplicate(self, event_id: str) -> bool:
        """Check if event_id already exists."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,))
            return cursor.fetchone() is not None
    
    def _is_disposition_known(self, workflow_id: str, code: str) -> bool:
        """Check if disposition code is known for workflow."""
        disposition = self.db.get_disposition(workflow_id, code)
        return disposition is not None
    
    def ingest_file(self, file_path: str, drop_id: str):
        """Ingest a single event JSONL file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                
                self.stats['events_total'] += 1
                
                # Parse JSON
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    self.stats['malformed_events'] += 1
                    self.db.increment_metadata('malformed_events')
                    continue
                
                # Check for event_id
                if 'event_id' not in event:
                    self.stats['malformed_events'] += 1
                    self.db.increment_metadata('malformed_events')
                    continue
                
                event_id = event['event_id']
                
                # Check for duplicate
                if self._is_event_duplicate(event_id):
                    self.stats['duplicate_events_dropped'] += 1
                    self.db.increment_metadata('duplicate_events_dropped')
                    continue
                
                # Validate required fields
                required_fields = ['event_type', 'workflow_id', 'call_id', 'occurred_at']
                if not all(field in event for field in required_fields):
                    self.stats['malformed_events'] += 1
                    self.db.increment_metadata('malformed_events')
                    continue
                
                # Parse timestamp
                occurred_at = parse_timestamp(event['occurred_at'])
                if occurred_at is None:
                    self.stats['malformed_events'] += 1
                    self.db.increment_metadata('malformed_events')
                    continue
                
                # Handle disposition events
                event_type = event['event_type']
                workflow_id = event['workflow_id']
                
                if event_type.startswith('disposition.'):
                    disposition_code = event.get('payload', {}).get('disposition_code')
                    if disposition_code and not self._is_disposition_known(workflow_id, disposition_code):
                        self.stats['unknown_disposition_events'] += 1
                        self.db.increment_metadata('unknown_disposition_events')
                        continue
                
                # Link to lead
                lead_id = None
                phone_hash = None
                
                if event_type == 'disposition.override':
                    # Link via call_id
                    lead_id = self._find_lead_by_call_id(event['call_id'])
                else:
                    # Link via phone
                    phone = event.get('phone')
                    if phone:
                        lead_id = self._find_lead_by_phone(workflow_id, phone)
                        if lead_id:
                            canonical, _ = normalize_phone(phone)
                            phone_hash = self.db._hash_phone(canonical)
                
                is_orphan = lead_id is None
                if is_orphan:
                    self.stats['orphan_events'] += 1
                    self.db.increment_metadata('orphan_events')
                
                # Store event
                self._store_event(event, lead_id, phone_hash, is_orphan, drop_id, occurred_at)
    
    def _store_event(self, event: Dict, lead_id: Optional[int], 
                     phone_hash: Optional[str], is_orphan: bool, 
                     drop_id: str, occurred_at: datetime):
        """Store an event in the ledger."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO events (
                    event_id, event_type, workflow_id, call_id, occurred_at,
                    phone_hash, lead_id, payload, is_orphan, drop_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event['event_id'],
                event['event_type'],
                event['workflow_id'],
                event['call_id'],
                occurred_at,
                phone_hash,
                lead_id,
                json.dumps(event.get('payload', {})),
                is_orphan,
                drop_id
            ))
    
    def relink_orphans(self):
        """Relink orphan events after new leads have been ingested."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Find orphan events that might now have a lead
            cursor.execute("""
                SELECT id, event_id, event_type, workflow_id, call_id, phone_hash, drop_id
                FROM events 
                WHERE is_orphan = 1 AND event_type != 'disposition.override'
            """)
            
            orphans = cursor.fetchall()
            relinked_count = 0
            
            for orphan in orphans:
                lead_id = None
                event_id = orphan['event_id']
                event_type = orphan['event_type']
                workflow_id = orphan['workflow_id']
                phone_hash = orphan['phone_hash']
                
                if phone_hash:
                    # Try to find lead by phone hash
                    cursor.execute("""
                        SELECT id FROM leads 
                        WHERE workflow_id = ? AND phone_hash = ?
                    """, (workflow_id, phone_hash))
                    row = cursor.fetchone()
                    if row:
                        lead_id = row['id']
                
                if lead_id:
                    # Update the event
                    cursor.execute("""
                        UPDATE events 
                        SET lead_id = ?, is_orphan = 0 
                        WHERE id = ?
                    """, (lead_id, orphan['id']))
                    relinked_count += 1
            
            # Update orphan count
            if relinked_count > 0:
                self.stats['orphan_events'] -= relinked_count
                self.db.increment_metadata('orphan_events', -relinked_count)
            
            conn.commit()
            return relinked_count
    
    def ingest_drop(self, drop_path: str, drop_id: str):
        """Ingest all event files from a drop."""
        events_dir = Path(drop_path) / 'events'
        
        for event_file in sorted(events_dir.glob('*.jsonl')):
            self.ingest_file(str(event_file), drop_id)
