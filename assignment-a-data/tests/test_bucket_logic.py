"""Tests for bucket logic and effective disposition."""

import pytest
import tempfile
import os
from pathlib import Path

from pipeline.database import Database
from pipeline.reporting import Reporting
from pipeline.validators import parse_timestamp


class TestBucketLogic:
    """Test lead bucket classification."""
    
    def test_yet_to_reach_out(self):
        """Leads with no calls are yet_to_reach_out."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        # Insert a lead with no events
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO leads (workflow_id, phone_hash, external_ref, batch_id)
                VALUES ('test', 'hash123', 'REF001', 'batch_001')
            """)
            lead_id = cursor.lastrowid
        
        bucket = reporting._get_lead_bucket(lead_id)
        assert bucket == "yet_to_reach_out"
    
    def test_completed_terminal(self):
        """Leads with terminal disposition are completed."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        # Insert lead and events
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO leads (workflow_id, phone_hash, external_ref, batch_id)
                VALUES ('motor_renewal', 'hash123', 'REF001', 'batch_001')
            """)
            lead_id = cursor.lastrowid
            
            # Insert disposition
            db.upsert_disposition('motor_renewal', 'renewed', 'Renewed', True, True)
            
            # Insert events for a completed call
            occurred_at = parse_timestamp("2026-10-15T10:30:00Z")
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id, 
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'call.initiated', 'motor_renewal', 'call_001',
                        ?, ?, '{}', 0, 'drop_1')
            """, (occurred_at, lead_id))
            
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_002', 'disposition.set', 'motor_renewal', 'call_001',
                        ?, ?, '{"disposition_code": "renewed"}', 0, 'drop_1')
            """, (occurred_at, lead_id))
        
        bucket = reporting._get_lead_bucket(lead_id)
        assert bucket == "completed"
    
    def test_in_progress_telephony_failed(self):
        """Leads where all calls failed at telephony layer."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO leads (workflow_id, phone_hash, external_ref, batch_id)
                VALUES ('motor_renewal', 'hash123', 'REF001', 'batch_001')
            """)
            lead_id = cursor.lastrowid
            
            occurred_at = parse_timestamp("2026-10-15T10:30:00Z")
            # Call with telephony_failed
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'call.initiated', 'motor_renewal', 'call_001',
                        ?, ?, '{}', 0, 'drop_1')
            """, (occurred_at, lead_id))
            
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_002', 'call.telephony_failed', 'motor_renewal', 'call_001',
                        ?, ?, '{"reason": "carrier_rejected"}', 0, 'drop_1')
            """, (occurred_at, lead_id))
        
        bucket = reporting._get_lead_bucket(lead_id)
        assert bucket == "in_progress_telephony_failed"
    
    def test_in_progress_dialed(self):
        """Leads with calls that were dialed but not terminal."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO leads (workflow_id, phone_hash, external_ref, batch_id)
                VALUES ('motor_renewal', 'hash123', 'REF001', 'batch_001')
            """)
            lead_id = cursor.lastrowid
            
            db.upsert_disposition('motor_renewal', 'callback_requested', 'Callback', False, False)
            
            occurred_at = parse_timestamp("2026-10-15T10:30:00Z")
            # Call with non-terminal disposition
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'call.initiated', 'motor_renewal', 'call_001',
                        ?, ?, '{}', 0, 'drop_1')
            """, (occurred_at, lead_id))
            
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, lead_id, payload, is_orphan, drop_id)
                VALUES ('evt_002', 'disposition.set', 'motor_renewal', 'call_001',
                        ?, ?, '{"disposition_code": "callback_requested"}', 0, 'drop_1')
            """, (occurred_at, lead_id))
        
        bucket = reporting._get_lead_bucket(lead_id)
        assert bucket == "in_progress_dialed"


class TestEffectiveDisposition:
    """Test effective disposition logic."""
    
    def test_disposition_set(self):
        """Effective disposition is disposition.set when no override."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db.upsert_disposition('motor_renewal', 'renewed', 'Renewed', True, True)
            
            occurred_at = parse_timestamp("2026-10-15T10:30:00Z")
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'disposition.set', 'motor_renewal', 'call_001',
                        ?, '{"disposition_code": "renewed"}', 0, 'drop_1')
            """, (occurred_at,))
        
        eff_disp = reporting._get_effective_disposition('call_001')
        assert eff_disp is not None
        assert eff_disp['code'] == 'renewed'
        assert eff_disp['is_success'] is True
    
    def test_override_takes_precedence(self):
        """Override takes precedence over disposition.set."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db.upsert_disposition('motor_renewal', 'not_interested', 'Not Interested', True, False)
            db.upsert_disposition('motor_renewal', 'renewed', 'Renewed', True, True)
            
            t1 = parse_timestamp("2026-10-15T10:30:00Z")
            t2 = parse_timestamp("2026-10-15T12:00:00Z")
            
            # Original disposition
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'disposition.set', 'motor_renewal', 'call_001',
                        ?, '{"disposition_code": "not_interested"}', 0, 'drop_1')
            """, (t1,))
            
            # Override
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, payload, is_orphan, drop_id)
                VALUES ('evt_002', 'disposition.override', 'motor_renewal', 'call_001',
                        ?, '{"disposition_code": "renewed", "overridden_by": "supervisor_1"}', 0, 'drop_1')
            """, (t2,))
        
        eff_disp = reporting._get_effective_disposition('call_001')
        assert eff_disp is not None
        assert eff_disp['code'] == 'renewed'
        assert eff_disp['is_success'] is True
    
    def test_latest_override(self):
        """Latest override wins when multiple exist."""
        db = Database(":memory:")
        reporting = Reporting(db)
        
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db.upsert_disposition('motor_renewal', 'not_interested', 'Not Interested', True, False)
            db.upsert_disposition('motor_renewal', 'renewed', 'Renewed', True, True)
            
            t1 = parse_timestamp("2026-10-15T10:30:00Z")
            t2 = parse_timestamp("2026-10-15T11:00:00Z")
            t3 = parse_timestamp("2026-10-15T12:00:00Z")
            
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, payload, is_orphan, drop_id)
                VALUES ('evt_001', 'disposition.override', 'motor_renewal', 'call_001',
                        ?, '{"disposition_code": "renewed"}', 0, 'drop_1')
            """, (t1,))
            
            cursor.execute("""
                INSERT INTO events (event_id, event_type, workflow_id, call_id,
                                   occurred_at, payload, is_orphan, drop_id)
                VALUES ('evt_002', 'disposition.override', 'motor_renewal', 'call_001',
                        ?, '{"disposition_code": "not_interested"}', 0, 'drop_1')
            """, (t3,))
        
        eff_disp = reporting._get_effective_disposition('call_001')
        assert eff_disp is not None
        assert eff_disp['code'] == 'not_interested'  # Override at t3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
