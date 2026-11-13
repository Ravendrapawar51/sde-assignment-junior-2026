"""Report generation for the pipeline."""

import csv
import json
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

from .database import Database
from .validators import to_ist, ist_date, ist_iso_format, parse_timestamp


class Reporting:
    """Generate all required reports from the database."""
    
    def __init__(self, db: Database):
        self.db = db
    
    def _get_effective_disposition(self, call_id: str) -> Optional[Dict]:
        """
        Get the effective disposition for a call.
        Latest disposition.override by occurred_at, or latest disposition.set if no override.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # First, try to find the latest override
            cursor.execute("""
                SELECT payload, occurred_at 
                FROM events 
                WHERE call_id = ? AND event_type = 'disposition.override'
                ORDER BY occurred_at DESC
                LIMIT 1
            """, (call_id,))
            override = cursor.fetchone()
            
            if override:
                payload = json.loads(override['payload'])
                code = payload.get('disposition_code')
                disposition = self.db.get_disposition(
                    cursor.execute("SELECT DISTINCT workflow_id FROM events WHERE call_id = ? LIMIT 1", (call_id,)).fetchone()['workflow_id'],
                    code
                )
                if disposition:
                    return {
                        'code': code,
                        'is_terminal': disposition['is_terminal'],
                        'is_success': disposition['is_success']
                    }
            
            # No override, get latest disposition.set
            cursor.execute("""
                SELECT payload, workflow_id 
                FROM events 
                WHERE call_id = ? AND event_type = 'disposition.set'
                ORDER BY occurred_at DESC
                LIMIT 1
            """, (call_id,))
            disposition_set = cursor.fetchone()
            
            if disposition_set:
                payload = json.loads(disposition_set['payload'])
                code = payload.get('disposition_code')
                disposition = self.db.get_disposition(disposition_set['workflow_id'], code)
                if disposition:
                    return {
                        'code': code,
                        'is_terminal': disposition['is_terminal'],
                        'is_success': disposition['is_success']
                    }
            
            return None
    
    def _get_lead_bucket(self, lead_id: int) -> str:
        """
        Determine a lead's bucket based on call states.
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if lead has any calls (call.initiated events)
            cursor.execute("""
                SELECT COUNT(*) as count FROM events 
                WHERE lead_id = ? AND event_type = 'call.initiated'
            """, (lead_id,))
            call_count = cursor.fetchone()['count']
            
            if call_count == 0:
                return 'yet_to_reach_out'
            
            # Check if any call has a terminal effective disposition
            cursor.execute("""
                SELECT DISTINCT call_id FROM events 
                WHERE lead_id = ? AND event_type = 'call.initiated'
            """, (lead_id,))
            call_ids = [row['call_id'] for row in cursor.fetchall()]
            
            has_terminal = False
            all_telephony_failed = True
            
            for call_id in call_ids:
                eff_disp = self._get_effective_disposition(call_id)
                if eff_disp and eff_disp['is_terminal']:
                    has_terminal = True
                    break
                
                # Check if this call has telephony_failed
                cursor.execute("""
                    SELECT 1 FROM events 
                    WHERE call_id = ? AND event_type = 'call.telephony_failed'
                    LIMIT 1
                """, (call_id,))
                if cursor.fetchone() is None:
                    all_telephony_failed = False
            
            if has_terminal:
                return 'completed'
            elif all_telephony_failed:
                return 'in_progress_telephony_failed'
            else:
                return 'in_progress_dialed'
    
    def _mask_phone(self, canonical_phone: str) -> str:
        """Mask phone number as +91******XXXX."""
        if len(canonical_phone) == 13 and canonical_phone.startswith('+91'):
            return f"+91******{canonical_phone[-4:]}"
        return canonical_phone
    
    def generate_batch_summary(self) -> List[Dict]:
        """Generate batch_summary.csv data."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get all leads with their buckets
            cursor.execute("""
                SELECT workflow_id, batch_id, id
                FROM leads
                ORDER BY workflow_id, batch_id
            """)
            
            leads = cursor.fetchall()
            
            # Aggregate by workflow and batch
            summary = {}
            for lead in leads:
                key = (lead['workflow_id'], lead['batch_id'])
                if key not in summary:
                    summary[key] = {
                        'workflow_id': lead['workflow_id'],
                        'batch_id': lead['batch_id'],
                        'total_leads': 0,
                        'yet_to_reach_out': 0,
                        'in_progress_telephony_failed': 0,
                        'in_progress_dialed': 0,
                        'completed': 0
                    }
                
                summary[key]['total_leads'] += 1
                bucket = self._get_lead_bucket(lead['id'])
                summary[key][bucket] += 1
            
            # Convert to sorted list
            result = list(summary.values())
            result.sort(key=lambda x: (x['workflow_id'], x['batch_id']))
            return result
    
    def generate_daily_overview(self) -> List[Dict]:
        """Generate daily_overview.csv data."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Get all call.initiated events with lead info
            cursor.execute("""
                SELECT DISTINCT 
                    e.workflow_id,
                    e.occurred_at,
                    e.call_id,
                    e.lead_id
                FROM events e
                WHERE e.event_type = 'call.initiated' AND e.lead_id IS NOT NULL
                ORDER BY e.workflow_id, e.occurred_at
            """)
            
            calls = cursor.fetchall()
            
            # Aggregate by workflow and date
            summary = {}
            for call in calls:
                # Parse occurred_at from string to datetime
                occurred_at = parse_timestamp(call['occurred_at']) if isinstance(call['occurred_at'], str) else call['occurred_at']
                ist_date_str = ist_date(occurred_at)
                key = (call['workflow_id'], ist_date_str)
                if key not in summary:
                    summary[key] = {
                        'workflow_id': call['workflow_id'],
                        'date': ist_date_str,
                        'total_calls': 0,
                        'connected_calls': 0,
                        'success_leads': set()
                    }
                
                summary[key]['total_calls'] += 1
                
                # Check if connected
                cursor.execute("""
                    SELECT 1 FROM events 
                    WHERE call_id = ? AND event_type = 'call.connected'
                    LIMIT 1
                """, (call['call_id'],))
                if cursor.fetchone():
                    summary[key]['connected_calls'] += 1
                
                # Check if effective disposition is success
                eff_disp = self._get_effective_disposition(call['call_id'])
                if eff_disp and eff_disp['is_success']:
                    summary[key]['success_leads'].add(call['lead_id'])
            
            # Convert to list and calculate connectivity rate
            result = []
            for key, data in summary.items():
                connectivity_rate = data['connected_calls'] / data['total_calls'] if data['total_calls'] > 0 else 0
                result.append({
                    'workflow_id': data['workflow_id'],
                    'date': data['date'],
                    'total_calls': data['total_calls'],
                    'connected_calls': data['connected_calls'],
                    'connectivity_rate': f"{connectivity_rate:.4f}",
                    'success_leads': len(data['success_leads'])
                })
            
            result.sort(key=lambda x: (x['workflow_id'], x['date']))
            return result
    
    def generate_lead_report(self) -> List[Dict]:
        """Generate lead_report.csv data."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, workflow_id, external_ref, batch_id, phone_hash
                FROM leads
                ORDER BY workflow_id, external_ref
            """)
            
            leads = cursor.fetchall()
            result = []
            
            for lead in leads:
                lead_id = lead['id']
                
                # Get phone for masking
                canonical_phone = self.db.get_canonical_phone(lead['phone_hash'])
                phone_masked = self._mask_phone(canonical_phone) if canonical_phone else ''
                
                # Get bucket
                bucket = self._get_lead_bucket(lead_id)
                
                # Get call stats
                cursor.execute("""
                    SELECT COUNT(*) as count FROM events 
                    WHERE lead_id = ? AND event_type = 'call.initiated'
                """, (lead_id,))
                total_calls = cursor.fetchone()['count']
                
                cursor.execute("""
                    SELECT COUNT(*) as count FROM events e
                    JOIN events c ON e.call_id = c.call_id
                    WHERE e.lead_id = ? AND c.event_type = 'call.connected'
                """, (lead_id,))
                connected_calls = cursor.fetchone()['count']
                
                # Get last call time
                cursor.execute("""
                    SELECT occurred_at FROM events 
                    WHERE lead_id = ? AND event_type = 'call.initiated'
                    ORDER BY occurred_at DESC
                    LIMIT 1
                """, (lead_id,))
                last_call_row = cursor.fetchone()
                last_call_at = ''
                if last_call_row:
                    occurred_at = parse_timestamp(last_call_row['occurred_at']) if isinstance(last_call_row['occurred_at'], str) else last_call_row['occurred_at']
                    last_call_at = ist_iso_format(occurred_at)
                
                # Get current disposition (most recent call with disposition)
                cursor.execute("""
                    SELECT call_id FROM events 
                    WHERE lead_id = ? AND event_type = 'call.initiated'
                    ORDER BY occurred_at DESC
                    LIMIT 1
                """, (lead_id,))
                last_call = cursor.fetchone()
                current_disposition = ''
                if last_call:
                    eff_disp = self._get_effective_disposition(last_call['call_id'])
                    if eff_disp:
                        current_disposition = eff_disp['code']
                
                # Check ever_success
                cursor.execute("""
                    SELECT DISTINCT call_id FROM events 
                    WHERE lead_id = ? AND event_type = 'call.initiated'
                """, (lead_id,))
                call_ids = [row['call_id'] for row in cursor.fetchall()]
                ever_success = False
                for call_id in call_ids:
                    eff_disp = self._get_effective_disposition(call_id)
                    if eff_disp and eff_disp['is_success']:
                        ever_success = True
                        break
                
                result.append({
                    'workflow_id': lead['workflow_id'],
                    'external_ref': lead['external_ref'],
                    'batch_id': lead['batch_id'],
                    'phone_masked': phone_masked,
                    'bucket': bucket,
                    'total_calls': total_calls,
                    'connected_calls': connected_calls,
                    'last_call_at': last_call_at,
                    'current_disposition': current_disposition,
                    'ever_success': ever_success
                })
            
            return result
    
    def generate_rejections(self) -> List[Dict]:
        """Generate rejections.csv data."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT workflow_id, batch_id, row_number, reason
                FROM rejections
                ORDER BY workflow_id, batch_id, row_number
            """)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def generate_data_quality(self, lead_stats: Dict, event_stats: Dict) -> Dict:
        """Generate data_quality.json data."""
        # Get final orphan count (after relinking)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) as count FROM events WHERE is_orphan = 1")
            final_orphans = cursor.fetchone()['count']
        
        return {
            'lead_rows_total': lead_stats['total_rows'],
            'lead_rows_rejected': lead_stats['rejected_rows'],
            'rejections_by_reason': lead_stats['rejections_by_reason'],
            'duplicate_lead_rows_merged': lead_stats['duplicate_merges'],
            'field_warnings': lead_stats['field_warnings'],
            'events_total': event_stats['events_total'],
            'malformed_events': event_stats['malformed_events'],
            'duplicate_events_dropped': event_stats['duplicate_events_dropped'],
            'orphan_events': final_orphans,
            'unknown_disposition_events': event_stats['unknown_disposition_events']
        }
    
    def write_reports(self, output_dir: str, lead_stats: Dict, event_stats: Dict):
        """Write all report files to the output directory."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # batch_summary.csv
        batch_summary = self.generate_batch_summary()
        with open(output_path / 'batch_summary.csv', 'w', newline='', encoding='utf-8') as f:
            if batch_summary:
                writer = csv.DictWriter(f, fieldnames=[
                    'workflow_id', 'batch_id', 'total_leads',
                    'yet_to_reach_out', 'in_progress_telephony_failed',
                    'in_progress_dialed', 'completed'
                ])
                writer.writeheader()
                writer.writerows(batch_summary)
        
        # daily_overview.csv
        daily_overview = self.generate_daily_overview()
        with open(output_path / 'daily_overview.csv', 'w', newline='', encoding='utf-8') as f:
            if daily_overview:
                writer = csv.DictWriter(f, fieldnames=[
                    'workflow_id', 'date', 'total_calls',
                    'connected_calls', 'connectivity_rate', 'success_leads'
                ])
                writer.writeheader()
                writer.writerows(daily_overview)
        
        # lead_report.csv
        lead_report = self.generate_lead_report()
        with open(output_path / 'lead_report.csv', 'w', newline='', encoding='utf-8') as f:
            if lead_report:
                writer = csv.DictWriter(f, fieldnames=[
                    'workflow_id', 'external_ref', 'batch_id', 'phone_masked',
                    'bucket', 'total_calls', 'connected_calls',
                    'last_call_at', 'current_disposition', 'ever_success'
                ])
                writer.writeheader()
                writer.writerows(lead_report)
        
        # rejections.csv
        rejections = self.generate_rejections()
        with open(output_path / 'rejections.csv', 'w', newline='', encoding='utf-8') as f:
            if rejections:
                writer = csv.DictWriter(f, fieldnames=[
                    'workflow_id', 'batch_id', 'row_number', 'reason'
                ])
                writer.writeheader()
                writer.writerows(rejections)
        
        # data_quality.json
        data_quality = self.generate_data_quality(lead_stats, event_stats)
        with open(output_path / 'data_quality.json', 'w', encoding='utf-8') as f:
            json.dump(data_quality, f, indent=2)
