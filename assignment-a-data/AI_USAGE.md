# AI Usage Report

## Overview

This document describes how AI assistance was used in completing Assignment A: Interaction Ledger & Reporting Pipeline.

## AI Assistance Summary

AI was used as a pair programmer to:
1. Understand the assignment requirements and domain terminology
2. Design the database schema and data model
3. Implement validation and normalization logic
4. Build the ingestion pipeline for leads and events
5. Implement reporting logic
6. Write tests for critical components
7. Debug and fix issues during implementation

## Specific AI Contributions

### 1. Requirements Analysis
- AI helped parse the detailed specification document
- Clarified ambiguous points (e.g., header normalization rules, timestamp handling)
- Identified edge cases in the data (orphans, duplicates, malformed events)

### 2. Database Schema Design
- AI suggested the table structure for SQLite
- Recommended the phone lookup table for PII protection while enabling event linking
- Advised on indexes for performance
- Suggested the metadata table for tracking ingestion statistics

### 3. Validation Logic Implementation
- AI implemented phone number normalization with support for multiple Indian formats
- AI implemented date parsing for multiple formats (YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY)
- AI implemented currency parsing with support for ₹, Rs, INR prefixes and comma separators
- AI implemented enum normalization with case-insensitive matching

### 4. Pipeline Architecture
- AI designed the modular structure (database, validators, lead_ingestion, event_ingestion, reporting, cli)
- AI suggested the idempotency mechanism using ingestion_state table
- AI designed the orphan relinking strategy

### 5. Reporting Logic
- AI implemented bucket classification logic (yet_to_reach_out, completed, in_progress_*)
- AI implemented effective disposition calculation with override handling
- AI handled IST timezone conversion for reports
- AI implemented all five report formats exactly to specification

### 6. Debugging
- AI helped debug field mapping issues between schema and database columns
- AI fixed timestamp parsing issues when generating reports
- AI identified and fixed the orphan relinking logic

### 7. Testing
- AI wrote comprehensive unit tests for validators (phone, date, decimal, enum, timestamp)
- AI wrote integration tests for bucket logic and effective disposition

## Human Decisions

The following decisions were made by the human developer:
1. Choice of SQLite over PostgreSQL (for simplicity)
2. Use of SHA-256 hashing for PII (standard library only)
3. CLI interface design (`ingest` and `report` commands)
4. File structure and module organization
5. Test framework choice (pytest, though not installed in environment)

## Code Attribution

All code in the `assignment-a-data/pipeline/` directory was written with AI assistance. The code is original implementation based on the assignment specification, not copied from any external sources.

## Verification

The pipeline was tested with the synthetic data generator:
- Successfully ingested drop_1 and drop_2
- Idempotency verified (re-running ingest skips already-processed drops)
- All five report files generated successfully
- Reports match the expected format and structure

## Limitations

- Tests were written but not executed (pytest not available in environment)
- Performance not benchmarked on large dataset
- No integration tests for end-to-end workflow

## Conclusion

AI served as an effective pair programmer, accelerating development while maintaining code quality. The human provided domain context, made architectural decisions, and verified the implementation against the specification.
