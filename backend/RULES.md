# Route-scoped active rules

Active rules are stored in the JSONB `active_rules.data` column and validated
as `ActiveRule` before activation and when loaded for an audit. An audit queries
the active rules for one exact `visa_type`, evaluates those rules in code, and
returns a `rule_results` entry with status and source provenance for each rule.

The evaluator supports these operators: `exists`, `not_exists`, `==`, `in`,
`contains`, numeric `>=`, `<=`, `>`, `<`, and `date_after_months`. Target fields
are dot-separated paths into applicant data; rule JSON is never executed as
code. Unit-bearing numeric rules are only compared when the target field's unit
matches the rule's `threshold_unit`.

## Extract and approve

1. Submit policy text to `POST /rules/extract` with its exact `visa_type`,
   `source_name`, and—when known—an `http` or `https` `source_url` and
   `source_section`.
2. Review the saved draft against that official source. Extraction output is a
   proposal, not approved policy.
3. Resolve missing or ambiguous values, then confirm the rule. Incomplete,
   unsupported, or invalid rules must not be used as if they passed.
4. Query `GET /rules/active?visa_type=uk_skilled_worker` to inspect the route's
   active rules. Deactivate obsolete rules before activating replacements.

Use a distinct `rule_id` for a new version (for example, suffix it with
`_v2`). Activating an existing ID is rejected rather than overwriting its saved
record. The current schema retains version metadata, but the admin workflow for
reviewing changes and managing versions is not yet implemented.

## Audit behavior and current limitations

- No active rules for the requested route results in `NEEDS_REVIEW`, never a
  passing audit.
- Missing required applicant data and unreviewed OCR data need human review.
- A rule with an unstructured exception or a holding-period requirement returns
  `needs_review`; current applicant documents do not prove continuous balance
  history or evaluate arbitrary exception conditions.
- A numeric comparison with mismatched or unknown units returns
  `needs_review`, not a pass/fail guess.
- PostgreSQL rules determine pass/fail. pgvector retrieval remains available for
  policy citations and explanation; retrieval failure is logged and reported in
  audit metrics without replacing the rule comparison.
- This implementation does not seed UK Skilled Worker policy values. Add only
  values and requirements verified against current official UK guidance.
- Scheduled scraping, automatic rule changes, and the admin review dashboard
  are out of scope. Scraped content must remain a proposal until a human
  approves it.
