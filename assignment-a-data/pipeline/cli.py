"""Main CLI for the data pipeline."""

import argparse
import sys
from pathlib import Path

from .database import Database
from .lead_ingestion import LeadIngestion
from .event_ingestion import EventIngestion
from .reporting import Reporting


def main():
    parser = argparse.ArgumentParser(description='Agentic Universe Data Pipeline')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Ingest command
    ingest_parser = subparsers.add_parser('ingest', help='Ingest a data drop')
    ingest_parser.add_argument('drop_path', help='Path to the drop directory')
    
    # Report command
    report_parser = subparsers.add_parser('report', help='Generate reports')
    report_parser.add_argument('--out', required=True, help='Output directory for reports')
    report_parser.add_argument('--db', default='pipeline.db', help='Database file path')
    
    args = parser.parse_args()
    
    if args.command == 'ingest':
        ingest_drop(args.drop_path)
    elif args.command == 'report':
        generate_reports(args.out, args.db)
    else:
        parser.print_help()
        sys.exit(1)


def ingest_drop(drop_path: str):
    """Ingest a single drop."""
    drop_path = Path(drop_path)
    drop_id = drop_path.name
    
    if not drop_path.exists():
        print(f"Error: Drop path {drop_path} does not exist")
        sys.exit(1)
    
    # Initialize database
    db = Database('pipeline.db')
    
    # Check if already ingested
    if db.is_drop_ingested(drop_id):
        print(f"Drop {drop_id} already ingested, skipping")
        return
    
    print(f"Ingesting drop {drop_id}...")
    
    # Get paths
    schema_dir = Path(__file__).parent.parent / 'schemas'
    dispositions_path = schema_dir / 'dispositions.csv'
    
    # Ingest leads
    print("  Ingesting leads...")
    lead_ingestion = LeadIngestion(db, str(schema_dir))
    lead_ingestion.ingest_drop(str(drop_path), drop_id)
    print(f"    Total rows: {lead_ingestion.stats['total_rows']}")
    print(f"    Rejected: {lead_ingestion.stats['rejected_rows']}")
    print(f"    Duplicate merges: {lead_ingestion.stats['duplicate_merges']}")
    print(f"    Field warnings: {lead_ingestion.stats['field_warnings']}")
    
    # Ingest events
    print("  Ingesting events...")
    event_ingestion = EventIngestion(db, str(dispositions_path))
    event_ingestion.ingest_drop(str(drop_path), drop_id)
    print(f"    Total events: {event_ingestion.stats['events_total']}")
    print(f"    Malformed: {event_ingestion.stats['malformed_events']}")
    print(f"    Duplicates dropped: {event_ingestion.stats['duplicate_events_dropped']}")
    print(f"    Orphans: {event_ingestion.stats['orphan_events']}")
    print(f"    Unknown dispositions: {event_ingestion.stats['unknown_disposition_events']}")
    
    # Relink orphans (in case this drop contains leads for previous orphans)
    print("  Relinking orphans...")
    relinked = event_ingestion.relink_orphans()
    print(f"    Relinked: {relinked}")
    
    # Mark drop as ingested
    db.mark_drop_ingested(drop_id)
    
    print(f"Drop {drop_id} ingested successfully")


def generate_reports(output_dir: str, db_path: str):
    """Generate all reports."""
    db = Database(db_path)
    reporting = Reporting(db)
    
    print(f"Generating reports to {output_dir}...")
    
    # Get stats from database metadata
    lead_stats = {
        'total_rows': 0,
        'rejected_rows': 0,
        'duplicate_merges': 0,
        'field_warnings': 0,
        'rejections_by_reason': {}
    }
    
    event_stats = {
        'events_total': 0,
        'malformed_events': 0,
        'duplicate_events_dropped': 0,
        'orphan_events': 0,
        'unknown_disposition_events': 0
    }
    
    with db.get_connection() as conn:
        cursor = conn.cursor()
        
        # Count leads
        cursor.execute("SELECT COUNT(*) as count FROM leads")
        total_leads = cursor.fetchone()['count']
        
        # Count rejections
        cursor.execute("SELECT COUNT(*) as count FROM rejections")
        lead_stats['rejected_rows'] = cursor.fetchone()['count']
        lead_stats['total_rows'] = total_leads + lead_stats['rejected_rows']
        
        # Get stats from metadata
        lead_stats['duplicate_merges'] = int(db.get_metadata('duplicate_merges') or '0')
        lead_stats['field_warnings'] = int(db.get_metadata('field_warnings') or '0')
        
        # Get rejection reasons from metadata
        cursor.execute("SELECT key, value FROM metadata WHERE key LIKE 'rejection:%'")
        for row in cursor.fetchall():
            reason = row['key'].replace('rejection:', '')
            lead_stats['rejections_by_reason'][reason] = int(row['value'])
        
        # Get event stats
        cursor.execute("SELECT COUNT(*) as count FROM events")
        event_stats['events_total'] = cursor.fetchone()['count']
        
        event_stats['malformed_events'] = int(db.get_metadata('malformed_events') or '0')
        event_stats['duplicate_events_dropped'] = int(db.get_metadata('duplicate_events_dropped') or '0')
        event_stats['unknown_disposition_events'] = int(db.get_metadata('unknown_disposition_events') or '0')
        
        # Count orphans (final count after relinking)
        cursor.execute("SELECT COUNT(*) as count FROM events WHERE is_orphan = 1")
        event_stats['orphan_events'] = cursor.fetchone()['count']
    
    reporting.write_reports(output_dir, lead_stats, event_stats)
    
    print("Reports generated successfully")


if __name__ == '__main__':
    main()
