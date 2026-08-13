# Analytics

Analytics should provide useful statistical information without becoming a collection of decorative charts.

## Strength analytics

Track:
- estimated 1RM over time
- max weight over time
- reps
- sets
- volume
- tonnage
- exercise frequency
- PR history
- intensity where meaningful

## Training analytics

Track:
- workouts per week
- workouts per month
- training frequency
- session duration
- total training time
- muscle-group volume
- exercise frequency
- consistency

## Body analytics

Track:
- body weight trend
- body fat trend
- circumference trends
- custom measurement trends

## Activity analytics

Track:
- activity minutes
- distance
- frequency
- activity type distribution

## Date ranges

Support:
- 7 days
- 30 days
- 3 months
- 6 months
- 1 year
- all time
- custom range

## Charts

Use charts when they communicate trends or comparisons clearly.

Examples:
- line chart: estimated 1RM over time
- line chart: body weight over time
- bar chart: weekly training volume
- line chart: exercise strength trend
- bar chart: muscle-group volume
- line chart: activity duration

Always provide important values in text as well.

## Performance

Analytics queries can become expensive.

Start with straightforward ORM queries and indexes.

Only add denormalized/cached aggregates when profiling demonstrates a need.

Analytics must always respect user ownership.
