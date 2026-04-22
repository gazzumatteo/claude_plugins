# Code optimization checklist

Generated from: `{{findings_json}}`
Commit strategy: {{commit_strategy}}
Min confidence: {{min_confidence}}
Allow review-required: {{allow_review}}

## Legend

- `[ ]` pending
- `[x]` done
- `[~]` blocked (regression detected during apply)
- `[-]` skipped (user declined or manual review required)

Items are ordered per category by: risk ascending, confidence descending, severity descending.

---

## {{category}} ({{count}})

- [ ] **{{id}}** — {{description}}
  - files: `{{files}}`
  - action: {{proposed_action}}
  - risk: {{risk}} · confidence: {{confidence}} · severity: {{severity}}
  - review: {{reason_for_manual_review}}  <!-- omit line if not applicable -->
