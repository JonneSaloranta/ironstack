# Fitness & Activity Tracker — Claude Code Instructions

## Project goal

Build a self-hosted, mobile-first fitness and activity tracking application.

Primary goals:
- create and use workout programs
- log gym workouts accurately
- track progression
- provide explainable smart weight suggestions
- automatically detect personal records (PRs)
- track other physical activities manually
- track body measurements
- provide extensive statistics and charts

The application must run entirely on the user's own infrastructure with Docker.

## Technology

### Backend
- Python
- Django
- PostgreSQL

### Frontend
- Django Templates
- HTMX
- Alpine.js
- CSS

Do not introduce React, Vue, or another SPA framework without a strong architectural reason.

The application should be server-rendered first. Use HTMX for dynamic interactions and Alpine.js for small client-side state and interactions.

### Deployment
Use Docker Compose.

Production should include at least:
- Django application
- PostgreSQL
- reverse proxy

Persist database, media, and static data appropriately with Docker volumes.

## Architecture principles

- Keep the application modular.
- Keep business/domain logic out of Django views.
- Do not put progression algorithms in templates.
- Do not put PR calculations in views.
- Do not put analytics logic in templates.
- Use services/domain logic for complex business rules.
- Use Django ORM.
- Keep core data normalized.
- Avoid premature optimization.
- Avoid unnecessary dependencies.
- Write tests for domain logic.

Most important rule:

> Workout history must remain historically trustworthy.

Changing a program later must never alter completed workout history.

## Suggested Django apps

```text
apps/
    accounts/
    exercises/
    programs/
    workouts/
    progression/
    measurements/
    activities/
    analytics/
    core/
```

The exact structure may evolve if implementation demonstrates a better organization, but avoid putting the entire domain into one giant app.

## Development workflow

Before implementing a significant feature:

1. Inspect the existing project.
2. Inspect relevant models.
3. Inspect existing tests.
4. Identify dependencies and architectural impact.
5. Make the smallest coherent implementation.
6. Add or update tests.
7. Run the tests.
8. Fix failures.
9. Update documentation when architecture changes.

Do not blindly rewrite existing code.

Do not create duplicate abstractions when an existing abstraction can be extended.

Before adding a dependency, check whether Django, HTMX, Alpine.js, or existing project code already solves the problem.

## Commit messages

Always use Conventional Commits (`<type>[optional scope]: <description>`) for the summary line of every commit — e.g. `feat(backups): add admin-only web UI for creating and restoring backups`, `fix(nav): stop Home lighting up alongside Progress`, `docs: update CHANGELOG.md for 1.1.0`, `ci: pin GitHub Actions to a commit SHA`. Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `ci`, `perf`, `build`, `style`, `revert` — this exact set, since `.github/workflows/ci.yml`'s `create-release` job groups a release's auto-generated notes by this same type (see `docs/ARCHITECTURE.md` "Versioning"); a commit whose subject doesn't match one of these falls into a catch-all "Other" section there instead of being categorized properly. A longer body explaining the why (matching this project's established style) is still welcome below the summary line.

## Implementation order

Implement in phases:

1. Foundation
2. Exercises
3. Programs
4. Workout logging
5. PR engine
6. Progression engine
7. Smart suggestions
8. Body tracking
9. Activities
10. Analytics
11. Polish/security/accessibility/performance

Do not attempt to build the entire application in one pass.

## Product principle

The application is a training log first and an intelligent assistant second.

Automation must never take control away from the user.

The user always has final control over:
- exercise
- weight
- reps
- progression
- program
- schedule

Suggestions should make training easier, not force decisions.

## First task

Do not immediately implement the complete application.

First:
1. inspect the repository
2. determine whether a Django project already exists
3. create an implementation plan
4. identify missing infrastructure
5. propose the Django app structure
6. propose the initial domain/database model
7. identify architectural risks
8. create/update project documentation
9. only then begin implementation

After each phase:
- run tests
- verify migrations
- verify Docker
- inspect the UI
- fix issues before proceeding.
