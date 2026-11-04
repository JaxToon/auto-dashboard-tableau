# API reference (v1)

Live OpenAPI: http://localhost:8000/docs. All endpoints except `/health/*` and mock login require the `pbac_session` cookie. Errors use one shape:

```json
{"error": {"code": "STABLE_CODE", "message": "User-safe message", "request_id": "…", "details": {}}}
```

Send `X-Request-ID` to set the correlation ID; it is echoed back and written to audit events.

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/api/v1/auth/mock-users` | anyone (mock mode only) | List mock users |
| POST | `/api/v1/auth/mock-login` | anyone (mock mode only) | `{user_id}` → session cookie |
| POST | `/api/v1/auth/logout` | user | End session |
| GET | `/api/v1/me` | user | Identity, roles, permissions, app mode |
| POST | `/api/v1/analytics/interpret` | `analytics:ask` | `{question, dataset_id?, clarifications?}` → `status` (`ok` / `needs_clarification` / `unsupported`), intent, `interpretation_id`, resolved definitions, quick result for answers/charts |
| POST | `/api/v1/dashboards/plan` | `dashboard:create` | `{interpretation_id, force_dashboard?}` → draft v1 |
| GET | `/api/v1/dashboards?scope=mine\|review` | user / reviewer | List authorized drafts |
| GET | `/api/v1/dashboards/{id}` | owner, or reviewer once submitted | Draft, versions, reviews, publications |
| PATCH | `/api/v1/dashboards/{id}` | owner | `title, description, layout, time_range_start, time_range_end, filters, chart_types, visualization_titles, remove_visualizations` → new version |
| POST | `/api/v1/dashboards/{id}/validate` | reader | Spec + result validation report |
| POST | `/api/v1/dashboards/{id}/preview` | reader | Validated results + provenance |
| POST | `/api/v1/dashboards/{id}/submit-review` | owner | → Needs Review (or Ready to Publish when review is off) |
| POST | `/api/v1/dashboards/{id}/approve` | `dashboard:review`, not owner | `{decision: approved\|changes_requested, comment}` |
| POST | `/api/v1/dashboards/{id}/publish` | owner with `dashboard:publish` | `{confirm: true, target_project_id?}` → publication (Published only when verified) |
| POST | `/api/v1/dashboards/{id}/archive` | owner | Archive |
| GET | `/api/v1/catalog/datasets` | user | Entitled datasets, fields, freshness, example prompts |
| GET | `/api/v1/catalog/metrics` | user | KPI definitions for entitled datasets |
| POST | `/api/v1/recommendations/analyze` | `recommendation:review` | Cluster minimized events; thresholds and rule results |
| GET | `/api/v1/recommendations` | `recommendation:view` | Reviewers: all; users: accepted, entitled datasets |
| POST | `/api/v1/recommendations/{id}/decision` | reviewer / user | `{decision: accept\|dismiss\|create_draft}` |
| POST | `/api/v1/integrations/tableau/test` | `admin:integrations` | Connection + target project check (no secrets) |
| GET | `/api/v1/integrations/tableau/projects` | `admin:integrations` | Visible projects, allowlist flags |
| GET / PATCH | `/api/v1/admin/settings` | `admin:settings` | Flags (env ceilings), thresholds, connector health |
| GET | `/api/v1/audit/events?action=` | user (own) / auditor (all) | Audit events |
| GET | `/api/v1/activity` | user | Own request history |
| GET | `/health/live`, `/health/ready` | anyone | Liveness / readiness (no secrets) |

Key error codes: `NOT_AUTHENTICATED`, `PERMISSION_DENIED`, `DATASET_NOT_ENTITLED`, `PROMPT_TOO_LONG`, `SPEC_INVALID`, `EDIT_INVALID`, `PUBLISH_BLOCKED`, `CONFIRMATION_REQUIRED`, `TABLEAU_PROJECT_NOT_ALLOWED`, `TABLEAU_NOT_CONFIGURED`, `SELF_APPROVAL_NOT_ALLOWED`, `AI_PLANNING_DISABLED`, `DATA_ACCESS_DISABLED`, `ENV_CEILING`, `RATE_LIMITED`.
