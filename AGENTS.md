# CivicWatch Specification (Canonical)

This file is the single source of truth for project objectives, API behavior, and implementation priorities.

If code and docs ever diverge, update code to match this spec or update this spec in the same change.

## Mission

Build a structured civic intelligence engine that:

- Pulls meeting data from CivicWeb
- Parses agenda HTML and attachments
- Classifies agenda items by topic (starting with zoning)
- Extracts structured zoning signals
- Exposes clean API endpoints for downstream UI and alerting

Primary focus: detect zoning-related changes (rezonings, PUDs, Chapter 160 amendments, etc.).

## External Data Source

CivicWeb endpoints:

- `/Services/MeetingsService.svc/meetings?from=YYYY-MM-DD&to=YYYY-MM-DD`
- `/Services/MeetingsService.svc/meetings/{id}/meetingData`
- `/Services/MeetingsService.svc/meetings/{id}/meetingDocuments`

Key facts:

- `meetingData` provides metadata (name, time, location, video link)
- `meetingDocuments` includes `Html` with rendered agenda
- Agenda HTML includes item numbers, titles, and attachment links like `/document/{document_id}/...?...handle=...`

## API Source Of Truth

FastAPI entrypoint: `app.main:app`

Routing source of truth: `app/api/routes.py` (included by `app/main.py`).

Current API surface:

- `GET /` and `GET /app`
  - Web interface for ingest + topic-filter exploration
- `GET /health`
- `GET /meetings?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD`
  - Proxies CivicWeb meetings list
- `GET /meetings/{meeting_id}`
  - Proxies CivicWeb `meetingData`
- `GET /meetings/{meeting_id}/agenda`
  - Returns parsed, normalized agenda items from local DB
- `GET /meetings/{meeting_id}/agenda?topic=zoning`
  - Filters agenda items by computed topic tags
- `GET /meetings/{meeting_id}/minutes-metadata`
  - Returns extracted minutes-document metadata for the meeting
- `GET /meetings/{meeting_id}/entities`
  - Returns extracted entities and mentions for a meeting
- `GET /entities/search?q=...`
  - Searches stored entities across meetings for UI exploration
- `GET /entities/{entity_id}/connections`
  - Returns graph connections (relationships) for an entity node, aggregated by neighbor + relation type
- `GET /entities/{entity_id}/related`
  - Returns co-occurring entities based on shared meeting mentions
- `POST /graph/backfill`
  - Backfills graph entities/bindings/connections for existing stored meetings/documents/mentions
- `POST /ingest/meeting/{meeting_id}`
  - Ingests one meeting into local DB
- `POST /ingest/range?from_date=YYYY-MM-DD&to_date=YYYY-MM-DD&limit=N`
  - Ingests a date range into local DB
  - Supports historical crawling with:
    - `crawl=true|false` (default `true`)
    - `chunk_days=<N>` (default `31`)
    - `store_raw=true|false` (default `true`)
- `POST /ingest/range/job?from_date=...&to_date=...`
  - Starts async ingest job for long historical crawls
- `GET /ingest/range/job/{job_id}`
  - Polls ingest job status and progress (`processed`, `discovered`, `current_meeting_id`)
- `GET /stored/meetings`
  - Browses stored meetings and local coverage without crawling
- `GET /explore/topics`
  - Returns topic summaries derived from stored agenda items

## Parsing Pipeline

1. Fetch `meetingDocuments`
2. Extract agenda `Html`
3. Parse HTML with BeautifulSoup
4. Extract:
   - `section`
   - `item_key` (example: `6.17`)
   - `title`
   - attachment links
5. Normalize text
6. Classify topics
7. Return structured response

## Historical Crawl Storage

- Raw upstream payloads are persisted in `meeting_raw_data` for each ingested meeting:
  - `meeting_data_json`
  - `meeting_documents_json`
- Purpose: retain source data for deterministic re-parsing and future extraction refinements.

## Minutes Metadata Extraction

- During ingest, minute-like documents are detected by title (`minutes`, `meeting minutes`).
- For minute PDFs, the app attempts deterministic metadata extraction:
  - `detected_date`
  - `page_count` (if PDF parser is available)
  - `text_excerpt` (if PDF parser is available)
  - `status` (`ok`, `download_failed`, `pdf_parser_unavailable`, etc.)
- Extracted records are persisted in `meeting_minutes_metadata`.

## Entity Extraction (Deterministic)

- Entities are extracted and persisted during ingest from:
  - meeting metadata (meeting name/location/time)
  - agenda item titles
  - minutes PDF text excerpts
- Current entity types:
  - `person` (conservative titled-name extraction + alias snowball matching)
  - `date`
  - `address`
  - `ordinance_number`
  - `resolution_number`
  - `organization` (e.g., `LLC`, `Inc`, `Company`)
- Storage tables:
  - `entities` (canonical entity values)
  - `entity_aliases` (deterministic aliases, used for person snowball matching)
  - `entity_mentions` (source-linked mentions with context)

## Graph Model (Nodes / Connections / Evidence)

Conceptual model:

- `entities` = graph nodes (people, organizations, addresses, dates, meetings, documents, etc.)
- `entity_connections` = graph relationships / edges between nodes
- `entity_mentions` = evidence rows (source snippets + provenance + confidence)
- `topics` = classification labels/signals (not entity nodes)

Current graph-specific node kinds added:

- `meeting`
- `document`

### Graph Schema (Stage 2-3 Foundation)

