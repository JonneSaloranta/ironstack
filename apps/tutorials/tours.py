"""Every page's guided tour, in one place. Read docs/TUTORIALS.md before
changing anything here.

A tour's steps point at elements by `data-tour="<anchor>"`; the
anchors live in the templates. apps.tutorials.tests renders every
tour's page and fails if a required anchor has gone missing, if a page
without arguments has neither a tour nor an entry in NO_TOUR_NEEDED, or
if a tour's text lacks a translation — so a template change can't
silently break a tour, and a new page can't silently go without one.

Depth follows the page: the pages people log on (a workout, training
mode, the food diary) get several steps; a simple list gets one or two;
plain forms get none (their labels and help texts already explain them).

Tours are grouped by `section` in the order they appear here — that is
the order of profile → Tutorials. Keep each section's tours together.
"""

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from .registry import Step, Tour, first_url

GETTING_STARTED = _("Getting started")
WORKOUTS = _("Workouts")
PROGRAMS = _("Programs and exercises")
PROGRESS = _("Progress")
BODY = _("Body and activities")
NUTRITION = _("Nutrition")
STRETCHING = _("Stretching")
SOCIAL = _("Friends and groups")
PROFILE = _("Profile")


def _sessions(user):
    from apps.workouts.models import WorkoutSession

    return WorkoutSession.objects.filter(user=user).order_by("-started_at")


def _active_session(user):
    return _sessions(user).filter(status="in_progress")


def _programs(user):
    from apps.programs.models import Program

    return Program.objects.filter(Q(owner=user) | Q(owner__isnull=True)).order_by(
        "owner", "name"
    )


def _exercises(user):
    from apps.exercises.models import Exercise

    return Exercise.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True).order_by(
        "name"
    )


def _measurement_types(user):
    from apps.measurements.models import MeasurementType

    return MeasurementType.objects.filter(
        Q(owner=user) | Q(owner__isnull=True), active=True
    ).order_by("pk")


def _activity_types(user):
    from apps.activities.models import ActivityType

    return ActivityType.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True).order_by(
        "pk"
    )


def _foods(user):
    from apps.nutrition.models import Food

    return Food.objects.filter(Q(owner=user) | Q(owner__isnull=True), active=True).order_by(
        "-created_at"
    )


def _recipes(user):
    from apps.nutrition.models import Recipe

    return Recipe.objects.filter(Q(owner=user) | Q(owner__isnull=True)).order_by("owner", "name")


def _diet_plans(user):
    from apps.nutrition.models import DietPlan

    return DietPlan.objects.filter(user=user).order_by("-created_at")


def _routines(user):
    from apps.stretching.models import StretchRoutine

    return StretchRoutine.objects.filter(
        Q(owner=user) | Q(owner__isnull=True), active=True
    ).order_by("owner", "pk")


def _active_stretch_session(user):
    from apps.stretching.models import StretchSession

    return StretchSession.objects.filter(user=user, status="in_progress")


