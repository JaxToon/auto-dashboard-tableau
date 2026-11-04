# Initial assessment

**Date:** 4 November 2026

## Repository state before this work

The working directory contained only a private product requirements document (PRD), which is not included in this repository. There was no existing application, framework, UI library, package manager setup, test suite or git repository. Nothing had to be preserved or migrated, so the PRD's suggested stack (§15.2) was adopted with the adjustments recorded in the ADR.

## Environment found

| Item | Found |
|---|---|
| OS | Windows 11 |
| Python | 3.11.9 (also 3.13) |
| Node / npm | 24 / 12 |
| Docker | 29.x available |
| Tableau access | None. No server URL, site, project or credentials were provided. |
| Approved AI gateway | None provided. |
| Bank metadata / data | None provided; none used. |

## Consequences

1. Tableau publishing is built against the documented REST API through Tableau Server Client, with a test seam (fake client) for unit tests. It cannot be proven end to end until the Tableau administrator provides a non-production project and publisher token. The E2E test fails until then.
2. With no approved model endpoint, the planner is the deterministic rules-based provider the PRD allows (§11.3). No content leaves the machine.
3. All data is synthetic and generated from formulas (no production-derived inputs).
