# UI Guidelines

## Core principle

Mobile-first.

Design initially for approximately 360–430px wide screens, then progressively enhance for desktop.

## Workout logging

This is the most important interaction in the application.

The user should be able to log:

```text
Exercise
Set
Weight
Reps
```

with as few taps as reasonably possible.

The interface must support:
- quick weight entry
- quick rep entry
- set completion
- editing
- RPE/RIR when desired
- failure marking
- notes
- suggested next weight

Do not make workout logging a multi-step form process.

## Dashboard

The dashboard should prioritize action and useful current information.

Possible content:

```text
Next workout
Last workout
This week's workouts
This week's volume
Recent PRs
Body weight
Recent activity
```

## Mobile navigation

A bottom navigation pattern is appropriate for mobile.

Possible sections:

- Home
- Workout
- Programs
- Progress
- Profile

Do not overload navigation with every feature.

## Desktop

Desktop can use:
- sidebar navigation
- wider analytics layouts
- tables
- larger charts
- multi-column cards

Keep information architecture consistent with mobile.

## Accessibility

Support:
- keyboard navigation
- visible focus states
- proper labels
- accessible forms
- sufficient contrast
- useful touch target sizes
- screen reader-friendly controls

Never rely on color alone to convey status.

## States

Major screens should handle:
- loading
- empty
- error
- success
- mobile
- desktop

## Visual style

The interface should feel like a practical training tool rather than a social media application.

Avoid unnecessary visual clutter, excessive animations, and tiny controls.
