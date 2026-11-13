"""Tests for validation and normalization functions."""

import pytest
from pipeline.validators import (
    normalize_phone, normalize_date, normalize_decimal, normalize_enum,
    parse_timestamp, to_ist, ist_date, ist_iso_format
)


class TestPhoneNormalization:
    """Test phone number normalization."""
    
    def test_valid_10_digit(self):
        """Valid 10-digit number starting with 6-9."""
        assert normalize_phone("9876543210") == ("+919876543210", True)
        assert normalize_phone("6123456789") == ("+916123456789", True)
    
    def test_with_country_code_91(self):
        """Numbers with +91 or 91 prefix."""
        assert normalize_phone("919876543210") == ("+919876543210", True)
        assert normalize_phone("+91 98765 43210") == ("+919876543210", True)
        assert normalize_phone("91-98765-43210") == ("+919876543210", True)
    
    def test_with_leading_zero(self):
        """Numbers with leading 0."""
        assert normalize_phone("09876543210") == ("+919876543210", True)
    
    def test_invalid_9_digit(self):
        """9-digit numbers are invalid."""
        result, valid = normalize_phone("987654321")
        assert valid is False
    
    def test_invalid_prefix(self):
        """Numbers not starting with 6-9 are invalid."""
        result, valid = normalize_phone("5123456789")
        assert valid is False
    
    def test_empty(self):
        """Empty string is invalid."""
        result, valid = normalize_phone("")
        assert valid is False
    
    def test_landline(self):
        """Landline numbers are invalid."""
        result, valid = normalize_phone("022-1234-5678")
        assert valid is False


class TestDateNormalization:
    """Test date normalization."""
    
    def test_yyyy_mm_dd(self):
        """YYYY-MM-DD format."""
        assert normalize_date("2026-10-15") == "2026-10-15"
    
    def test_dd_mm_yyyy_slash(self):
        """DD/MM/YYYY format."""
        assert normalize_date("15/10/2026") == "2026-10-15"
    
    def test_dd_mm_yyyy_dash(self):
        """DD-MM-YYYY format."""
        assert normalize_date("15-10-2026") == "2026-10-15"
    
    def test_dd_mon_yyyy(self):
        """DD-Mon-YYYY format."""
        assert normalize_date("15-Oct-2026") == "2026-10-15"
        assert normalize_date("15-OCT-2026") == "2026-10-15"
    
    def test_invalid_date(self):
        """Impossible dates like 31 Feb are invalid."""
        assert normalize_date("31/02/2026") is None
    
    def test_empty(self):
        """Empty string returns None."""
        assert normalize_date("") is None
    
    def test_invalid_format(self):
        """Invalid format returns None."""
        assert normalize_date("soon") is None


class TestDecimalNormalization:
    """Test decimal/currency normalization."""
    
    def test_plain_number(self):
        """Plain decimal string."""
        assert normalize_decimal("5000.50") == 5000.50
        assert normalize_decimal("5000") == 5000.00
    
    def test_with_rupee_symbol(self):
        """With ₹ symbol."""
        assert normalize_decimal("₹5,000") == 5000.00
        assert normalize_decimal("₹5000.50") == 5000.50
    
    def test_with_rs_prefix(self):
        """With Rs. prefix."""
        assert normalize_decimal("Rs. 5,000") == 5000.00
        assert normalize_decimal("Rs 5000") == 5000.00
    
    def test_with_inr_prefix(self):
        """With INR prefix."""
        assert normalize_decimal("INR 5,000") == 5000.00
    
    def test_with_commas(self):
        """With thousands separators."""
        assert normalize_decimal("12,345.67") == 12345.67
    
    def test_negative(self):
        """Negative values are invalid."""
        assert normalize_decimal("-100") is None
    
    def test_empty(self):
        """Empty string returns None."""
        assert normalize_decimal("") is None
    
    def test_invalid(self):
        """Invalid strings return None."""
        assert normalize_decimal("twelve thousand") is None


class TestEnumNormalization:
    """Test enum normalization."""
    
    def test_case_insensitive(self):
        """Case-insensitive matching."""
        allowed = ["en", "hi", "ta"]
        assert normalize_enum("EN", allowed) == "en"
        assert normalize_enum("Hi", allowed) == "hi"
        assert normalize_enum("TA", allowed) == "ta"
    
    def test_whitespace(self):
        """Whitespace is trimmed."""
        allowed = ["en", "hi"]
        assert normalize_enum(" en ", allowed) == "en"
    
    def test_invalid(self):
        """Invalid values return None."""
        allowed = ["en", "hi"]
        assert normalize_enum("english", allowed) is None
    
    def test_empty(self):
        """Empty string returns None."""
        assert normalize_enum("", ["en"]) is None


class TestTimestampParsing:
    """Test timestamp parsing."""
    
    def test_utc_format(self):
        """UTC format with Z suffix."""
        dt = parse_timestamp("2026-10-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None
    
    def test_offset_format(self):
        """Format with timezone offset."""
        dt = parse_timestamp("2026-10-15T10:30:00+05:30")
        assert dt is not None
        assert dt.tzinfo is not None
    
    def test_naive_format(self):
        """Naive format (treated as IST)."""
        dt = parse_timestamp("2026-10-15 10:30:00")
        assert dt is not None
        assert dt.tzinfo is not None  # Should have IST timezone
    
    def test_invalid(self):
        """Invalid format returns None."""
        assert parse_timestamp("invalid") is None


class TestISTConversion:
    """Test IST timezone conversion."""
    
    def test_to_ist_from_utc(self):
        """Convert UTC to IST."""
        from datetime import timezone, timedelta
        utc_dt = parse_timestamp("2026-10-15T10:30:00Z")
        ist_dt = to_ist(utc_dt)
        assert ist_dt.hour == 16  # UTC+5:30
    
    def test_ist_date(self):
        """Get IST date string."""
        from datetime import timezone, timedelta
        utc_dt = parse_timestamp("2026-10-15T20:30:00Z")
        date_str = ist_date(utc_dt)
        assert date_str == "2026-10-16"  # Crosses midnight in IST
    
    def test_ist_iso_format(self):
        """Format as ISO-8601 in IST."""
        from datetime import timezone, timedelta
        utc_dt = parse_timestamp("2026-10-15T10:30:00Z")
        iso_str = ist_iso_format(utc_dt)
        assert iso_str.endswith("+05:30")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
