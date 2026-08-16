# Product Requirements

## Core capabilities

The application must allow a user to:

1. create training programs
2. use built-in program templates
3. copy and modify templates
4. create Workout A/B/C style programs
5. optionally schedule workouts on weekdays
6. use programs without a fixed weekly schedule
7. log actual sets, reps, weights and notes
8. optionally record RPE/RIR
9. mark failed sets
10. receive progression recommendations
11. receive explainable smart weight suggestions
12. manually override recommendations
13. automatically detect PRs
14. track body measurements
15. log non-gym activities manually
16. view extensive analytics
17. use the system comfortably on mobile and desktop
18. know an explainable estimate of their daily calorie/macro needs
19. set a fat-loss, maintenance, or muscle-gain goal at a chosen rate
20. log food, meals, and recipes against calorie/macro targets
21. build a diet plan matching a calorie/macro/meal-count target
22. see whether their actual weight trend matches their goal, and
    receive an explainable, non-forced calorie adjustment suggestion
    when it doesn't (`docs/NUTRITION.md`)

## Explicitly out of scope for v1

Do not implement unless explicitly requested:

- Apple Health integration
- Google Fit integration
- smartwatch integrations
- social network
- public profiles
- messaging
- subscriptions
- advertisements

**Nutrition/calorie tracking was on this list through v1 ("do not
implement unless explicitly requested") — it was explicitly requested
for v2, see `docs/NUTRITION.md`, so items 18-22 above are now real
requirements, not a future possibility.**

## UX priorities

Priority order:

1. reliable workout logging
2. clear history
3. useful progression
4. understandable suggestions
5. PR feedback
6. useful analytics
7. visual polish

The system should be fast and practical during a real gym session.

## User control

Automation must remain advisory.

Users can always:
- change the suggested weight
- change reps
- skip exercises
- change programs
- change progression methods
- train outside the suggested schedule
