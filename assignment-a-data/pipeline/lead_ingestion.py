"""Lead ingestion with validation, normalization, rejection, and merging."""

import csv
import os
from typing import Dict, List, Tuple, Optional, Any
from pathlib import Path

from .database import Database
from .validators import (
    normalize_header, normalize_phone, normalize_date, 
    normalize_decimal, normalize_enum
)


class LeadIngestion:
    """Handle lead file ingestion with validation and merging."""
    
    def __init__(self, db: Database, schema_dir: str):
        self.db = db
        self.schema_dir = schema_dir
        self.schemas = self._load_schemas()
        self.stats = {
            'total_rows': 0,
            'rejected_rows': 0,
            'duplicate_merges': 0,
            'field_warnings': 0,
            'rejections_by_reason': {}
        }
    
    def _load_schemas(self) -> Dict[str, Dict]:
        """Load workflow schemas from JSON files."""
        import json
        schemas = {}
        schema_path = Path(self.schema_dir)
        
        for schema_file in schema_path.glob('*.json'):
            with open(schema_file, 'r') as f:
                schema = json.load(f)
                schemas[schema['workflow_id']] = schema
        
        return schemas
    
    def _get_field_mapping(self, headers: List[str], workflow_id: str) -> Dict[str, str]:
        """Map CSV headers to schema fields."""
        schema = self.schemas[workflow_id]
        mapping = {}
        
        for header in headers:
            normalized = normalize_header(header)
            
            for field in schema['fields']:
                field_name = field['name']
                # Check against field name
                if normalize_header(field_name) == normalized:
                    mapping[header] = field_name
                    break
                # Check against aliases
                if 'aliases' in field:
                    for alias in field['aliases']:
                        if normalize_header(alias) == normalized:
                            mapping[header] = field_name
                            break
        
        return mapping
    
    def _validate_row(self, row: Dict[str, str], mapping: Dict[str, str], 
                      workflow_id: str) -> Tuple[Optional[Dict], Optional[str], int]:
        """
        Validate and normalize a lead row.
        Returns (normalized_data, rejection_reason, warning_count).
        """
        schema = self.schemas[workflow_id]
        normalized = {}
        warnings = 0
        
        # Process fields in schema order
        for field in schema['fields']:
            field_name = field['name']
            field_type = field['type']
            required = field.get('required', False)
            
            # Find the CSV header that maps to this field
            csv_header = None
            for header, mapped_name in mapping.items():
                if mapped_name == field_name:
                    csv_header = header
                    break
            
            if csv_header is None:
                if required:
                    return None, f"missing_required:{field_name}", 0
                continue
            
            raw_value = row.get(csv_header, '').strip()
            
            # Check required
            if required and not raw_value:
                return None, f"missing_required:{field_name}", 0
            
            # Skip validation if empty and not required
            if not raw_value and not required:
                normalized[field_name] = None
                continue
            
            # Type-specific validation
            if field_type == 'string':
                normalized[field_name] = raw_value
            
            elif field_type == 'phone':
                canonical, is_valid = normalize_phone(raw_value)
                if not is_valid:
                    if required or raw_value:
                        return None, "invalid_phone", 0
                    normalized[field_name] = None
                else:
                    normalized[field_name] = canonical
            
            elif field_type == 'date':
                date_val = normalize_date(raw_value)
                if date_val is None:
                    if required:
                        return None, f"missing_required:{field_name}", 0
                    normalized[field_name] = None
                    warnings += 1
                else:
                    normalized[field_name] = date_val
            
            elif field_type == 'decimal':
                decimal_val = normalize_decimal(raw_value)
                if decimal_val is None:
                    if required:
                        return None, f"missing_required:{field_name}", 0
                    normalized[field_name] = None
                    warnings += 1
                else:
                    normalized[field_name] = decimal_val
            
            elif field_type == 'enum':
                enum_val = normalize_enum(raw_value, field.get('values', []))
                if enum_val is None:
                    if required:
                        return None, f"missing_required:{field_name}", 0
                    normalized[field_name] = None
                    warnings += 1
                else:
                    normalized[field_name] = enum_val
        
        # Apply defaults
        for field in schema['fields']:
            field_name = field['name']
            if 'default' in field and normalized.get(field_name) is None:
                normalized[field_name] = field['default']
        
        return normalized, None, warnings
    
    def _is_blank_row(self, row: Dict[str, str]) -> bool:
        """Check if a row is entirely blank or whitespace."""
        return all(not cell or not cell.strip() for cell in row.values())
    
    def ingest_file(self, file_path: str, workflow_id: str, batch_id: str, drop_id: str):
        """Ingest a single lead CSV file."""
        with open(file_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            
            # Get field mapping
            mapping = self._get_field_mapping(headers, workflow_id)
            
            row_number = 0
            for row in reader:
                row_number += 1
                
                # Skip blank rows
                if self._is_blank_row(row):
                    continue
                
                self.stats['total_rows'] += 1
                
                # Validate and normalize
                normalized, rejection_reason, warnings = self._validate_row(
                    row, mapping, workflow_id
                )
                
                if rejection_reason:
                    self.stats['rejected_rows'] += 1
                    self.stats['rejections_by_reason'][rejection_reason] = \
                        self.stats['rejections_by_reason'].get(rejection_reason, 0) + 1
                    self.db.increment_metadata(f'rejection:{rejection_reason}')
                    
                    # Store rejection
                    self._store_rejection(workflow_id, batch_id, row_number, 
                                          rejection_reason, drop_id)
                    continue
                
                self.stats['field_warnings'] += warnings
                self.db.increment_metadata('field_warnings', warnings)
                
                # Insert or merge lead
                self._upsert_lead(normalized, workflow_id, batch_id, drop_id)
    
    def _store_rejection(self, workflow_id: str, batch_id: str, row_number: int,
                        reason: str, drop_id: str):
        """Store a rejected row."""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO rejections (workflow_id, batch_id, row_number, reason, drop_id)
                VALUES (?, ?, ?, ?, ?)
            """, (workflow_id, batch_id, row_number, reason, drop_id))
    
    def _upsert_lead(self, data: Dict, workflow_id: str, batch_id: str, drop_id: str):
        """Insert or merge a lead record."""
        phone = data['phone']
        phone_hash = self.db._hash_phone(phone)
        
        # Store phone lookup for event linking
        self.db.store_phone_lookup(phone_hash, phone)
        
        # Find reference field and map to database columns
        schema = self.schemas[workflow_id]
        ref_field = None
        for field in schema['fields']:
            if field.get('reference', False):
                ref_field = field['name']
                break
        
        external_ref = data[ref_field]
        
        # Hash PII fields
        customer_name_hash = None
        if 'customer_name' in data and data['customer_name']:
            customer_name_hash = self.db._hash_string(data['customer_name'])
        
        # Map schema field names to database column names
        field_mapping = {
            'policy_number': 'external_ref',
            'order_id': 'external_ref',
        }
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            # Check if lead exists
            cursor.execute("""
                SELECT id, batch_id FROM leads 
                WHERE workflow_id = ? AND phone_hash = ?
            """, (workflow_id, phone_hash))
            existing = cursor.fetchone()
            
            if existing:
                # Merge: update only non-null fields
                lead_id = existing['id']
                self.stats['duplicate_merges'] += 1
                self.db.increment_metadata('duplicate_merges')
                
                # Build update query dynamically
                updates = []
                values = []
                
                for field_name, value in data.items():
                    if field_name in ['phone', 'customer_name']:
                        continue  # Skip PII fields
                    
                    # Map field name to column name
                    col_name = field_mapping.get(field_name, field_name)
                    
                    if value is not None:
                        updates.append(f"{col_name} = ?")
                        values.append(value)
                
                if updates:
                    values.extend([lead_id])
                    cursor.execute(f"""
                        UPDATE leads 
                        SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                    """, values)
            else:
                # Insert new lead - map fields to columns
                mapped_data = {}
                for field_name, value in data.items():
                    if field_name in ['phone', 'customer_name']:
                        continue
                    col_name = field_mapping.get(field_name, field_name)
                    mapped_data[col_name] = value
                
                cursor.execute("""
                    INSERT INTO leads (
                        workflow_id, phone_hash, customer_name_hash, external_ref,
                        batch_id, policy_expiry, premium_amount, preferred_language,
                        product, order_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    workflow_id, phone_hash, customer_name_hash, external_ref,
                    batch_id, mapped_data.get('policy_expiry'), mapped_data.get('premium_amount'),
                    mapped_data.get('preferred_language'), mapped_data.get('product'),
                    mapped_data.get('order_date')
                ))
    
    def ingest_drop(self, drop_path: str, drop_id: str):
        """Ingest all lead files from a drop."""
        leads_dir = Path(drop_path) / 'leads'
        
        for workflow_dir in leads_dir.iterdir():
            if not workflow_dir.is_dir():
                continue
            
            workflow_id = workflow_dir.name
            
            for batch_file in sorted(workflow_dir.glob('*.csv')):
                batch_id = batch_file.stem  # e.g., batch_001
                self.ingest_file(str(batch_file), workflow_id, batch_id, drop_id)