`entity_bindings` (maps a graph node to a canonical source-row identity)

- `id` (PK)
- `entity_id` (FK -> `entities.id`)
- `source_table` (`meetings`, `documents`, ...)
- `source_id` (local source table PK)
- Constraints:
  - unique (`source_table`, `source_id`) as `uq_entity_binding_source`

`entity_connections` (graph edges with provenance anchor)

- `id` (PK)
- `from_entity_id` (FK -> `entities.id`)
- `to_entity_id` (FK -> `entities.id`)
- `relation_type` (examples: `contains_document`, `mentions`, `occurs_on`, `occurs_at`)
- `meeting_id` (nullable FK-ish convenience field for filtering/aggregation)
- `document_id` (nullable CivicWeb document id convenience field)
- `evidence_source_type` (source provenance type; aligns with `entity_mentions.source_type`)
- `evidence_source_id` (source row id for provenance)
- `strength` (numeric weight, currently derived from mention confidence / structural default)
- `evidence_count` (currently `1` per unique edge+evidence row; aggregated in API)
- `last_seen_at` (UTC ISO timestamp)
- Constraints:
  - unique (`from_entity_id`, `to_entity_id`, `relation_type`, `evidence_source_type`, `evidence_source_id`)
    as `uq_entity_connection_edge_evidence`

### Ingest Pipeline Mapping (Current Implementation)

During `ingest_meeting(...)`:

1. Existing deterministic extraction persists `entity_mentions` (evidence) from:
   - `meeting_metadata`
   - `agenda_item_title`
   - `document_title`
   - `document_content`
   - `minutes_excerpt`
2. Graph rebuild runs (`rebuild_graph_for_meeting`)
3. Graph rebuild upserts:
   - meeting node (`entity_type=meeting`, normalized `meeting:{meeting_id}`)
   - document nodes (`entity_type=document`, normalized `document:{meeting_id}:{document_id}`)
   - `entity_bindings` for meeting/document source rows
4. Graph rebuild upserts structural connections:
   - `meeting -> document` (`contains_document`)
5. Graph rebuild upserts evidence-backed connections from mentions:
   - `meeting -> entity` (`mentions`, or `occurs_on` / `occurs_at` for meeting metadata date/address)
   - `document -> entity` (`mentions`) for document-backed evidence sources

Backfill path:

- `POST /graph/backfill` runs the same graph rebuild logic against already-stored meetings/documents/mentions
- This is the supported way to promote historical data into graph nodes/edges after schema changes

## Topic Classification

File: `app/classifiers/topics.py`

Current topic focus:

- `zoning`
- `ordinances_general`
- `public_hearings`
- `contracts_procurement`
- `budget_finance`
- `infrastructure_transport`
- `urban_renewal_development`
- `boards_commissions`
- `licenses_permits`
- `utilities_franchise`

Current zoning detection signals include:

- `zoning`
- `rezone` / `rezoning`
- `chapter 160`
- `title xv` / `title 15`
- `pud` / `planned unit development`
- `c-h`
- `highway commercial`
- phrase pattern for `rezone ... C-H ... to ... PUD`

Filtering contract:

- `topic` query parameter is normalized and matched against computed lowercase topic tags

## Response Shapes

Agenda item:

```json
{
  "item_key": "6.17",
  "section": "PUBLIC HEARINGS",
  "title": "...",
  "topics": ["zoning"],
  "zoning_signals": {
    "ordinance_number": "2026-14",
    "from_zone": "C-H",
    "to_zone": "PUD",
    "reading_stage": "first",
    "address": "1234 Douglas Ave"
  },
  "documents": [
    {
      "document_id": 148134,
      "title": "...",
      "url": "...",
      "handle": "..."
    }
  ]
}
```

`zoning_signals` is populated for items tagged with topic `zoning`; otherwise it is `null`.

## Development Conventions

- Python 3.11+
- Pydantic v2 only
- Deterministic parsing before LLM extraction
- No API keys in repo
- Avoid duplicate dependency pins
- Deployment runtime dependencies are installed from `requirements.txt` (Docker/Fly)
- Keep `requirements.txt` and `pyproject.toml` dependency pins synchronized

## Hosting / Deployment (Current Beta)

- Recommended host: Fly.io (single-instance beta)
- Deployment artifacts:
  - `Dockerfile`
  - `fly.toml`
- Runtime assumptions today:
  - SQLite on local persistent volume (`/data/civicwatch.db`)
  - in-memory ingest job state (`app/jobs.py`)
  - single app instance only

Beta constraints:

- Do not scale to multiple app instances yet
- Keep ingest throttles enabled (single active job + cooldown + max range span)
- Use mounted persistent volume for SQLite durability

## Acceptance Checks For Parsing Changes

Before merging parser/classifier changes:

- Verify extraction still returns correct `item_key`
- Verify extraction still returns correct `title`
- Verify extraction still returns correct `document_id`
- Verify `/meetings/{meeting_id}/agenda?topic=zoning` returns only zoning-tagged items
- Avoid broad regexes that create false positives

## Roadmap

Phase 1:

- Stable ingestion
- Deterministic zoning classification
- Clean filtering

Phase 2:

- Attachment text extraction (PDF then DOCX)
- Structured zoning signal extraction (`ordinance_number`, `address`, `from_zone`, `to_zone`, `reading_stage`)

Phase 3:

- User watchlists
- Address-based alerting
- Multi-city expansion
