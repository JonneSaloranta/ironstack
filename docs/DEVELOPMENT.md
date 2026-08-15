# Development Guide

## Pinned versions & tooling

- Python 3.14
- Django 6.x
- PostgreSQL 16
- `psycopg` (v3) as the only new runtime dependency for DB connectivity
- Dev-only: `pytest`, `pytest-django`, `factory_boy` for tests; `ruff` for
  linting/formatting. None of these ship in the production image.
- No REST framework is added until an actual API client needs it (see
  `ARCHITECTURE.md` → API layer).

## Phase 1 — Foundation

Implement:
- Django project
- PostgreSQL
- Docker
- authentication
- base layout
- responsive/mobile-first design system

Acceptance:
- application starts through Docker Compose
- migrations work
- authentication works
- tests run
- basic responsive layout works

## Phase 2 — Exercises

Implement:
- exercises
- muscle groups
- equipment
- custom exercises

Acceptance:
- users can browse exercises
- users can create custom exercises
- permissions are enforced

## Phase 3 — Programs

Implement:
- programs
- workouts
- exercise prescriptions
- program templates
- copying templates
- scheduling
- program versioning

Acceptance:
- users can create and edit programs
- users can copy templates
- program edits do not alter historical sessions

## Phase 4 — Workout logging

Implement:
- start workout
- log sets
- edit sets
- complete workout
- abandoned workout
- workout history

Acceptance:
- mobile logging is fast
- historical data is preserved
- incomplete workouts remain visible

## Phase 5 — PR engine

Implement:
- max weight
- rep PR
- rep-specific PR
- estimated 1RM
- set volume PR
- session volume PR
- PR notifications

Acceptance:
- PR tests pass
- records survive program changes

## Phase 6 — Progression engine

Implement:
- double progression
- linear
- percentage-based
- rep-range
- RPE/RIR
- maintenance
- manual

Acceptance:
- progression logic has unit tests
- algorithms are independent from HTTP/templates

## Phase 7 — Smart suggestions

Implement:
- history analysis
- weight suggestions
- explanation
- confidence
- failure handling
- RPE/RIR handling

Acceptance:
- suggestions are deterministic for the same inputs
- user can override suggestions
- explanations are understandable

## Phase 8 — Body tracking

Implement:
- measurements
- history
- charts

## Phase 9 — Activities

Implement:
- manual activity types
- activity logging
- activity history
- activity analytics

## Phase 10 — Analytics

Implement:
- dashboard
- charts
- trends
- PR history
- muscle-group analytics
- date-range filtering

## Phase 11 — Polish

Perform:
- mobile UI refinement
- desktop UI refinement
- accessibility review
- security review
- query/performance review
- error handling review
- loading/empty states
- Docker production review

## Testing

At minimum test:

### Domain
- progression
- smart suggestions
- PR calculation
- 1RM calculation

### Permissions
- cross-user program access
- cross-user workout access
- cross-user measurement access
- cross-user activity access

### History
- changing programs does not alter completed sessions
- changing prescriptions does not alter performed sets

### UI/integration
- important workout flows
- authentication
- form submission
- error handling

## Migrations

Never edit an already-applied migration.

Create a new migration for schema changes.

## Dependencies

Before adding a package:
1. check if Django solves the requirement
2. check existing project dependencies
3. consider implementation complexity
4. add only when justified

## Definition of done

A feature is complete when:
- implementation exists
- migrations exist where needed
- tests exist
- tests pass
- mobile UI works
- desktop UI works
- permissions are enforced
- errors are handled
- documentation is updated when necessary