TOURS = (
    # --- Getting started -------------------------------------------------
    Tour(
        key="dashboard",
        section=GETTING_STARTED,
        title=_("Home"),
        view_names=("dashboard",),
        steps=(
            Step(
                None,
                _("Welcome to IronStack"),
                _(
                    "A quick look around. Every section has a short tour like this the first "
                    "time you open it — skip any of them, or turn them all off."
                ),
            ),
            Step(
                "main-nav",
                _("Navigation"),
                _(
                    "Everything is one tap away from here: home, nutrition and stretching (if "
                    "you use them), progress, workouts, programs and your profile."
                ),
            ),
            Step(
                "dashboard-workout",
                _("Your workout"),
                _(
                    "Start a workout here, or jump back into the one you're in the middle of. "
                    "To follow a plan, start a workout from one of your programs instead."
                ),
            ),
            Step(
                "dashboard-achievements",
                _("Achievements"),
                _("Streaks and milestones from everyone on this server who shares them."),
                optional=True,
            ),
            Step(
                "dashboard-shortcuts",
                _("Shortcuts"),
                _("The exercise library, body tracking, activities and stretching."),
            ),
            Step(
                "nutrition-calendar",
                _("Calendar"),
                _(
                    "Each day's training and nutrition at a glance. Tap a day for details; "
                    "the “?” explains the colours."
                ),
                optional=True,
            ),
            Step(
                "nav-profile",
                _("Your profile"),
                _(
                    "Preferences, friends, your data — and Tutorials, where you can replay "
                    "any of these tours."
                ),
            ),
        ),
    ),
    # --- Workouts --------------------------------------------------------
    Tour(
        key="workout-list",
        section=WORKOUTS,
        title=_("Workout history"),
        view_names=("workouts:session-list",),
        steps=(
            Step(
                "session-history",
                _("Your workouts"),
                _("Every workout you've done, newest first. Open one to see or correct it."),
                optional=True,
            ),
            Step(
                "start-freeform",
                _("Start without a plan"),
                _(
                    "A freeform workout lets you add any exercises as you go. Workouts from "
                    "a program start from the program's page."
                ),
            ),
        ),
    ),
    Tour(
        key="workout-session",
        section=WORKOUTS,
        title=_("Logging a workout"),
        view_names=("workouts:session-detail",),
        start_url=first_url(_sessions, "workouts:session-detail"),
        steps=(
            Step(
                "session-summary",
                _("This workout"),
                _("When it started and ended, and how many sets and how much volume so far."),
            ),
            Step(
                "performed-exercise",
                _("One card per exercise"),
                _("The sets you've logged appear here, and you can edit or delete any of them."),
                optional=True,
            ),
            Step(
                "set-suggestion",
                _("Suggested weight"),
                _(
                    "Based on your last sessions, with the reason and how confident it is. "
                    "It's only a starting point — you decide what goes on the bar."
                ),
                optional=True,
            ),
            Step(
                "set-log-form",
                _("Log a set"),
                _(
                    "Enter weight and reps and tap Log set. RPE, RIR, warm-up and failure are "
                    "optional. New personal records are detected automatically."
                ),
                optional=True,
            ),
            Step(
                "add-exercise",
                _("Add an exercise"),
                _("Add anything that isn't in the plan — it only changes this workout."),
                optional=True,
            ),
            Step(
                "training-fab",
                _("Training mode"),
                _(
                    "One exercise at a time with big buttons and a rest timer — the easiest "
                    "way to log at the gym. This button follows you on every page."
                ),
                optional=True,
            ),
            Step(
                "finish-workout",
                _("Finish"),
                _(
                    "Complete the workout when you're done. Abandon keeps it in your history "
                    "as unfinished."
                ),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="training-mode",
        section=WORKOUTS,
        title=_("Training mode"),
        view_names=("workouts:session-train",),
        start_url=first_url(_active_session, "workouts:session-train"),
        steps=(
            Step(
                "train-nav",
                _("Exercises"),
                _(
                    "The dots show your progress. The arrows move between exercises in any "
                    "order — the next unfinished one opens by default."
                ),
            ),
            Step(
                "train-exercise",
                _("Current exercise"),
                _("Its target and the sets you've already logged; tap a set to edit it."),
                optional=True,
            ),
            Step(
                "train-suggestion",
                _("Suggested weight"),
                _("A starting point from your history. You can always enter something else."),
                optional=True,
            ),
            Step(
                "train-log-form",
                _("Log the set"),
                _(
                    "Weight and reps, then Log set. “More options” has RPE, RIR, warm-up, "
                    "failure and notes."
                ),
                optional=True,
            ),
            Step(
                "rest-timer",
                _("Rest timer"),
                _(
                    "Starts by itself after each set and chimes when rest is over. Adjust it "
                    "by 15 seconds, skip it, or mute the sound."
                ),
            ),
            Step(
                "train-full-view",
                _("Full view"),
                _("Every exercise at once — for reviewing or correcting the whole workout."),
            ),
        ),
    ),
    # --- Programs and exercises ------------------------------------------
    Tour(
        key="program-list",
        section=PROGRAMS,
        title=_("Programs"),
        view_names=("programs:program-list",),
        steps=(
            Step(
                "program-actions",
                _("Create or import"),
                _("Build your own program, or import one someone exported for you."),
            ),
            Step(
                "my-programs",
                _("Your programs"),
                _("Open a program to see its workouts and start one."),
                optional=True,
            ),
            Step(
                "program-templates",
                _("Ready-made programs"),
                _(
                    "Copy a built-in program to make it yours. Changing your copy never "
                    "changes workouts you've already logged."
                ),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="program-detail",
        section=PROGRAMS,
        title=_("A program"),
        view_names=("programs:program-detail",),
        start_url=first_url(_programs, "programs:program-detail"),
        steps=(
            Step(
                "program-info",
                _("About the program"),
                _(
                    "Its description and version. Editing a program never changes workouts "
                    "you've already logged."
                ),
            ),
            Step(
                "copy-template",
                _("Make it yours"),
                _("This is a template: copy it to a program of your own to edit and use it."),
                optional=True,
            ),
            Step(
                "program-workout",
                _("Workouts"),
                _(
                    "A program is a set of workouts, each with its exercises, sets, reps and "
                    "how the weight should progress."
                ),
                optional=True,
            ),
            Step(
                "add-prescription",
                _("Add exercises"),
                _("Add an exercise to a workout and set its target sets and reps."),
                optional=True,
            ),
            Step(
                "start-program-workout",
                _("Start it"),
                _("Starts a workout with these exercises and targets filled in."),
                optional=True,
            ),
            Step(
                "add-workout",
                _("Add a workout"),
                _("For example a second training day."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="exercise-list",
        section=PROGRAMS,
        title=_("Exercise library"),
        view_names=("exercises:exercise-list",),
        steps=(
            Step(
                "exercise-filters",
                _("Find an exercise"),
                _("Search by name, or filter by muscle group and equipment."),
            ),
            Step(
                "exercise-new",
                _("Your own exercises"),
                _("Missing something? Add it — only you will see it."),
            ),
        ),
    ),
    Tour(
        key="exercise-detail",
        section=PROGRAMS,
        title=_("An exercise"),
        view_names=("exercises:exercise-detail",),
        start_url=first_url(_exercises, "exercises:exercise-detail"),
        steps=(
            Step(
                "exercise-records",
                _("Personal records"),
                _("Your best lifts for this exercise, kept up to date automatically."),
            ),
            Step(
                "exercise-progress",
                _("Progress"),
                _("Your estimated one-rep max and volume for this exercise over time."),
            ),
        ),
    ),
    # --- Progress --------------------------------------------------------
    Tour(
        key="progress",
        section=PROGRESS,
        title=_("Progress"),
        view_names=("analytics:dashboard",),
        steps=(
            Step(
                "range-filter",
                _("Time range"),
                _("Everything on this page follows the range you pick here."),
            ),
            Step(
                "progress-summary",
                _("Summary"),
                _("Workouts, time spent training and total volume lifted in the range."),
            ),
            Step(
                "exercise-trend-picker",
                _("One exercise"),
                _("Pick an exercise to see how its strength has developed."),
                optional=True,
            ),
            Step(
                "volume-chart",
                _("Weekly volume"),
                _("How much you lifted each week. The table under the chart has exact figures."),
                optional=True,
            ),
            Step(
                "muscle-chart",
                _("Muscle groups"),
                _("Which muscles got the most work — handy for spotting imbalances."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="exercise-progress",
        section=PROGRESS,
        title=_("Progress for an exercise"),
        view_names=("analytics:exercise",),
        start_url=first_url(_exercises, "analytics:exercise"),
        steps=(
            Step(
                "range-filter",
                _("Time range"),
                _("Choose how far back to look."),
            ),
            Step(
                "one-rm-chart",
                _("Estimated 1RM"),
                _(
                    "The heaviest single rep you could likely lift, estimated from your "
                    "sets. A rising line means you're getting stronger."
                ),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="exercise-records",
        section=PROGRESS,
        title=_("Personal records"),
        view_names=("records:exercise-records",),
        start_url=first_url(_exercises, "records:exercise-records", arg="exercise_pk"),
        steps=(
            Step(
                "current-prs",
                _("Current records"),
                _("Heaviest weight, best estimated 1RM, most reps and more."),
            ),
            Step(
                "rep-maxes",
                _("Rep maxes"),
                _("The most weight you've lifted for each number of reps."),
            ),
        ),
    ),
    # --- Body and activities ---------------------------------------------
    Tour(
        key="measurement-list",
        section=BODY,
        title=_("Body tracking"),
        view_names=("measurements:type-list",),
        steps=(
            Step(
                "measurement-type",
                _("What you track"),
                _("Body weight, waist, body fat and more. Open one to log a reading."),
                optional=True,
            ),
            Step(
                "measurement-actions",
                _("Your own measurements"),
                _("Add anything else you want to follow."),
            ),
        ),
    ),
    Tour(
        key="measurement-history",
        section=BODY,
        title=_("A measurement"),
        view_names=("measurements:history",),
        start_url=first_url(_measurement_types, "measurements:history"),
        steps=(
            Step(
                "measurement-stats",
                _("Statistics"),
                _("Your current value and how it has changed since you started."),
                optional=True,
            ),
            Step(
                "measurement-log",
                _("Log a reading"),
                _("Enter today's value — or an earlier date to catch up."),
            ),
            Step(
                "measurement-table",
                _("History"),
                _("Every reading, newest first, with edit and delete."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="activity-list",
        section=BODY,
        title=_("Activities"),
        view_names=("activities:type-list",),
        steps=(
            Step(
                "activity-type",
                _("Activity types"),
                _("Running, cycling, walking and more. Open one to log it."),
                optional=True,
            ),
            Step(
                "activity-actions",
                _("Your own types"),
                _("Add any other activity you want to track."),
            ),
        ),
    ),
    Tour(
        key="activity-history",
        section=BODY,
        title=_("An activity"),
        view_names=("activities:history",),
        start_url=first_url(_activity_types, "activities:history"),
        steps=(
            Step(
                "activity-log",
                _("Log it"),
                _("Date, duration and, if you like, distance and notes."),
            ),
            Step(
                "activity-table",
                _("History"),
                _("Everything you've logged of this activity."),
                optional=True,
            ),
        ),
    ),
    # --- Nutrition -------------------------------------------------------
    Tour(
        key="nutrition-dashboard",
        section=NUTRITION,
        title=_("Nutrition overview"),
        view_names=("nutrition:dashboard",),
        steps=(
            Step(
                "nutrition-subnav",
                _("Nutrition sections"),
                _("Diary, foods, recipes, diet plans, calculators and statistics."),
            ),
            Step(
                "day-type",
                _("Training or rest day"),
                _("Whether you've completed a workout today. It's shown for context only."),
            ),
            Step(
                "nutrition-today",
                _("Today so far"),
                _("What you've eaten today against your targets."),
            ),
            Step(
                "nutrition-adjustment",
                _("Target check"),
                _(
                    "Compares your weight trend with your goal and may suggest a new target. "
                    "Nothing changes unless you accept it."
                ),
                optional=True,
            ),
            Step(
                "nutrition-target",
                _("Daily target"),
                _("Calories and macros, worked out from your profile and goal."),
                optional=True,
            ),
            Step(
                "nutrition-goal",
                _("Your goal"),
                _("Lose, keep or gain — and how fast. Change it at any time."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="food-diary",
        section=NUTRITION,
        title=_("Food diary"),
        view_names=("nutrition:diary-day",),
        steps=(
            Step(
                "diary-date-nav",
                _("Pick a day"),
                _("Move a day at a time, or pick any date to fill in."),
            ),
            Step(
                "diary-totals",
                _("Day total"),
                _("Calories and macros eaten on this day."),
            ),
            Step(
                "diary-meal",
                _("Meals"),
                _(
                    "Each meal lists what you logged. “Save as recipe” turns a meal you eat "
                    "often into a recipe you can log in one tap."
                ),
            ),
            Step(
                "diary-add-food",
                _("Add food"),
                _("Search, scan a barcode, or enter macros straight in."),
            ),
            Step(
                "diary-copy",
                _("Copy a day"),
                _("Eating the same as yesterday? Copy the whole day in one go."),
            ),
        ),
    ),
    Tour(
        key="food-diary-add",
        section=NUTRITION,
        title=_("Adding food"),
        view_names=("nutrition:diary-add-entry",),
        steps=(
            Step(
                "diary-meal-select",
                _("Which meal"),
                _("Everything you add on this page goes to the meal chosen here."),
            ),
            Step(
                "food-search",
                _("Search or scan"),
                _(
                    "Type a name or scan a barcode. Your own foods show first; Open Food "
                    "Facts can be searched too. The amount is prefilled with what you logged "
                    "last time."
                ),
            ),
            Step(
                "quick-entry",
                _("Enter macros manually"),
                _(
                    "Eating out? Type in the meal's calories and macros without creating a "
                    "food for it."
                ),
            ),
            Step(
                "most-used",
                _("Most used"),
                _("The foods you add most often, one tap away."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="food-list",
        section=NUTRITION,
        title=_("Foods"),
        view_names=("nutrition:food-list",),
        steps=(
            Step(
                "food-actions",
                _("Add foods"),
                _("Create your own, or browse Open Food Facts by category."),
            ),
            Step(
                "food-find-new",
                _("Find a new food"),
                _("Search Open Food Facts or scan a barcode to bring a product into your library."),
            ),
            Step(
                "food-filters",
                _("Your library"),
                _("Filter and sort every food you can log. Newest first by default."),
            ),
        ),
    ),
    Tour(
        key="food-detail",
        section=NUTRITION,
        title=_("A food"),
        view_names=("nutrition:food-detail",),
        start_url=first_url(_foods, "nutrition:food-detail"),
        steps=(
            Step(
                "nutrition-label",
                _("Nutrition facts"),
                _(
                    "Laid out like a package label: energy, fat, carbohydrate, fibre, protein "
                    "and salt — then any vitamins and minerals that are known."
                ),
            ),
        ),
    ),
    Tour(
        key="recipe-list",
        section=NUTRITION,
        title=_("Recipes"),
        view_names=("nutrition:recipe-list",),
        steps=(
            Step(
                "recipe-actions",
                _("Your recipes"),
                _("Combine foods into a recipe once, then log a serving in one tap."),
            ),
            Step(
                "template-recipes",
                _("Template recipes"),
                _("Ready-made recipes everyone can use."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="recipe-detail",
        section=NUTRITION,
        title=_("A recipe"),
        view_names=("nutrition:recipe-detail",),
        start_url=first_url(_recipes, "nutrition:recipe-detail"),
        steps=(
            Step(
                "recipe-ingredients",
                _("Ingredients"),
                _("The foods in it and their amounts. Nutrition is worked out from these."),
            ),
            Step(
                "recipe-log",
                _("Log it"),
                _("Add servings of this recipe to your diary."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="diet-plan-list",
        section=NUTRITION,
        title=_("Diet plans"),
        view_names=("nutrition:diet-plan-list",),
        steps=(
            Step(
                "diet-plan-actions",
                _("Create a plan"),
                _(
                    "A plan is built for you from your foods and recipes to hit your targets. "
                    "You can adjust every item afterwards."
                ),
            ),
        ),
    ),
    Tour(
        key="diet-plan-detail",
        section=NUTRITION,
        title=_("A diet plan"),
        view_names=("nutrition:diet-plan-detail",),
        start_url=first_url(_diet_plans, "nutrition:diet-plan-detail"),
        steps=(
            Step(
                "diet-plan-active",
                _("Active plan"),
                _("The active plan is shown on the nutrition overview each day."),
            ),
            Step(
                "diet-plan-meal",
                _("Meals"),
                _("Each meal's items and how close they come to its calorie budget."),
                optional=True,
            ),
            Step(
                "diet-plan-log",
                _("Log the plan"),
                _("Copy a day of the plan into your diary as if you'd eaten it."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="shopping-list",
        section=NUTRITION,
        title=_("Shopping list"),
        view_names=("nutrition:diet-plan-shopping",),
        start_url=first_url(_diet_plans, "nutrition:diet-plan-shopping"),
        steps=(
            Step(
                "shopping-trips",
                _("Shopping trips"),
                _("One list per shopping day, covering the days until the next one."),
                optional=True,
            ),
            Step(
                "shopping-list",
                _("What to buy"),
                _(
                    "Everything the plan's meals need, recipes included. The same food on "
                    "several days is added up, with each day's amount under it. Tick items "
                    "off as you shop."
                ),
                optional=True,
            ),
            Step(
                "shopping-settings",
                _("Shopping days"),
                _(
                    "Pick the days you go shopping, and whether a shopping day's own meals "
                    "go on that day's list. Set separately for each plan."
                ),
            ),
        ),
    ),
    Tour(
        key="calculators",
        section=NUTRITION,
        title=_("Calculators"),
        view_names=("nutrition:calculators-home",),
        steps=(
            Step(
                "calculator",
                _("Calculators"),
                _(
                    "Energy use, macros, body fat, water, BMI and more. They only calculate "
                    "— nothing is saved or changed."
                ),
            ),
        ),
    ),
    Tour(
        key="nutrition-stats",
        section=NUTRITION,
        title=_("Nutrition statistics"),
        view_names=("nutrition:stats",),
        steps=(
            Step(
                "stats-30-days",
                _("Last 30 days"),
                _(
                    "Median daily calories and macros. Days with nothing logged don't count, "
                    "and one barely-logged day doesn't drag the figure down."
                ),
            ),
            Step(
                "stats-periods",
                _("Longer and shorter periods"),
                _("The same medians for a week up to everything you've ever logged."),
            ),
            Step(
                "stats-chart",
                _("Daily calories"),
                _("Each of the last 30 days, with the exact figures in a table below."),
                optional=True,
            ),
        ),
    ),
    # --- Stretching ------------------------------------------------------
    Tour(
        key="stretching-home",
        section=STRETCHING,
        title=_("Stretching"),
        view_names=("stretching:home",),
        steps=(
            Step(
                "stretching-subnav",
                _("Stretching sections"),
                _("Overview, routines, the stretch library and your history."),
            ),
            Step(
                "stretch-summary",
                _("Your streak"),
                _("Days in a row with stretching, and your totals."),
            ),
            Step(
                "routine-card",
                _("Start a routine"),
                _("A guided session times every stretch for you."),
                optional=True,
            ),
            Step(
                "stretch-log",
                _("Log it afterwards"),
                _("Stretched without the timer? Log just how long you stretched."),
            ),
        ),
    ),
    Tour(
        key="stretch-routine",
        section=STRETCHING,
        title=_("A routine"),
        view_names=("stretching:routine-detail",),
        start_url=first_url(_routines, "stretching:routine-detail"),
        steps=(
            Step(
                "routine-info",
                _("About the routine"),
                _("How many stretches it has and roughly how long it takes."),
            ),
            Step(
                "routine-item",
                _("Stretches"),
                _("Each stretch with its hold time, sets and sides."),
                optional=True,
            ),
            Step(
                "routine-start",
                _("Start"),
                _("Opens the guided player."),
                optional=True,
            ),
            Step(
                "routine-add-stretch",
                _("Make it yours"),
                _("Add stretches and reorder them — in your own routines."),
                optional=True,
            ),
        ),
    ),
    Tour(
        key="stretch-player",
        section=STRETCHING,
        title=_("Guided session"),
        view_names=("stretching:session-play",),
        start_url=first_url(_active_stretch_session, "stretching:session-play"),
        steps=(
            Step(
                "stretch-player",
                _("The timer"),
                _(
                    "Counts down every hold and rest, with a chime between them. Pause, add or "
                    "remove 10 seconds, or skip. Leaving the page keeps your place."
                ),
            ),
            Step(
                "stretch-list-item",
                _("Stretches"),
                _("Mark a stretch done or skipped yourself if you prefer."),
                optional=True,
            ),
            Step(
                "stretch-finish",
                _("Finish"),
                _("Saves the session to your history."),
            ),
        ),
    ),
    # --- Friends and groups ----------------------------------------------
    Tour(
        key="friends",
        section=SOCIAL,
        title=_("Friends"),
        view_names=("social:friend-list",),
        steps=(
            Step(
                "friend-search",
                _("Find friends"),
                _(
                    "Search for people on this server. Friends can message you and see the "
                    "progress you choose to share."
                ),
            ),
        ),
    ),
    Tour(
        key="messages",
        section=SOCIAL,
        title=_("Messages"),
        view_names=("social:message-list",),
        steps=(
            Step(
                "messages-direct",
                _("Direct messages"),
                _("One conversation per friend."),
            ),
            Step(
                "messages-groups",
                _("Group chats"),
                _("Every group you're in has its own chat."),
            ),
        ),
    ),
    Tour(
        key="groups",
        section=SOCIAL,
        title=_("Groups"),
        view_names=("social:group-list",),
        steps=(
            Step(
                "group-new",
                _("Create a group"),
                _("Train together: invite friends or share an invite link."),
            ),
        ),
    ),
    # --- Profile ---------------------------------------------------------
    Tour(
        key="profile",
        section=PROFILE,
        title=_("Profile"),
        view_names=("profile",),
        steps=(
            Step(
                "profile-preferences",
                _("Preferences"),
                _("Units, language, timezone, theme, and which sections you use."),
            ),
            Step(
                "profile-social",
                _("Friends and groups"),
                _("Messages, friends and groups."),
            ),
            Step(
                "profile-data",
                _("Your data"),
                _("Everything you've logged is yours — browse it or download it."),
            ),
            Step(
                "profile-tutorials",
                _("Tutorials"),
                _("Replay any tour, or turn tutorials on or off."),
            ),
        ),
    ),
)


# Pages without URL arguments that deliberately have no tour, and why.
# apps.tutorials.tests fails for any such page missing from both TOURS
# and this list, so a new page always gets a conscious decision.
NO_TOUR_NEEDED = {
    # Forms: their labels and help texts already explain every field.
    "account-details": "form",
    "password_change": "form",
    "password_change_done": "confirmation",
    "exercises:exercise-create": "form",
    "programs:program-create": "form",
    "programs:program-import": "form",
    "measurements:type-create": "form",
    "activities:type-create": "form",
    "nutrition:goal-edit": "form",
    "nutrition:food-create": "form",
    "nutrition:recipe-create": "form",
    "nutrition:recipe-import": "form",
    "nutrition:diet-plan-create": "form",
    "nutrition:diet-plan-import": "form",
    "stretching:routine-create": "form",
    "stretching:stretch-create": "form",
    "stretching:quick-log": "form",
    "social:group-create": "form",
    "feedback-create": "form",
    "api_keys:key-create": "form",
    "two-factor-setup": "form",
    "two-factor-manage": "form",
    "two-factor-disable": "form",
    "account-delete": "form",
    # Nutrition onboarding is itself a guided, step-by-step flow.
    "nutrition:onboarding-body": "guided flow",
    "nutrition:onboarding-activity": "guided flow",
    "nutrition:onboarding-activity-level": "guided flow",
    "nutrition:onboarding-goal": "guided flow",
    "nutrition:onboarding-review": "guided flow",
    # Each calculator is one short form with its own explanation.
    "nutrition:calculator-bmr-tdee": "form",
    "nutrition:calculator-macros": "form",
    "nutrition:calculator-body-fat": "form",
    "nutrition:calculator-water-intake": "form",
    "nutrition:calculator-bmi": "form",
    "nutrition:calculator-waist-hip-ratio": "form",
    "nutrition:calculator-time-to-goal": "form",
    # Simple lists whose parent page's tour already covers them.
    "nutrition:food-browse": "simple list (food-list tour)",
    "stretching:routine-list": "simple list (stretching-home tour)",
    "stretching:stretch-list": "simple list (stretching-home tour)",
    "stretching:session-history": "simple list (stretching-home tour)",
    "social:friend-search": "search box (friends tour)",
    "social:block-list": "simple list",
    "coaching:client-list": "simple list",
    "coaching:my-coaches": "simple list",
    "coaching:shared-plan-list": "simple list",
    "api_keys:key-list": "has its own help dialog",
    "data-export": "self-explanatory download page",
    # Staff/admin-only tools.
    "backup-list": "staff only",
    "feedback-list": "staff only",
    "seo-settings": "staff only",
    # Signed-out pages: nobody to show a tour to yet.
    "login": "signed out",
    "signup": "signed out",
    "password_reset": "signed out",
    "password_reset_done": "signed out",
    "password_reset_complete": "signed out",
    # The tutorials page itself.
    "tutorials:list": "this is the tutorials page",
}
