# Threat model (PoC baseline)

Engineering checklist, not a compliance certification. Scope: local/isolated PoC with synthetic data.

| # | Threat | Mitigation in this PoC | Test | Remaining before pilot |
|---|---|---|---|---|
| T1 | Client claims a higher role | Roles loaded from DB per request; headers ignored | `test_frontend_cannot_claim_a_role` | SSO + role mapping |
| T2 | Access to unentitled dataset via payload | Entitlement check in interpret, validate_spec, data adapter (every request) | `test_cannot_access_unentitled_dataset_by_changing_payload` | Entitlements from IAM |
| T3 | Read/modify another user's draft | Owner check; reviewers only see submitted drafts; 404 instead of 403 | `test_cannot_read_or_edit_another_users_private_draft` | — |
| T4 | Injecting fields, formulas, aggregations, SQL | Strict schemas (`extra=forbid`), catalog allowlists, edit allowlist, no SQL anywhere | `test_cannot_inject_fields_formulas_or_aggregations`, policy tests | — |
| T5 | Prompt injection | Rules planner only extracts catalog terms; instruction-like text flagged and ignored; policy runs after planner | `test_prompt_injection_cannot_override_policy` | Re-test when an LLM provider is added |
| T6 | Tampered intent | Intents stored server-side and bound to the user | `test_plan_requires_server_side_interpretation` | Shared store |
| T7 | Publishing to an arbitrary Tableau project | Allowlist in API and in the REST adapter | `test_cannot_publish_to_non_allowlisted_project` | Tableau-side project permissions |
| T8 | Self-approval / bypassing review | Separation of duties; approval tied to exact version; edits reset approval | `test_only_owner_publishes_and_no_self_approval`, `test_edit_creates_version_and_invalidates_approval` | — |
| T9 | False "Published" claim | Status Published only after confirm + read-back verification; mock never Published | `test_mock_publish_is_never_published`, `test_verification_failure_is_not_published` | Real E2E |
| T10 | Secret leakage | `SecretStr`, never serialized; admin view shows only "set/missing"; audit strips secret keys; error messages exclude PAT | `test_secrets_never_reach_browser_or_audit`, `test_rest_errors_are_safe` | Secret manager |
| T11 | Raw prompt / identity disclosure in recommendations | Telemetry keeps normalized keys and salted pseudonymous groups only | `test_no_raw_prompts_or_identities_exposed` | Privacy/DPO review |
| T12 | Session theft | HttpOnly SameSite=Lax cookie, Secure outside local/test, hashed token at rest, TTL | — | SSO session policy, TLS |
| T13 | Resource abuse | Prompt length limit, per-user rate limit, row/series/range limits, kill switches | `test_empty_and_oversized_prompts`, `test_kill_switches` | Shared rate limiter, quotas |
| T14 | Information leak via errors | Stable codes, request IDs, generic 500 handler | `test_errors_are_safe` | — |
| T15 | Wrong numbers published | Stock/flow semantics, reconciliation checks, results embedded verbatim, Tableau value read-back | engine + reconciliation tests | BA validation of KPI definitions |
| T16 | Clickjacking / sniffing | `X-Frame-Options: DENY`, `nosniff`, `no-referrer` | — | CSP |

Out of scope by design: production databases, real PII, external LLMs, automatic Tableau permission changes.
