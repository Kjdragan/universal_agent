# CSI Interfaces and Schemas

Last updated: 2026-03-01

## Existing Event Output
- `report_product_ready`
- `opportunity_bundle_ready` (implemented)

## New Event Output (Planned)
- `delivery_health_snapshot` (deferred)

## Opportunity Bundle Schema (Target)
```json
{
  "bundle_id": "string",
  "window_start_utc": "iso8601",
  "window_end_utc": "iso8601",
  "confidence_method": "evidence_model|heuristic",
  "quality_summary": {
    "signal_volume": 0,
    "freshness_minutes": 0,
    "delivery_health": "ok|degraded|blocked",
    "coverage_score": 0.0
  },
  "opportunities": [
    {
      "opportunity_id": "string",
      "title": "string",
      "thesis": "string",
      "source_mix": {"youtube_channel_rss": 0, "reddit_discovery": 0},
      "evidence_refs": ["artifact/path/or/url"],
      "novelty_score": 0.0,
      "confidence_score": 0.0,
      "risk_flags": ["string"],
      "recommended_action": "string",
      "followup_task_template": "string"
    }
  ]
}
```

## Dashboard API Targets
- Extend `/api/v1/dashboard/csi/reports`
- Add `/api/v1/dashboard/csi/opportunities` (implemented)
- Add `/api/v1/dashboard/csi/delivery-health`

## Specialist Loop Confidence Fields (Implemented)
- `confidence_score`: numeric loop confidence.
- `confidence_target`: configured threshold.
- `confidence_method`: `heuristic|evidence_model`.
- `confidence_evidence`: structured evidence snapshot used by scorer.
