# Assignment A: Interaction Ledger & Reporting Pipeline - Submission

## Overview

Built a complete data pipeline for Agentic Universe that ingests lead CSV files and event JSONL streams, validates and normalizes data, stores it in an append-only ledger with PII protection, and generates five required reports.

## Architecture

### Database Schema (SQLite)

**Tables:**

1. **ingestion_state** - Tracks which drops have been ingested for idempotency
   - `drop_id` (PK), `ingested_at`

2. **phone_lookup** - Maps hashed phone numbers to canonical form for event linking
   - `phone_hash` (PK), `canonical_phone`
   - Enables linking events to leads while protecting PII

3. **leads** - Lead records with PII hashed
   - `id`, `workflow_id`, `phone_hash`, `customer_name_hash`, `external_ref`, `batch_id`
   - Workflow-specific fields: `policy_expiry`, `premium_amount`, `preferred_language`, `product`, `order_date`
   - Unique constraint on `(workflow_id, phone_hash)` for deduplication
   - `batch_id` is immutable (set from first valid row)

4. **rejections** - Rejected lead rows with reasons
   - `workflow_id`, `batch_id`, `row_number`, `reason`, `drop_id`

5. **events** - Append-only interaction ledger
   - `id`, `event_id` (unique), `event_type`, `workflow_id`, `call_id`, `occurred_at`
   - `phone_hash`, `lead_id`, `payload` (JSON), `is_orphan`, `drop_id`
   - No updates or deletes - truly append-only

6. **dispositions** - Cached disposition catalogue
   - `workflow_id`, `code` (PK), `label`, `is_terminal`, `is_success`

7. **metadata** - Key-value store for ingestion statistics
   - `key` (PK), `value` - tracks counts like rejections, duplicates, malformed events

### PII Protection Strategy

**Approach:** Hash PII fields using SHA-256 while maintaining ability to match events to leads.

- **Phone numbers:** Stored as SHA-256 hash in `leads.phone_hash`
- **Customer names:** Stored as SHA-256 hash in `leads.customer_name_hash`
- **Phone lookup table:** Maps `phone_hash` → `canonical_phone` for event linking
  - Events store `phone_hash` to link to leads
  - Canonical phone only needed for masked output in reports

**Trade-off:** Hashing is one-way. If a phone number needs to be recovered (e.g., for customer support), we'd need to store the canonical form encrypted or use a reversible encryption scheme. For this assignment, the lookup table provides the canonical form for reporting purposes.

### Idempotency Design

**Mechanism:** Track ingested drops in `ingestion_state` table.

- Before ingesting, check if `drop_id` exists in `ingestion_state`
- If exists, skip entire drop
- After successful ingestion, insert `drop_id` into `ingestion_state`

**Implication:** Re-running `ingest data/drop_1` after it's already processed does nothing. Ingesting drop_1 then drop_2 gives same result as ingesting both at once.

### Event Orphan Handling

**Challenge:** Events can arrive before their lead (in earlier drops).

**Solution:**
1. Store events with `is_orphan=1` when lead not found
2. After each drop ingestion, run `relink_orphans()` to find newly linked events
3. Final orphan count in `data_quality.json` reflects state after all drops processed

**Trade-off:** Requires a relink step after each ingestion. Could be optimized with a background job in production.

## Design Decisions & Trade-offs

### 1. SQLite vs PostgreSQL

**Decision:** SQLite for simplicity and portability.

**Trade-offs:**
- **Pros:** No external dependencies, single file, easy to test, sufficient for dataset size
- **Cons:** Limited concurrency (write locks), no built-in replication, not suitable for high-throughput production

**Production alternative:** PostgreSQL for better concurrency, replication, and performance at scale.

### 2. Field Mapping Strategy

**Decision:** Map schema field names to database columns (e.g., `policy_number` → `external_ref`).

**Rationale:** Different workflows have different reference field names (`policy_number` for motor_renewal, `order_id` for policy_feedback), but database needs consistent column.

**Trade-off:** Adds mapping logic but keeps database schema clean and workflow-agnostic.

### 3. Metadata Table for Statistics

**Decision:** Store ingestion statistics in `metadata` table rather than in-memory.

**Rationale:** Statistics need to persist across runs for `data_quality.json`. In-memory stats would be lost after ingestion.

**Trade-off:** Additional table and I/O, but enables accurate reporting even if pipeline crashes mid-ingestion.

### 4. Timestamp Handling

**Decision:** Parse timestamps to Python datetime objects, store as strings in SQLite.

**Rationale:** SQLite doesn't have native datetime type. Storing as ISO strings preserves timezone info.

**Trade-off:** Need to parse timestamps when generating reports (IST conversion). Could store as Unix timestamps for easier arithmetic, but ISO strings are more readable.

### 5. Bucket Logic Implementation

**Decision:** Compute buckets dynamically in Python during report generation.

**Rationale:** Bucket logic is complex (depends on call states, dispositions). Computing on-the-fly is flexible and avoids stale data.

**Trade-off:** Slower report generation. Could pre-compute and cache buckets in database for performance.

### 6. Effective Disposition Calculation

**Decision:** Calculate by querying events table for each call during report generation.

**Rationale:** Append-only ledger means we need to find latest override or disposition.set. Querying ensures accuracy.

**Trade-off:** O(n) queries for n calls. Could add materialized view or cache effective disposition per call.

## What Works Well

1. **Idempotency:** Re-running ingestion is safe and fast (skips already-processed drops)
2. **PII Protection:** Phone numbers and names are hashed at rest
3. **Orphan Handling:** Events arriving before leads are correctly linked when lead arrives
4. **Validation:** Comprehensive validation with clear rejection reasons
5. **Duplicate Merging:** Last-write-wins for non-null fields preserves latest data

## Known Limitations

1. **Performance:** Bucket and effective disposition calculations are O(n) - would need optimization for large datasets
2. **No Incremental Processing:** Drop 2 re-processes all leads (though skips already-ingested drops). Could track per-file state for true incremental processing.
3. **Test Coverage:** Tests written but not run (pytest not installed). Would add to requirements.txt in production.
4. **Error Handling:** Basic error handling. Production would need retry logic, dead letter queues for malformed events.

## Production Considerations (100k leads/day)

### Scheduling
- Run ingestion as scheduled job (e.g., every 15 minutes) watching for new drops
- Use cron or Kubernetes CronJob

### Failure Handling
- Dead letter queue for permanently malformed events
- Alerting on high rejection rates or orphan counts
- Transactional ingestion: rollback entire drop if any part fails

### Backfills
- Store raw files in S3/GCS with versioning
- Ability to reprocess specific date ranges by dropping from `ingestion_state`
- Immutable ledger makes backfills safe (can re-insert events)

### Scaling
- Move to PostgreSQL for better concurrency
- Partition events table by date
- Add read replicas for reporting workload
- Consider streaming architecture (Kafka) for real-time processing

## Next Steps

1. Add pytest to requirements.txt and run tests in CI
2. Implement incremental processing (track per-file state)
3. Add performance benchmarking for large dataset
4. Consider SQL-based aggregation for reports (stretch goal)
5. Add monitoring and alerting hooks
