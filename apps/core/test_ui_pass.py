"""Tests for the UI/UX audit pass: shared template helpers, and the
user-visible feedback that used to be missing (validation errors that
weren't shown, saves/deletes that redirected silently, unbounded history)."""

from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.activities.models import ActivityType
from apps.core.formatting import form_error_text
from apps.core.pagination import paginate_list
from apps.core.templatetags.core_extras import field_a11y, url_replace
from apps.measurements.models import MeasurementType, UnitKind
from apps.workouts import services as workout_services

User = get_user_model()


class _DemoForm(forms.Form):
    name = forms.CharField(help_text="Shown to friends")


class TemplateHelperTests(TestCase):
    def test_url_replace_keeps_filters_and_drops_empty_params(self):
        request = RequestFactory().get("/x/", {"q": "row & curl", "muscle_group": "", "page": "2"})
        query = url_replace(request, page=3)
        self.assertIn("page=3", query)
        self.assertIn("q=row+%26+curl", query)
        self.assertNotIn("muscle_group", query)

    def test_field_a11y_links_errors_and_help_to_the_control(self):
        form = _DemoForm(data={"name": ""})
        form.is_valid()
        html = field_a11y(form["name"])
        self.assertIn('aria-invalid="true"', html)
        self.assertIn("id_name_help", html)
        self.assertIn("id_name_error", html)

    def test_field_a11y_leaves_a_valid_field_alone(self):
        form = _DemoForm(data={"name": "Ada"})
        form.is_valid()
        self.assertNotIn("aria-invalid", field_a11y(form["name"]))

    def test_form_error_text_names_the_field(self):
        form = _DemoForm(data={"name": ""})
        form.is_valid()
        self.assertIn("required", form_error_text(form))

    def test_paginate_list_clamps_an_out_of_range_page(self):
        request = RequestFactory().get("/x/", {"page": "999"})
        page = paginate_list(request, list(range(60)), per_page=25)
        self.assertEqual(page.number, 3)
        self.assertEqual(len(page.object_list), 10)


class FeedbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def _messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_an_invalid_measurement_log_now_explains_itself(self):
        mtype = MeasurementType.objects.create(name="Calf", owner=self.user, unit_kind=UnitKind.LENGTH)
        response = self.client.post(reverse("measurements:log", args=[mtype.pk]), {"value": "abc"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("could not be saved" in m for m in self._messages(response)))

    def test_an_invalid_activity_log_now_explains_itself(self):
        atype = ActivityType.objects.create(name="Climbing", owner=self.user)
        response = self.client.post(reverse("activities:log", args=[atype.pk]), {})
        self.assertTrue(any("could not be saved" in m for m in self._messages(response)))

    def test_creating_a_program_confirms(self):
        response = self.client.post(
            reverse("programs:program-create"), {"name": "My plan", "description": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("Program created.", self._messages(response))


class WorkoutHistoryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")

    def test_history_is_paginated(self):
        for _ in range(25):
            workout_services.start_session(self.user, workout=None)
        response = self.client.get(reverse("workouts:session-list"))
        self.assertEqual(len(response.context["sessions"]), 20)
        self.assertContains(response, 'rel="next"')

    def test_history_card_shows_set_count_and_volume(self):
        from apps.exercises.models import Exercise

        session = workout_services.start_session(self.user, workout=None)
        performed = workout_services.add_performed_exercise(
            session, Exercise.objects.create(name="Row", owner=None)
        )
        workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        response = self.client.get(reverse("workouts:session-list"))
        self.assertContains(response, "1 exercise")
        self.assertContains(response, "500")

    def test_set_delete_asks_for_confirmation(self):
        from apps.exercises.models import Exercise

        session = workout_services.start_session(self.user, workout=None)
        performed = workout_services.add_performed_exercise(
            session, Exercise.objects.create(name="Row", owner=None)
        )
        workout_services.log_set(performed, weight=Decimal("100"), reps=5)
        response = self.client.get(reverse("workouts:session-detail", args=[session.pk]))
        self.assertContains(response, "hx-confirm=")

    def test_destructive_forms_use_the_shared_confirm_dialog(self):
        session = workout_services.start_session(self.user, workout=None)
        response = self.client.get(reverse("workouts:session-detail", args=[session.pk]))
        self.assertContains(response, "data-confirm=")
        self.assertNotContains(response, "confirm('")


class AnalyticsPickerTests(TestCase):
    def test_progress_page_lists_only_trained_exercises(self):
        from apps.exercises.models import Exercise

        user = User.objects.create_user("alice", password="s3cret-pass")
        self.client.login(username="alice", password="s3cret-pass")
        trained = Exercise.objects.create(name="Trained Lift", owner=None)
        Exercise.objects.create(name="Never Done", owner=None)
        session = workout_services.start_session(user, workout=None)
        performed = workout_services.add_performed_exercise(session, trained)
        workout_services.log_set(performed, weight=Decimal("50"), reps=5)
        response = self.client.get(reverse("analytics:dashboard"))
        self.assertContains(response, "Trained Lift")
        self.assertNotContains(response, "Never Done")
