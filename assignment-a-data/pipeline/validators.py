"""Validation and normalization functions for lead and event data."""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Dict, Any
import json


IST = timezone(timedelta(hours=5, minutes=30))


def normalize_header(header: str) -> str:
    """Normalize a header name for matching."""
    # Lower-case, trim, replace runs of _ or whitespace with single space
    normalized = header.lower().strip()
    normalized = re.sub(r'[_\s]+', ' ', normalized)
    return normalized


def normalize_phone(phone: str) -> Tuple[Optional[str], bool]:
    """
    Normalize an Indian mobile phone number.
    Returns (canonical_phone, is_valid).
    Canonical format: +91XXXXXXXXXX (10 digits starting with 6-9)
    """
    if not phone:
        return None, False
    
    # Strip all non-digit characters
    digits = re.sub(r'[^\d]', '', phone)
    
    # Handle country code prefixes
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    
    # Validate: exactly 10 digits starting with 6, 7, 8, or 9
    if len(digits) == 10 and digits[0] in '6789':
        return f"+91{digits}", True
    
    return None, False


def normalize_date(date_str: str) -> Optional[str]:
    """
    Parse and normalize a date string.
    Accepts: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY
    Returns: YYYY-MM-DD or None if invalid
    """
    if not date_str or not date_str.strip():
        return None
    
    date_str = date_str.strip()
    
    formats = [
        '%Y-%m-%d',
        '%d/%m/%Y',
        '%d-%m-%Y',
        '%d-%b-%Y',
        '%d-%B-%Y',
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # Validate the date is actually possible (e.g., not 31 Feb)
            # datetime.strptime will raise ValueError for impossible dates
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    
    return None


def normalize_decimal(amount_str: str) -> Optional[float]:
    """
    Parse and normalize a decimal/currency string.
    Accepts: ₹, Rs, Rs., INR prefixes (case-insensitive, optional space)
    Commas are thousands separators.
    Returns: float rounded to 2 decimal places, or None if invalid
    """
    if not amount_str or not amount_str.strip():
        return None
    
    amount_str = amount_str.strip()
    
    # Remove currency prefixes
    amount_str = re.sub(r'^[₹RsRs\.INR\s]*', '', amount_str, flags=re.IGNORECASE)
    
    # Remove thousands separators (commas)
    amount_str = amount_str.replace(',', '')
    
    try:
        value = float(amount_str)
        if value < 0:
            return None
        return round(value, 2)
    except ValueError:
        return None


def normalize_enum(value: str, allowed_values: list) -> Optional[str]:
    """
    Normalize an enum value.
    Case-insensitive comparison against allowed values.
    Returns lower-case value or None if invalid.
    """
    if not value or not value.strip():
        return None
    
    value_normalized = value.strip().lower()
    
    for allowed in allowed_values:
        if allowed.lower() == value_normalized:
            return allowed.lower()
    
    return None


def parse_timestamp(ts_str: str) -> Optional[datetime]:
    """
    Parse a timestamp string into a datetime object.
    Handles:
    - UTC: ...Z
    - Offset: ...+05:30
    - Naive: YYYY-MM-DD HH:MM:SS (treated as IST)
    """
    if not ts_str or not ts_str.strip():
        return None
    
    ts_str = ts_str.strip()
    
    # UTC format
    if ts_str.endswith('Z'):
        try:
            return datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    
    # ISO format with offset
    if '+' in ts_str or ts_str.count('-') > 2:
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            pass
    
    # Naive format - treat as IST
    try:
        dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        return dt.replace(tzinfo=IST)
    except ValueError:
        pass
    
    return None


def to_ist(dt: datetime) -> datetime:
    """Convert a datetime to IST timezone."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def ist_date(dt: datetime) -> str:
    """Get the IST date (YYYY-MM-DD) from a datetime."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime('%Y-%m-%d')


def ist_iso_format(dt: datetime) -> str:
    """Format a datetime as ISO-8601 in IST."""
    ist_dt = to_ist(dt)
    return ist_dt.strftime('%Y-%m-%dT%H:%M:%S+05:30')
