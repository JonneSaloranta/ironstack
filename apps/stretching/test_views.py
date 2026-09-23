"""View tests for apps.stretching: login required, ownership (another
user's rows are 404s), and the main flows end to end."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.exercises.models import Exercise
from apps.workouts import services as workout_services

from . import services
from .models import (
    PerformedStretchStatus,
    Stretch,
    StretchRoutine,
    StretchSession,
    StretchSessionStatus,
)

User = get_user_model()


class StretchingViewTestCase(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="s3cret-pass")
        self.bob = User.objects.create_user(username="bob", password="s3cret-pass")
        self.client.force_login(self.alice)
        self.cat_cow = Stretch.objects.get(name="Cat-Cow")
        self.quad = Stretch.objects.get(name="Standing Quad Stretch")
        self.system_routine = StretchRoutine.objects.get(name="Lower Body Cool-Down")


class LoginRequiredTests(StretchingViewTestCase):
    def test_pages_redirect_anonymous_users(self):
        self.client.logout()
        for name in ["home", "routine-list", "stretch-list", "session-history", "quick-log"]:
            with self.subTest(name=name):
                response = self.client.get(reverse(f"stretching:{name}"))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response["Location"])


class PagesRenderTests(StretchingViewTestCase):
    def test_every_list_page_renders(self):
        services.quick_log(self.alice, date=timezone.localdate(), duration=timedelta(minutes=9))
        for name in ["home", "routine-list", "stretch-list", "session-history", "quick-log"]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(f"stretching:{name}")).status_code, 200)

    def test_home_lists_system_routines(self):
        response = self.client.get(reverse("stretching:home"))
        self.assertContains(response, "Lower Body Cool-Down")

    def test_library_filters_by_muscle_group_and_type(self):
        quads = self.quad.muscle_groups.get(name="Quads")
        response = self.client.get(
            reverse("stretching:stretch-list"), {"muscle_group": quads.pk, "kind": "static"}
        )
        self.assertContains(response, "Standing Quad Stretch")
        self.assertNotContains(response, "Cat-Cow")

    def test_stretch_detail_shows_instructions(self):
        response = self.client.get(reverse("stretching:stretch-detail", args=[self.quad.pk]))
        self.assertContains(response, "grab that ankle")


class RoutineViewTests(StretchingViewTestCase):
    def test_create_routine_and_add_a_stretch(self):
        response = self.client.post(
            reverse("stretching:routine-create"), {"name": "Evening", "description": ""}
        )
        routine = StretchRoutine.objects.get(owner=self.alice, name="Evening")
        self.assertRedirects(response, reverse("stretching:routine-detail", args=[routine.pk]))
        self.client.post(
            reverse("stretching:routine-item-add", args=[routine.pk]),
            {"stretch": self.quad.pk, "hold_seconds": "", "sets": 2, "rest_seconds": 5},
        )
        item = routine.items.get()
        self.assertEqual(item.hold_seconds, self.quad.default_hold_seconds)
        self.assertEqual(item.sets, 2)

    def test_duplicate_name_is_a_form_error_not_a_crash(self):
        StretchRoutine.objects.create(owner=self.alice, name="Evening")
        response = self.client.post(
            reverse("stretching:routine-create"), {"name": "evening", "description": ""}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StretchRoutine.objects.filter(owner=self.alice).count(), 1)

    def test_system_routine_is_read_only_but_copyable(self):
        self.assertEqual(
            self.client.get(
                reverse("stretching:routine-update", args=[self.system_routine.pk])
            ).status_code,
            404,
        )
        response = self.client.post(
            reverse("stretching:routine-copy", args=[self.system_routine.pk])
        )
        copy = StretchRoutine.objects.get(owner=self.alice)
        self.assertRedirects(response, reverse("stretching:routine-detail", args=[copy.pk]))
        self.assertEqual(copy.items.count(), self.system_routine.items.count())

    def test_another_users_routine_is_404(self):
        bobs = StretchRoutine.objects.create(owner=self.bob, name="Bob's")
        item = services.add_routine_item(bobs, self.quad)
        for url in [
            reverse("stretching:routine-detail", args=[bobs.pk]),
            reverse("stretching:routine-update", args=[bobs.pk]),
            reverse("stretching:routine-item-update", args=[bobs.pk, item.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("stretching:session-start", args=[bobs.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("stretching:routine-item-delete", args=[bobs.pk, item.pk])
            ).status_code,
            404,
        )

    def test_move_and_remove_items(self):
        routine = StretchRoutine.objects.create(owner=self.alice, name="R")
        first = services.add_routine_item(routine, self.cat_cow)
        second = services.add_routine_item(routine, self.quad)
        self.client.post(
            reverse("stretching:routine-item-move", args=[routine.pk, second.pk]),
            {"direction": "up"},
        )
        self.assertEqual(list(routine.items.all()), [second, first])
        self.client.post(reverse("stretching:routine-item-delete", args=[routine.pk, first.pk]))
        self.assertEqual(list(routine.items.all()), [second])

    def test_remove_routine_hides_it(self):
        routine = StretchRoutine.objects.create(owner=self.alice, name="R")
        self.client.post(reverse("stretching:routine-deactivate", args=[routine.pk]))
        routine.refresh_from_db()
        self.assertFalse(routine.active)


class StretchLibraryViewTests(StretchingViewTestCase):
    def test_create_custom_stretch(self):
        quads = self.quad.muscle_groups.get(name="Quads")
        response = self.client.post(
            reverse("stretching:stretch-create"),
            {
                "name": "Lizard",
                "kind": "static",
                "per_side": "on",
                "default_hold_seconds": 40,
                "muscle_groups": [quads.pk],
                "instructions": "Low lunge, elbows down.",
            },
        )
        stretch = Stretch.objects.get(owner=self.alice, name="Lizard")
        self.assertRedirects(response, reverse("stretching:stretch-detail", args=[stretch.pk]))
        self.assertTrue(stretch.per_side)
        self.assertEqual(list(stretch.muscle_groups.all()), [quads])

    def test_system_stretch_cannot_be_edited(self):
        self.assertEqual(
            self.client.get(reverse("stretching:stretch-update", args=[self.quad.pk])).status_code,
            404,
        )

    def test_another_users_stretch_is_404(self):
        bobs = Stretch.objects.create(owner=self.bob, name="Secret")
        self.assertEqual(
            self.client.get(reverse("stretching:stretch-detail", args=[bobs.pk])).status_code, 404
        )


class GuidedSessionViewTests(StretchingViewTestCase):
    def test_start_play_mark_and_finish(self):
        response = self.client.post(
            reverse("stretching:session-start", args=[self.system_routine.pk])
        )
        session = StretchSession.objects.get(user=self.alice)
        self.assertRedirects(response, reverse("stretching:session-play", args=[session.pk]))

        play = self.client.get(reverse("stretching:session-play", args=[session.pk]))
        self.assertContains(play, 'id="stretch-timer-steps"')
        self.assertContains(play, "Standing Quad Stretch")

        first, second = session.performed_stretches.all()[:2]
        # The timer's JSON call...
        response = self.client.post(
            reverse("stretching:performed-mark", args=[session.pk, first.pk]),
            data=json.dumps({"action": "done", "actual_seconds": 58}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        # ...and the plain no-JS form.
        response = self.client.post(
            reverse("stretching:performed-mark", args=[session.pk, second.pk]),
            {"action": "skip"},
        )
        self.assertRedirects(response, reverse("stretching:session-play", args=[session.pk]))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, first.actual_seconds), (PerformedStretchStatus.DONE, 58))
        self.assertEqual(second.status, PerformedStretchStatus.SKIPPED)

        response = self.client.post(reverse("stretching:session-complete", args=[session.pk]))
        self.assertRedirects(response, reverse("stretching:session-detail", args=[session.pk]))
        session.refresh_from_db()
        self.assertEqual(session.status, StretchSessionStatus.COMPLETED)
        detail = self.client.get(reverse("stretching:session-detail", args=[session.pk]))
        self.assertContains(detail, "0:58")

    def test_starting_while_one_is_in_progress_returns_to_it(self):
        self.client.post(reverse("stretching:session-start", args=[self.system_routine.pk]))
        existing = StretchSession.objects.get(user=self.alice)
        other = StretchRoutine.objects.get(name="Morning Mobility")
        response = self.client.post(reverse("stretching:session-start", args=[other.pk]))
        self.assertRedirects(response, reverse("stretching:session-play", args=[existing.pk]))
        self.assertEqual(StretchSession.objects.filter(user=self.alice).count(), 1)

    def test_empty_routine_cannot_be_started(self):
        empty = StretchRoutine.objects.create(owner=self.alice, name="Empty")
        self.client.post(reverse("stretching:session-start", args=[empty.pk]))
        self.assertFalse(StretchSession.objects.exists())

    def test_abandon(self):
        self.client.post(reverse("stretching:session-start", args=[self.system_routine.pk]))
        session = StretchSession.objects.get(user=self.alice)
        self.client.post(reverse("stretching:session-abandon", args=[session.pk]))
        session.refresh_from_db()
        self.assertEqual(session.status, StretchSessionStatus.ABANDONED)

    def test_another_users_session_is_404(self):
        bobs = services.start_session(self.bob, routine=self.system_routine)
        performed = bobs.performed_stretches.first()
        self.assertEqual(
            self.client.get(reverse("stretching:session-play", args=[bobs.pk])).status_code, 404
        )
        for url in [
            reverse("stretching:performed-mark", args=[bobs.pk, performed.pk]),
            reverse("stretching:session-complete", args=[bobs.pk]),
            reverse("stretching:session-delete", args=[bobs.pk]),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 404)

    def test_get_is_not_allowed_on_actions(self):
        session = services.start_session(self.alice, routine=self.system_routine)
        self.assertEqual(
            self.client.get(reverse("stretching:session-complete", args=[session.pk])).status_code,
            405,
        )


class CooldownViewTests(StretchingViewTestCase):
    def _completed_workout(self, user, *names):
        workout = workout_services.start_session(user)
        for name in names:
            workout_services.add_performed_exercise(workout, Exercise.objects.get(name=name))
        return workout_services.complete_session(workout)

    def test_starts_the_suggested_routine_linked_to_the_workout(self):
        workout = self._completed_workout(self.alice, "Barbell Back Squat", "Leg Curl")
        self.client.post(reverse("stretching:session-start-cooldown", args=[workout.pk]))
        session = StretchSession.objects.get(user=self.alice)
        self.assertEqual(session.after_workout, workout)
        self.assertEqual(session.routine, self.system_routine)

    def test_cannot_start_a_cooldown_for_someone_elses_workout(self):
        workout = self._completed_workout(self.bob, "Barbell Back Squat")
        response = self.client.post(
            reverse("stretching:session-start-cooldown", args=[workout.pk])
        )
        self.assertEqual(response.status_code, 404)


class QuickLogViewTests(StretchingViewTestCase):
    def test_log_edit_and_delete(self):
        today = timezone.localdate()
        response = self.client.post(
            reverse("stretching:quick-log"),
            {
                "date": today.isoformat(),
                "routine": self.system_routine.pk,
                "duration_minutes": 12,
                "notes": "",
            },
        )
        self.assertRedirects(response, reverse("stretching:session-history"))
        session = StretchSession.objects.get(user=self.alice)
        self.assertEqual(session.duration, timedelta(minutes=12))
        self.assertEqual(session.name, "Lower Body Cool-Down")

        self.client.post(
            reverse("stretching:session-edit", args=[session.pk]),
            {"date": today.isoformat(), "duration_minutes": 20, "notes": "Felt good"},
        )
        session.refresh_from_db()
        self.assertEqual(session.duration, timedelta(minutes=20))
        self.assertEqual(session.routine, self.system_routine)

        self.client.post(reverse("stretching:session-delete", args=[session.pk]))
        self.assertFalse(StretchSession.objects.exists())

    def test_another_users_routine_cannot_be_chosen(self):
        bobs = StretchRoutine.objects.create(owner=self.bob, name="Bob's")
        response = self.client.post(
            reverse("stretching:quick-log"),
            {
                "date": timezone.localdate().isoformat(),
                "routine": bobs.pk,
                "duration_minutes": 5,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StretchSession.objects.exists())


class CooldownCardTests(StretchingViewTestCase):
    """The suggestion card on a finished workout's own page
    (stretching_extras.cooldown_card)."""

    def _completed_workout(self, *names):
        workout = workout_services.start_session(self.alice)
        for name in names:
            workout_services.add_performed_exercise(workout, Exercise.objects.get(name=name))
        return workout_services.complete_session(workout)

    def _page(self, workout):
        return self.client.get(reverse("workouts:session-detail", args=[workout.pk]))

    def test_card_suggests_a_cooldown_after_a_finished_workout(self):
        workout = self._completed_workout("Barbell Back Squat", "Leg Curl")
        response = self._page(workout)
        self.assertContains(response, "Cool down with a stretch?")
        self.assertContains(response, "Lower Body Cool-Down")
        self.assertContains(
            response, reverse("stretching:session-start-cooldown", args=[workout.pk])
        )

    def test_no_card_while_the_workout_is_still_in_progress(self):
        workout = workout_services.start_session(self.alice)
        workout_services.add_performed_exercise(
            workout, Exercise.objects.get(name="Barbell Back Squat")
        )
        self.assertNotContains(self._page(workout), "Cool down with a stretch?")

    def test_no_card_when_stretching_is_turned_off(self):
        self.alice.stretching_enabled = False
        self.alice.save(update_fields=["stretching_enabled"])
        workout = self._completed_workout("Barbell Back Squat")
        self.assertNotContains(self._page(workout), "Cool down with a stretch?")

    def test_card_links_to_the_cooldown_once_done(self):
        workout = self._completed_workout("Barbell Back Squat")
        session = services.start_session(
            self.alice, routine=self.system_routine, after_workout=workout
        )
        services.complete_session(session)
        response = self._page(workout)
        self.assertNotContains(response, "Cool down with a stretch?")
        self.assertContains(response, reverse("stretching:session-detail", args=[session.pk]))
