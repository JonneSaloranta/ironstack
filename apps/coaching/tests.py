from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.exercises.models import Exercise
from apps.nutrition.models import DietPlan, DietPlanMeal, Food, MealSlot, ServingUnit
from apps.programs.models import ExercisePrescription, Program, Workout
from apps.workouts import services as workout_services
from apps.workouts.models import WorkoutSession

from . import services
from .models import CoachingRelationship, CoachingRequest, CoachingRequestStatus

User = get_user_model()


def make_coach(**kwargs):
    defaults = {"is_personal_trainer": True, "accepting_new_clients": True}
    defaults.update(kwargs)
    username = defaults.pop("username", "coach")
    return User.objects.create_user(username=username, password="s3cret-pass", **defaults)


def make_relationship(coach, coachee):
    request = services.send_coaching_request(coachee, coach)
    services.accept_coaching_request(request, acting_user=coach)
    return services.get_active_relationship(coach, coachee)


def make_shared_program(coach, coachee, *, name="Coach Program"):
    exercise = Exercise.objects.filter(owner__isnull=True).first()
    program = Program.objects.create(owner=coach, name=name)
    program.shared_with_clients.add(coachee)
    workout = Workout.objects.create(program=program, name="Day 1", order=0)
    ExercisePrescription.objects.create(
        workout=workout, exercise=exercise, order=0, set_count=3, min_reps=8, max_reps=12
    )
    return program


def make_shared_diet_plan(coach, coachee, *, name="Coach Diet Plan"):
    breakfast = MealSlot.objects.filter(owner__isnull=True).first()
    food = Food.objects.create(
        owner=coach,
        name="Coach Oats",
        serving_size=Decimal("100"),
        serving_unit=ServingUnit.GRAM,
        calories=150,
        protein_grams=Decimal("10"),
        carbohydrate_grams=Decimal("20"),
        fat_grams=Decimal("2"),
    )
    plan = DietPlan.objects.create(
        user=coach,
        name=name,
        target_calories=2000,
        target_protein_grams=Decimal("150"),
        target_carbohydrate_grams=Decimal("200"),
        target_fat_grams=Decimal("60"),
    )
    meal = DietPlanMeal.objects.create(diet_plan=plan, meal_slot=breakfast, target_calories=500)
    meal.items.create(food=food, quantity=Decimal("100"))
    plan.shared_with_clients.add(coachee)
    return plan


class CoachingRequestServiceTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")

    def test_sending_creates_a_pending_request(self):
        services.send_coaching_request(self.coachee, self.coach)
        request = CoachingRequest.objects.get(coach=self.coach, coachee=self.coachee)
        self.assertEqual(request.status, CoachingRequestStatus.PENDING)

    def test_cannot_request_yourself(self):
        with self.assertRaises(services.CoachingError):
            services.send_coaching_request(self.coach, self.coach)

    def test_cannot_request_a_user_who_isnt_a_pt(self):
        plain_user = User.objects.create_user(username="plain", password="s3cret-pass")
        with self.assertRaises(services.CoachingError):
            services.send_coaching_request(self.coachee, plain_user)

    def test_cannot_request_a_pt_not_accepting_new_clients(self):
        self.coach.accepting_new_clients = False
        self.coach.save()
        with self.assertRaises(services.CoachingError):
            services.send_coaching_request(self.coachee, self.coach)

    def test_cannot_send_a_duplicate_pending_request(self):
        services.send_coaching_request(self.coachee, self.coach)
        with self.assertRaises(services.CoachingError):
            services.send_coaching_request(self.coachee, self.coach)

    def test_cannot_request_a_coach_already_actively_coaching_you(self):
        make_relationship(self.coach, self.coachee)
        with self.assertRaises(services.CoachingError):
            services.send_coaching_request(self.coachee, self.coach)

    def test_re_requesting_after_a_decline_reopens_the_same_row(self):
        request = services.send_coaching_request(self.coachee, self.coach)
        services.decline_coaching_request(request, acting_user=self.coach)
        services.send_coaching_request(self.coachee, self.coach)
        request.refresh_from_db()
        self.assertEqual(request.status, CoachingRequestStatus.PENDING)
        self.assertEqual(CoachingRequest.objects.count(), 1)

    def test_a_coachee_may_have_multiple_simultaneous_coaches(self):
        other_coach = make_coach(username="coach2")
        make_relationship(self.coach, self.coachee)
        make_relationship(other_coach, self.coachee)
        self.assertEqual(len(services.active_coach_ids_for(self.coachee)), 2)


class AcceptDeclineCoachingRequestTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        self.request = services.send_coaching_request(self.coachee, self.coach)

    def test_accepting_creates_an_active_relationship(self):
        services.accept_coaching_request(self.request, acting_user=self.coach)
        self.assertTrue(services.is_active_relationship(self.coach, self.coachee))

    def test_only_the_coach_can_accept(self):
        with self.assertRaises(services.CoachingError):
            services.accept_coaching_request(self.request, acting_user=self.coachee)

    def test_declining_does_not_create_a_relationship(self):
        services.decline_coaching_request(self.request, acting_user=self.coach)
        self.assertFalse(services.is_active_relationship(self.coach, self.coachee))
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, CoachingRequestStatus.DECLINED)

    def test_cannot_respond_to_an_already_answered_request(self):
        services.accept_coaching_request(self.request, acting_user=self.coach)
        with self.assertRaises(services.CoachingError):
            services.accept_coaching_request(self.request, acting_user=self.coach)

    def test_accepting_creates_exactly_one_active_relationship(self):
        services.accept_coaching_request(self.request, acting_user=self.coach)
        self.assertEqual(
            CoachingRelationship.objects.filter(
                coach=self.coach, coachee=self.coachee, ended_at__isnull=True
            ).count(),
            1,
        )


class EndCoachingRelationshipTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        self.relationship = make_relationship(self.coach, self.coachee)

    def test_either_side_can_end_it(self):
        services.end_coaching_relationship(self.relationship, acting_user=self.coachee)
        self.assertFalse(services.is_active_relationship(self.coach, self.coachee))
        self.relationship.refresh_from_db()
        self.assertIsNotNone(self.relationship.ended_at)

    def test_an_unrelated_user_cannot_end_it(self):
        stranger = User.objects.create_user(username="stranger", password="s3cret-pass")
        with self.assertRaises(services.CoachingError):
            services.end_coaching_relationship(self.relationship, acting_user=stranger)

    def test_ending_twice_raises(self):
        services.end_coaching_relationship(self.relationship, acting_user=self.coach)
        with self.assertRaises(services.CoachingError):
            services.end_coaching_relationship(self.relationship, acting_user=self.coach)

    def test_ending_keeps_the_row_and_a_new_request_can_start_again(self):
        services.end_coaching_relationship(self.relationship, acting_user=self.coach)
        self.assertTrue(CoachingRelationship.objects.filter(pk=self.relationship.pk).exists())
        make_relationship(self.coach, self.coachee)
        self.assertEqual(
            CoachingRelationship.objects.filter(coach=self.coach, coachee=self.coachee).count(),
            2,
        )


class ProgramVisibilityAndImportTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")

    def test_unshared_program_is_invisible_and_unimportable(self):
        program = make_shared_program(self.coach, self.coachee)
        program.shared_with_clients.clear()
        make_relationship(self.coach, self.coachee)
        from apps.programs import services as program_services

        self.assertNotIn(program, program_services.visible_to(self.coachee))
        with self.assertRaises(services.CoachingError):
            services.import_program_from_coach(program, self.coachee)

    def test_shared_program_invisible_without_a_relationship(self):
        program = make_shared_program(self.coach, self.coachee)
        from apps.programs import services as program_services

        self.assertNotIn(program, program_services.visible_to(self.coachee))
        with self.assertRaises(services.CoachingError):
            services.import_program_from_coach(program, self.coachee)

    def test_shared_and_active_relationship_succeeds_and_is_independent(self):
        program = make_shared_program(self.coach, self.coachee)
        make_relationship(self.coach, self.coachee)
        from apps.programs import services as program_services

        self.assertIn(program, program_services.visible_to(self.coachee))
        copy = services.import_program_from_coach(program, self.coachee)

        self.assertEqual(copy.owner, self.coachee)
        self.assertEqual(copy.imported_from, program)
        self.assertEqual(copy.coach_snapshot_version, program.version)
        self.assertNotEqual(copy.pk, program.pk)

        copy.workouts.first().delete()
        program.refresh_from_db()
        self.assertEqual(program.workouts.count(), 1)

    def test_sharing_with_one_client_does_not_expose_it_to_another(self):
        other_coachee = User.objects.create_user(username="other", password="s3cret-pass")
        program = make_shared_program(self.coach, self.coachee)
        make_relationship(self.coach, self.coachee)
        make_relationship(self.coach, other_coachee)
        from apps.programs import services as program_services

        self.assertIn(program, program_services.visible_to(self.coachee))
        self.assertNotIn(program, program_services.visible_to(other_coachee))
        with self.assertRaises(services.CoachingError):
            services.import_program_from_coach(program, other_coachee)

    def test_import_blocked_once_relationship_ends(self):
        program = make_shared_program(self.coach, self.coachee)
        relationship = make_relationship(self.coach, self.coachee)
        services.end_coaching_relationship(relationship, acting_user=self.coach)
        with self.assertRaises(services.CoachingError):
            services.import_program_from_coach(program, self.coachee)


class DietPlanVisibilityAndImportTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")

    def test_unshared_plan_is_invisible_and_unimportable(self):
        plan = make_shared_diet_plan(self.coach, self.coachee)
        plan.shared_with_clients.clear()
        make_relationship(self.coach, self.coachee)
        from apps.nutrition.services import diet_plans_visible_to

        self.assertNotIn(plan, diet_plans_visible_to(self.coachee))
        with self.assertRaises(services.CoachingError):
            services.import_diet_plan_from_coach(plan, self.coachee)

    def test_shared_plan_invisible_without_a_relationship(self):
        plan = make_shared_diet_plan(self.coach, self.coachee)
        from apps.nutrition.services import diet_plans_visible_to

        self.assertNotIn(plan, diet_plans_visible_to(self.coachee))
        with self.assertRaises(services.CoachingError):
            services.import_diet_plan_from_coach(plan, self.coachee)

    def test_shared_and_active_relationship_succeeds_and_lands_inactive(self):
        plan = make_shared_diet_plan(self.coach, self.coachee)
        make_relationship(self.coach, self.coachee)
        copy = services.import_diet_plan_from_coach(plan, self.coachee)

        self.assertEqual(copy.user, self.coachee)
        self.assertFalse(copy.is_active)
        self.assertEqual(copy.imported_from, plan)
        self.assertEqual(copy.coach_snapshot_version, plan.version)

    def test_import_blocked_once_relationship_ends(self):
        plan = make_shared_diet_plan(self.coach, self.coachee)
        relationship = make_relationship(self.coach, self.coachee)
        services.end_coaching_relationship(relationship, acting_user=self.coach)
        with self.assertRaises(services.CoachingError):
            services.import_diet_plan_from_coach(plan, self.coachee)

    def test_sharing_with_one_client_does_not_expose_it_to_another(self):
        other_coachee = User.objects.create_user(username="other", password="s3cret-pass")
        plan = make_shared_diet_plan(self.coach, self.coachee)
        make_relationship(self.coach, self.coachee)
        make_relationship(self.coach, other_coachee)
        from apps.nutrition.services import diet_plans_visible_to

        self.assertIn(plan, diet_plans_visible_to(self.coachee))
        self.assertNotIn(plan, diet_plans_visible_to(other_coachee))
        with self.assertRaises(services.CoachingError):
            services.import_diet_plan_from_coach(plan, other_coachee)


class ProgramUpdateFlowTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        make_relationship(self.coach, self.coachee)
        self.program = make_shared_program(self.coach, self.coachee)
        self.copy = services.import_program_from_coach(self.program, self.coachee)

    def test_no_update_when_unchanged(self):
        self.assertFalse(services.program_update_available(self.copy))

    def test_update_detected_after_coach_edits_and_bumps_version(self):
        Workout.objects.create(program=self.program, name="Day 2", order=1)
        self.program.bump_version()
        self.assertTrue(services.program_update_available(self.copy))

    def test_diff_contains_the_new_workout_name(self):
        Workout.objects.create(program=self.program, name="Brand New Day", order=1)
        self.program.bump_version()
        diff = services.program_update_diff(self.copy)
        self.assertIn("Brand New Day", diff)

    def test_apply_replaces_content_and_refreshes_snapshot(self):
        Workout.objects.create(program=self.program, name="Day 2", order=1)
        self.program.bump_version()

        services.apply_program_update(self.copy, acting_user=self.coachee)

        self.copy.refresh_from_db()
        self.assertEqual(self.copy.workouts.count(), 2)
        self.assertEqual(self.copy.coach_snapshot_version, self.program.version)
        self.assertFalse(services.program_update_available(self.copy))

    def test_applying_with_no_source_left_raises(self):
        self.program.delete()
        self.copy.refresh_from_db()
        with self.assertRaises(services.CoachingError):
            services.apply_program_update(self.copy, acting_user=self.coachee)

    def test_only_the_owner_can_apply_an_update(self):
        Workout.objects.create(program=self.program, name="Day 2", order=1)
        self.program.bump_version()
        with self.assertRaises(services.CoachingError):
            services.apply_program_update(self.copy, acting_user=self.coach)

    def test_existing_workout_history_survives_an_applied_update(self):
        """CLAUDE.md: "Workout history must remain historically
        trustworthy." — applying a coach's update deletes and
        recreates the client's own Workout/ExercisePrescription rows;
        this proves a WorkoutSession/PerformedExercise logged before
        that happened is completely unaffected by it."""
        workout = self.copy.workouts.first()
        session = workout_services.start_session(self.coachee, workout=workout)
        performed = session.performed_exercises.get()
        performed.sets.create(set_number=1, weight=Decimal("100"), reps=8)

        Workout.objects.create(program=self.program, name="Day 2", order=1)
        self.program.bump_version()
        services.apply_program_update(self.copy, acting_user=self.coachee)

        session.refresh_from_db()
        performed.refresh_from_db()
        self.assertTrue(WorkoutSession.objects.filter(pk=session.pk).exists())
        self.assertEqual(performed.sets.get().weight, Decimal("100"))
        self.assertIsNone(session.workout_id)
        self.assertIsNone(performed.prescription_id)


class DietPlanUpdateFlowTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        make_relationship(self.coach, self.coachee)
        self.plan = make_shared_diet_plan(self.coach, self.coachee)
        self.copy = services.import_diet_plan_from_coach(self.plan, self.coachee)

    def test_no_update_when_unchanged(self):
        self.assertFalse(services.diet_plan_update_available(self.copy))

    def test_update_detected_after_coach_edits_and_bumps_version(self):
        meal = self.plan.meals.first()
        meal.target_calories = 999
        meal.save()
        self.plan.bump_version()
        self.assertTrue(services.diet_plan_update_available(self.copy))

    def test_apply_replaces_content_and_refreshes_snapshot(self):
        meal = self.plan.meals.first()
        meal.target_calories = 999
        meal.save()
        self.plan.bump_version()

        services.apply_diet_plan_update(self.copy, acting_user=self.coachee)

        self.copy.refresh_from_db()
        self.assertEqual(self.copy.meals.first().target_calories, 999)
        self.assertEqual(self.copy.coach_snapshot_version, self.plan.version)
        self.assertFalse(services.diet_plan_update_available(self.copy))

    def test_existing_diary_entries_are_unaffected_by_an_applied_update(self):
        from apps.nutrition.models import DiaryEntry

        food = self.copy.meals.first().items.first().food
        entry = DiaryEntry.objects.create(
            user=self.coachee,
            food=food,
            quantity=Decimal("100"),
            date="2024-01-01",
            meal_slot=MealSlot.objects.filter(owner__isnull=True).first(),
        )
        meal = self.plan.meals.first()
        meal.target_calories = 999
        meal.save()
        self.plan.bump_version()

        services.apply_diet_plan_update(self.copy, acting_user=self.coachee)

        entry.refresh_from_db()
        self.assertEqual(entry.quantity, Decimal("100"))

    def test_only_the_owner_can_apply_an_update(self):
        meal = self.plan.meals.first()
        meal.target_calories = 999
        meal.save()
        self.plan.bump_version()
        with self.assertRaises(services.CoachingError):
            services.apply_diet_plan_update(self.copy, acting_user=self.coach)


class ClientAnalyticsAccessTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        make_relationship(self.coach, self.coachee)
        self.client.login(username="coach", password="s3cret-pass")

    def test_coach_can_view_an_active_clients_detail_page(self):
        response = self.client.get(reverse("coaching:client-detail", args=[self.coachee.username]))
        self.assertEqual(response.status_code, 200)

    def test_coach_cannot_view_a_non_client(self):
        stranger = User.objects.create_user(username="stranger", password="s3cret-pass")
        response = self.client.get(reverse("coaching:client-detail", args=[stranger.username]))
        self.assertEqual(response.status_code, 404)

    def test_coach_cannot_view_after_the_relationship_ends(self):
        relationship = services.get_active_relationship(self.coach, self.coachee)
        services.end_coaching_relationship(relationship, acting_user=self.coach)
        response = self.client.get(reverse("coaching:client-detail", args=[self.coachee.username]))
        self.assertEqual(response.status_code, 404)

    def test_show_achievements_off_does_not_block_the_coach(self):
        self.coachee.show_achievements = False
        self.coachee.save()
        response = self.client.get(reverse("coaching:client-detail", args=[self.coachee.username]))
        self.assertEqual(response.status_code, 200)


class CoachingRequestViewTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")

    def test_request_send_requires_post(self):
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.get(reverse("coaching:request-send", args=[self.coach.pk]))
        self.assertEqual(response.status_code, 405)

    def test_request_send_success_flow(self):
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.post(
            reverse("coaching:request-send", args=[self.coach.pk]), follow=True
        )
        self.assertContains(response, "Coaching request sent")
        self.assertTrue(
            CoachingRequest.objects.filter(coach=self.coach, coachee=self.coachee).exists()
        )

    def test_request_respond_accept_flow(self):
        request = services.send_coaching_request(self.coachee, self.coach)
        self.client.login(username="coach", password="s3cret-pass")
        response = self.client.post(
            reverse("coaching:request-respond", args=[request.pk]),
            {"action": "accept"},
            follow=True,
        )
        self.assertContains(response, "accepted")
        self.assertTrue(services.is_active_relationship(self.coach, self.coachee))

    def test_only_the_coach_can_respond(self):
        request = services.send_coaching_request(self.coachee, self.coach)
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.post(
            reverse("coaching:request-respond", args=[request.pk]), {"action": "accept"}
        )
        self.assertEqual(response.status_code, 404)

    def test_relationship_end_requires_post(self):
        relationship = make_relationship(self.coach, self.coachee)
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.get(reverse("coaching:relationship-end", args=[relationship.pk]))
        self.assertEqual(response.status_code, 405)

    def test_relationship_end_success_flow(self):
        relationship = make_relationship(self.coach, self.coachee)
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.post(
            reverse("coaching:relationship-end", args=[relationship.pk]), follow=True
        )
        self.assertContains(response, "ended")
        self.assertFalse(services.is_active_relationship(self.coach, self.coachee))

    def test_pending_coaching_badge_reflects_incoming_requests(self):
        services.send_coaching_request(self.coachee, self.coach)
        self.client.login(username="coach", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertTrue(response.context["coaching_badge"])

    def test_plain_user_has_no_coaching_badge(self):
        self.client.login(username="coachee", password="s3cret-pass")
        response = self.client.get(reverse("profile"))
        self.assertFalse(response.context["coaching_badge"])


class SharedPlanImportViewTests(TestCase):
    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        make_relationship(self.coach, self.coachee)
        self.program = make_shared_program(self.coach, self.coachee)
        self.plan = make_shared_diet_plan(self.coach, self.coachee)
        self.client.login(username="coachee", password="s3cret-pass")

    def test_shared_plan_list_shows_both(self):
        response = self.client.get(reverse("coaching:shared-plan-list"))
        self.assertContains(response, self.program.name)
        self.assertContains(response, self.plan.name)

    def test_program_import_view(self):
        response = self.client.post(
            reverse("coaching:program-import", args=[self.program.pk]), follow=True
        )
        self.assertContains(response, "Imported")
        self.assertTrue(
            Program.objects.filter(owner=self.coachee, imported_from=self.program).exists()
        )

    def test_diet_plan_import_view(self):
        response = self.client.post(
            reverse("coaching:diet-plan-import", args=[self.plan.pk]), follow=True
        )
        self.assertContains(response, "Imported")
        self.assertTrue(
            DietPlan.objects.filter(user=self.coachee, imported_from=self.plan).exists()
        )

    def test_program_update_preview_and_apply_views(self):
        copy = services.import_program_from_coach(self.program, self.coachee)
        Workout.objects.create(program=self.program, name="Extra Day", order=1)
        self.program.bump_version()

        preview = self.client.get(reverse("coaching:program-update-preview", args=[copy.pk]))
        self.assertContains(preview, "Extra Day")

        response = self.client.post(
            reverse("coaching:program-update-apply", args=[copy.pk]), follow=True
        )
        self.assertContains(response, "updated")
        copy.refresh_from_db()
        self.assertEqual(copy.workouts.count(), 2)


class ShareFormClientScopingTests(TestCase):
    """The whole point of a many-to-many `shared_with_clients` instead
    of a blanket boolean: a coach only ever gets to choose among their
    own currently active clients, never every user on the instance."""

    def setUp(self):
        self.coach = make_coach(username="coach")
        self.active_client = User.objects.create_user(
            username="active_client", password="s3cret-pass"
        )
        self.stranger = User.objects.create_user(username="stranger", password="s3cret-pass")
        make_relationship(self.coach, self.active_client)

    def test_program_form_only_offers_active_clients(self):
        from apps.programs.forms import ProgramForm

        form = ProgramForm(user=self.coach)
        queryset = form.fields["shared_with_clients"].queryset
        self.assertIn(self.active_client, queryset)
        self.assertNotIn(self.stranger, queryset)

    def test_program_form_hides_the_field_for_a_non_pt(self):
        from apps.programs.forms import ProgramForm

        plain_user = User.objects.create_user(username="plain", password="s3cret-pass")
        form = ProgramForm(user=plain_user)
        self.assertNotIn("shared_with_clients", form.fields)

    def test_diet_plan_share_form_only_offers_active_clients(self):
        from apps.nutrition.forms import DietPlanShareForm

        form = DietPlanShareForm(coach=self.coach)
        queryset = form.fields["clients"].queryset
        self.assertIn(self.active_client, queryset)
        self.assertNotIn(self.stranger, queryset)

    def test_program_can_be_shared_with_one_client_via_the_form(self):
        from apps.programs.forms import ProgramForm

        other_client = User.objects.create_user(username="other_client", password="s3cret-pass")
        make_relationship(self.coach, other_client)
        program = Program.objects.create(owner=self.coach, name="Solo Share")

        form = ProgramForm(
            data={
                "name": "Solo Share",
                "description": "",
                "shared_with_clients": [self.active_client.pk],
            },
            instance=program,
            user=self.coach,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertIn(self.active_client, program.shared_with_clients.all())
        self.assertNotIn(other_client, program.shared_with_clients.all())


class ProgramAndDietPlanListGroupingTests(TestCase):
    """apps.programs.views.ProgramListView/apps.nutrition.views.
    DietPlanListView both split a user's own flat list into groups —
    asked for directly: a program/plan imported from a coach should
    never be indistinguishable from one the user built themselves."""

    def setUp(self):
        self.coach = make_coach(username="coach")
        self.coachee = User.objects.create_user(username="coachee", password="s3cret-pass")
        make_relationship(self.coach, self.coachee)
        self.client.login(username="coachee", password="s3cret-pass")

    def test_program_list_splits_own_coach_and_template_programs(self):
        own_program = Program.objects.create(owner=self.coachee, name="My Own Program")
        own_template = Program.objects.create(
            owner=self.coachee, name="My Own Template", is_template=True
        )
        source = make_shared_program(self.coach, self.coachee, name="Coach's Program")
        imported = services.import_program_from_coach(source, self.coachee)

        response = self.client.get(reverse("programs:program-list"))

        self.assertIn(own_program, response.context["own_programs"])
        self.assertIn(own_template, response.context["own_templates"])
        self.assertIn(imported, response.context["coach_programs"])
        self.assertNotIn(imported, response.context["own_programs"])
        self.assertNotIn(own_program, response.context["coach_programs"])

    def test_diet_plan_list_splits_own_and_coach_plans(self):
        own_plan = DietPlan.objects.create(
            user=self.coachee, name="My Own Plan", target_calories=2000,
            target_protein_grams=Decimal("1"), target_carbohydrate_grams=Decimal("1"),
            target_fat_grams=Decimal("1"),
        )
        source = make_shared_diet_plan(self.coach, self.coachee, name="Coach's Plan")
        imported = services.import_diet_plan_from_coach(source, self.coachee)

        response = self.client.get(reverse("nutrition:diet-plan-list"))

        self.assertIn(own_plan, response.context["own_plans"])
        self.assertIn(imported, response.context["coach_plans"])
        self.assertNotIn(imported, response.context["own_plans"])
        self.assertNotIn(own_plan, response.context["coach_plans"])
