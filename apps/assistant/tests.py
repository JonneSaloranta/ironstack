import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.exercises.models import Exercise
from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.nutrition.models import DietPlan, Food, MealSlot, Recipe, ServingUnit
from apps.programs.models import Program, Workout
from apps.workouts.models import WorkoutSession, WorkoutSessionStatus

from . import formatting, proposals, services, tools
from .models import (
    AssistantMessage,
    AssistantPreference,
    AssistantProposal,
    AssistantSettings,
    Conversation,
    KeySource,
    MessageRole,
    MessageStatus,
    ProposalStatus,
    SharedKeyAccess,
    UsageRecord,
)
from .providers import (
    AnthropicProvider,
    OllamaProvider,
    ProviderError,
    ToolCall,
    TurnResult,
    _echoable_content,
)

User = get_user_model()

SHARED = {"ASSISTANT_PROVIDER": "anthropic", "ASSISTANT_API_KEY": "sk-ant-shared-key-123456"}


def make_user(username="alice", **kwargs):
    return User.objects.create_user(username=username, password="s3cret-pass", **kwargs)


def make_food(owner, name="Oats", calories=380, **kwargs):
    defaults = {
        "serving_size": Decimal("100"),
        "serving_unit": ServingUnit.GRAM,
        "protein_grams": Decimal("13"),
        "carbohydrate_grams": Decimal("60"),
        "fat_grams": Decimal("7"),
    }
    defaults.update(kwargs)
    return Food.objects.create(owner=owner, name=name, calories=calories, **defaults)


def allow_everyone(limit=0):
    site = AssistantSettings.load()
    site.shared_key_access = SharedKeyAccess.EVERYONE
    site.daily_token_limit = limit
    site.save()
    return site


class FakeProvider(AnthropicProvider):
    """Plays back scripted turns in Anthropic's wire format and records
    every history it was sent."""

    def __init__(self, turns):
        super().__init__(api_key="test", model="claude-opus-5-5")
        self.turns = list(turns)
        self.histories = []

    def run_turn(self, *, system, tools, history, on_text):
        self.histories.append(list(history))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        on_text(turn.text)
        return turn


def text_turn(text, tokens=(100, 20)):
    return TurnResult(
        raw=[{"role": "assistant", "content": [{"type": "text", "text": text}]}],
        text=text,
        stop_reason="end",
        input_tokens=tokens[0],
        output_tokens=tokens[1],
    )


def tool_turn(name, args, call_id="toolu_1"):
    return TurnResult(
        raw=[
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": call_id, "name": name, "input": args}],
            }
        ],
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, input=args)],
        stop_reason="tool_use",
        input_tokens=50,
        output_tokens=10,
    )


@override_settings(**SHARED)
class AccessTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_shared_provider_follows_access_setting(self):
        site = AssistantSettings.load()
        site.shared_key_access = SharedKeyAccess.STAFF
        site.save()
        self.assertFalse(services.access_for(self.user).available)
        self.user.is_staff = True
        self.assertEqual(services.access_for(self.user).key_source, KeySource.SHARED)

    def test_selected_users(self):
        site = AssistantSettings.load()
        site.shared_key_access = SharedKeyAccess.SELECTED
        site.save()
        self.assertFalse(services.access_for(self.user).available)
        site.allowed_users.add(self.user)
        self.assertTrue(services.access_for(self.user).available)

    def test_nobody_without_own_key(self):
        site = AssistantSettings.load()
        site.shared_key_access = SharedKeyAccess.NOBODY
        site.save()
        self.assertFalse(services.access_for(self.user).available)

    def test_own_key_wins_and_needs_no_shared_access(self):
        site = AssistantSettings.load()
        site.shared_key_access = SharedKeyAccess.NOBODY
        site.save()
        services.set_own_key(self.user, "sk-ant-my-own-key-abcdef")
        access = services.access_for(self.user)
        self.assertEqual(access.key_source, KeySource.OWN)
        self.assertEqual(access.provider, "anthropic")

    def test_turned_off_by_admin_blocks_own_key_too(self):
        services.set_own_key(self.user, "sk-ant-my-own-key-abcdef")
        site = AssistantSettings.load()
        site.enabled = False
        site.save()
        self.assertFalse(services.access_for(self.user).available)

    @override_settings(ASSISTANT_API_KEY="")
    def test_no_shared_key_configured(self):
        allow_everyone()
        self.assertFalse(services.access_for(self.user).available)

    @override_settings(ASSISTANT_PROVIDER="ollama", ASSISTANT_API_KEY="")
    def test_ollama_needs_no_key(self):
        allow_everyone()
        access = services.access_for(self.user)
        self.assertEqual(access.provider, "ollama")

    def test_is_available_needs_consent(self):
        allow_everyone()
        self.assertFalse(services.is_available(self.user))
        services.enable(self.user)
        self.assertTrue(services.is_available(self.user))


class ApiKeyStorageTests(TestCase):
    def test_key_is_encrypted_at_rest_and_never_hinted_in_full(self):
        user = make_user()
        services.set_own_key(user, "sk-ant-secret-value-9876")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_key FROM assistant_assistantpreference WHERE user_id = %s", [user.pk]
            )
            stored = cursor.fetchone()[0]
        self.assertNotIn("secret-value", stored)
        preference = AssistantPreference.objects.get(user=user)
        self.assertEqual(preference.api_key, "sk-ant-secret-value-9876")
        self.assertEqual(preference.api_key_hint, "…9876")

    def test_remove_key(self):
        user = make_user()
        services.set_own_key(user, "sk-ant-secret-value-9876")
        services.remove_own_key(user)
        self.assertFalse(AssistantPreference.objects.get(user=user).has_own_key)


@override_settings(**SHARED)
class ConversationTests(TestCase):
    def setUp(self):
        self.user = make_user(first_name="Alice")
        allow_everyone()
        services.enable(self.user)

    def test_requires_consent(self):
        services.disable(self.user)
        with self.assertRaises(services.AssistantError):
            services.start_conversation(self.user, "Hi")

    def test_start_queues_reply_with_context_in_first_message(self):
        conversation = services.start_conversation(self.user, "Make me a program")
        user_message, reply = conversation.messages.all()
        self.assertEqual(user_message.role, MessageRole.USER)
        self.assertEqual(user_message.text, "Make me a program")
        sent = user_message.raw[0]["content"][0]["text"]
        self.assertIn("<conversation_context>", sent)
        self.assertIn("Alice", sent)
        self.assertTrue(sent.endswith("Make me a program"))
        self.assertEqual(reply.status, MessageStatus.PENDING)
        self.assertEqual(conversation.key_source, KeySource.SHARED)

    def test_one_reply_at_a_time(self):
        conversation = services.start_conversation(self.user, "First")
        with self.assertRaises(services.AssistantError):
            services.send_message(conversation, "Second")
        with self.assertRaises(services.AssistantError):
            services.start_conversation(self.user, "Another")

    def test_daily_limit_on_shared_provider(self):
        allow_everyone(limit=1000)
        UsageRecord.objects.create(
            user=self.user,
            date=timezone.localdate(),
            key_source=KeySource.SHARED,
            input_tokens=900,
            output_tokens=100,
        )
        with self.assertRaises(services.AssistantError):
            services.start_conversation(self.user, "Hi")

    def test_daily_limit_does_not_apply_to_own_key(self):
        allow_everyone(limit=1000)
        services.set_own_key(self.user, "sk-ant-my-own-key-abcdef")
        UsageRecord.objects.create(
            user=self.user,
            date=timezone.localdate(),
            key_source=KeySource.SHARED,
            input_tokens=5000,
        )
        conversation = services.start_conversation(self.user, "Hi")
        self.assertEqual(conversation.key_source, KeySource.OWN)

    def test_empty_and_too_long_messages(self):
        with self.assertRaises(services.AssistantError):
            services.start_conversation(self.user, "   ")
        with self.assertRaises(services.AssistantError):
            services.start_conversation(self.user, "x" * (services.MAX_MESSAGE_LENGTH + 1))

    def test_removed_own_key_ends_that_conversation(self):
        services.set_own_key(self.user, "sk-ant-my-own-key-abcdef")
        conversation = services.start_conversation(self.user, "Hi")
        AssistantMessage.objects.filter(conversation=conversation).update(status=MessageStatus.DONE)
        services.remove_own_key(self.user)
        with self.assertRaises(services.AssistantError):
            services.send_message(conversation, "Again")


@override_settings(**SHARED)
class ProcessMessageTests(TestCase):
    def setUp(self):
        self.user = make_user()
        allow_everyone()
        services.enable(self.user)
        self.conversation = services.start_conversation(self.user, "How am I doing?")
        self.reply = self.conversation.messages.get(role=MessageRole.ASSISTANT)

    def run_with(self, *turns):
        provider = FakeProvider(turns)
        with mock.patch.object(services, "_provider_for", return_value=provider):
            services.process_message(self.reply.pk)
        self.reply.refresh_from_db()
        return provider

    def test_text_reply(self):
        self.run_with(text_turn("You're doing great."))
        self.assertEqual(self.reply.status, MessageStatus.DONE)
        self.assertEqual(self.reply.text, "You're doing great.")
        self.assertEqual(len(self.reply.raw), 1)
        self.assertEqual((self.reply.input_tokens, self.reply.output_tokens), (100, 20))
        usage = UsageRecord.objects.get(user=self.user, key_source=KeySource.SHARED)
        self.assertEqual((usage.requests, usage.input_tokens, usage.output_tokens), (1, 100, 20))

    def test_tool_loop_reads_own_data(self):
        weight_type = MeasurementType.objects.get(name="Body weight", owner__isnull=True)
        BodyMeasurement.objects.create(
            user=self.user, measurement_type=weight_type, value=Decimal("82.5")
        )
        provider = self.run_with(tool_turn("get_user_overview", {}), text_turn("Done."))
        self.assertEqual(self.reply.status, MessageStatus.DONE)
        self.assertEqual(len(self.reply.raw), 3)
        tool_result = self.reply.raw[1]["content"][0]
        self.assertEqual(tool_result["type"], "tool_result")
        self.assertEqual(json.loads(tool_result["content"])["latest_body_weight_kg"], 82.5)
        # The second request carries the first turn and its tool result.
        self.assertEqual(len(provider.histories[1]), len(provider.histories[0]) + 2)

    def test_bad_tool_input_goes_back_as_error(self):
        self.run_with(tool_turn("get_program", {"program_id": "nope"}), text_turn("Sorry."))
        tool_result = self.reply.raw[1]["content"][0]
        self.assertTrue(tool_result["is_error"])

    def test_history_is_replayed_in_order(self):
        self.run_with(text_turn("First answer."))
        reply = services.send_message(self.conversation, "Follow-up")
        provider = FakeProvider([text_turn("Second answer.")])
        with mock.patch.object(services, "_provider_for", return_value=provider):
            services.process_message(reply.pk)
        history = provider.histories[0]
        self.assertEqual([m["role"] for m in history], ["user", "assistant", "user"])
        self.assertEqual(history[1]["content"][0]["text"], "First answer.")
        self.assertEqual(history[2]["content"][0]["text"], "Follow-up")

    def test_provider_error_fails_cleanly(self):
        self.run_with(ProviderError("The API key was rejected."))
        self.assertEqual(self.reply.status, MessageStatus.ERROR)
        self.assertEqual(self.reply.error, "The API key was rejected.")
        self.assertEqual(self.reply.raw, [])
        # The user can send again.
        services.send_message(self.conversation, "Retry")

    def test_unexpected_error_fails_cleanly(self):
        self.run_with(RuntimeError("boom"))
        self.assertEqual(self.reply.status, MessageStatus.ERROR)
        self.assertNotIn("boom", self.reply.error)

    def test_refusal(self):
        self.run_with(TurnResult(raw=[], text="", stop_reason="refusal"))
        self.assertEqual(self.reply.status, MessageStatus.DONE)
        self.assertEqual(self.reply.raw, [])
        self.assertTrue(self.reply.text)

    def test_cut_off_tool_call_is_left_out_of_history(self):
        turn = tool_turn("get_user_overview", {})
        turn.stop_reason = "max_tokens"
        self.run_with(turn)
        self.assertEqual(self.reply.status, MessageStatus.DONE)
        self.assertEqual(self.reply.raw, [])

    def test_tool_rounds_are_bounded(self):
        turns = [
            tool_turn("list_meal_slots", {}, call_id=f"t{i}")
            for i in range(services.MAX_TOOL_ROUNDS)
        ]
        self.run_with(*turns)
        self.assertEqual(self.reply.status, MessageStatus.DONE)
        # Ends on a tool-result message, which is still a valid history.
        self.assertEqual(self.reply.raw[-1]["role"], "user")

    def test_only_claimed_once(self):
        self.run_with(text_turn("Once."))
        provider = FakeProvider([])
        with mock.patch.object(services, "_provider_for", return_value=provider):
            services.process_message(self.reply.pk)
        self.assertEqual(provider.histories, [])

    def test_stale_running_reply_is_failed(self):
        AssistantMessage.objects.filter(pk=self.reply.pk).update(
            status=MessageStatus.RUNNING, updated_at=timezone.now() - timedelta(hours=1)
        )
        self.assertEqual(services.fail_stale_messages(), 1)
        self.reply.refresh_from_db()
        self.assertEqual(self.reply.status, MessageStatus.ERROR)

    def test_pending_ids(self):
        self.assertEqual(services.pending_message_ids(), [self.reply.pk])


class ToolScopingTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        self.ctx = tools.ToolContext(self.user, None)

    def run_tool(self, name, args):
        content, is_error = tools.run(self.ctx, name, args)
        return json.loads(content) if not is_error else content, is_error

    def test_search_foods_sees_own_and_shared_only(self):
        make_food(self.user, "Zqx mine")
        make_food(None, "Zqx shared")
        make_food(self.other, "Zqx bob")
        data, is_error = self.run_tool("search_foods", {"query": "zqx"})
        self.assertFalse(is_error)
        self.assertEqual({f["name"] for f in data["foods"]}, {"Zqx mine", "Zqx shared"})

    def test_other_users_diet_plan_is_invisible(self):
        plan = DietPlan.objects.create(
            user=self.other,
            name="Bob's",
            target_calories=2000,
            target_protein_grams=150,
            target_carbohydrate_grams=200,
            target_fat_grams=60,
        )
        _content, is_error = self.run_tool("get_diet_plan", {"diet_plan_id": plan.pk})
        self.assertTrue(is_error)
        data, _ = self.run_tool("list_diet_plans", {})
        self.assertEqual(data["diet_plans"], [])

    def test_other_users_program_is_invisible(self):
        program = Program.objects.create(owner=self.other, name="Bob's program")
        _content, is_error = self.run_tool("get_program", {"program_id": program.pk})
        self.assertTrue(is_error)

    def test_workout_history_is_own_only(self):
        for owner in (self.user, self.other):
            WorkoutSession.objects.create(
                user=owner, status=WorkoutSessionStatus.COMPLETED, ended_at=timezone.now()
            )
        data, _ = self.run_tool("get_workout_history", {"days": 30})
        self.assertEqual(len(data["completed_sessions"]), 1)

    def test_unknown_tool_and_bad_input(self):
        self.assertTrue(tools.run(self.ctx, "drop_tables", {})[1])
        self.assertTrue(tools.run(self.ctx, "get_workout_history", {"days": "lots"})[1])
        self.assertTrue(tools.run(self.ctx, "get_workout_history", "[1, 2]")[1])

    def test_json_string_arguments_are_accepted(self):
        _content, is_error = tools.run(self.ctx, "search_foods", '{"query": "x"}')
        self.assertFalse(is_error)

    def test_definitions_are_stable(self):
        self.assertEqual(tools.definitions(), tools.definitions())
        self.assertNotIn("handler", tools.definitions()[0])


class DietPlanProposalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        self.oats = make_food(self.user)
        self.slot = MealSlot.objects.filter(owner__isnull=True).first()

    def proposal(self, items, **overrides):
        data = {
            "name": "Cut",
            "summary": "Because.",
            "target_calories": 2000,
            "target_protein_grams": 150,
            "target_carbohydrate_grams": 200,
            "target_fat_grams": 60,
            "meals": [{"meal_slot": self.slot.name, "items": items}],
        }
        data.update(overrides)
        return data

    def test_validates_and_computes_calories(self):
        payload = proposals.validate_diet_plan(
            self.user, self.proposal([{"food_id": self.oats.pk, "quantity": 50}])
        )
        self.assertEqual(payload["computed_daily_calories"], 190)
        self.assertEqual(payload["meals"][0]["items"][0]["label"], "Oats")
        # The stored payload is accepted again unchanged.
        self.assertEqual(proposals.validate_diet_plan(self.user, payload), payload)

    def test_rejects_other_users_food(self):
        bobs = make_food(self.other, "Bob food")
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_diet_plan(
                self.user, self.proposal([{"food_id": bobs.pk, "quantity": 50}])
            )

    def test_rejects_unknown_meal_slot(self):
        data = self.proposal([{"food_id": self.oats.pk, "quantity": 50}])
        data["meals"][0]["meal_slot"] = "Midnight feast"
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_diet_plan(self.user, data)

    def test_rejects_new_food_whose_macros_do_not_add_up(self):
        new_food = {
            "name": "Mystery bar",
            "serving_size": 100,
            "serving_unit": "g",
            "calories": 900,
            "protein_grams": 5,
            "carbohydrate_grams": 5,
            "fat_grams": 1,
        }
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_diet_plan(
                self.user, self.proposal([{"new_food": new_food, "quantity": 100}])
            )

    def test_weekly_plan_needs_weekdays(self):
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_diet_plan(
                self.user,
                self.proposal([{"food_id": self.oats.pk, "quantity": 50}], is_weekly=True),
            )

    def test_accept_creates_inactive_plan_and_new_food(self):
        recipe = Recipe.objects.create(owner=self.user, name="Porridge", servings=1)
        new_food = {
            "name": "Skyr",
            "serving_size": 100,
            "serving_unit": "g",
            "calories": 62,
            "protein_grams": 11,
            "carbohydrate_grams": 4,
            "fat_grams": 0.2,
        }
        payload = proposals.validate_diet_plan(
            self.user,
            self.proposal(
                [
                    {"food_id": self.oats.pk, "quantity": 80},
                    {"recipe_id": recipe.pk, "quantity": 1},
                    {"new_food": new_food, "quantity": 200},
                ]
            ),
        )
        proposal = self.make_proposal(payload, proposals.DIET_PLAN)
        plan = proposals.accept(proposal)
        self.assertFalse(plan.is_active)
        self.assertEqual(plan.user, self.user)
        items = list(plan.meals.get().items.all())
        self.assertEqual(len(items), 3)
        skyr = Food.objects.get(name="Skyr")
        self.assertEqual(skyr.owner, self.user)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ProposalStatus.ACCEPTED)
        self.assertEqual(proposal.created_object_id, plan.pk)
        with self.assertRaises(proposals.ProposalError):
            proposals.accept(proposal)

    def test_accept_revalidates(self):
        payload = proposals.validate_diet_plan(
            self.user, self.proposal([{"food_id": self.oats.pk, "quantity": 50}])
        )
        proposal = self.make_proposal(payload, proposals.DIET_PLAN)
        self.oats.delete()
        with self.assertRaises(proposals.ProposalError):
            proposals.accept(proposal)
        self.assertEqual(DietPlan.objects.count(), 0)

    def make_proposal(self, payload, kind):
        conversation = Conversation.objects.create(
            user=self.user, title="t", provider="anthropic", model="m", key_source="shared"
        )
        message = AssistantMessage.objects.create(
            conversation=conversation, role=MessageRole.ASSISTANT
        )
        return proposals.record(message, kind, payload)


class ProgramProposalTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.exercise = Exercise.objects.filter(owner__isnull=True).first()

    def proposal(self, **prescription):
        item = {"exercise_id": self.exercise.pk, "set_count": 3, "min_reps": 8, "max_reps": 12}
        item.update(prescription)
        return {
            "name": "Full body",
            "summary": "Three days.",
            "workouts": [{"name": "Day A", "scheduled_weekday": 0, "prescriptions": [item]}],
        }

    def test_accept_creates_program(self):
        payload = proposals.validate_program(self.user, self.proposal(target_weight_kg=60))
        conversation = Conversation.objects.create(
            user=self.user, title="t", provider="anthropic", model="m", key_source="shared"
        )
        message = AssistantMessage.objects.create(
            conversation=conversation, role=MessageRole.ASSISTANT
        )
        program = proposals.accept(proposals.record(message, proposals.PROGRAM, payload))
        self.assertEqual(program.owner, self.user)
        workout = Workout.objects.get(program=program)
        self.assertEqual(workout.scheduled_weekday, 0)
        prescription = workout.prescriptions.get()
        self.assertEqual(prescription.exercise, self.exercise)
        self.assertEqual(prescription.target_weight, Decimal("60"))
        self.assertEqual(prescription.progression_method, "double_progression")

    def test_rejects_invisible_exercise(self):
        bobs = Exercise.objects.create(owner=make_user("bob"), name="Bob's lift")
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_program(self.user, self.proposal(exercise_id=bobs.pk))

    def test_rejects_inverted_rep_range(self):
        with self.assertRaises(proposals.ProposalError):
            proposals.validate_program(self.user, self.proposal(min_reps=12, max_reps=8))


@override_settings(**SHARED, ASSISTANT_RUN_INLINE=True)
class ViewTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other = make_user("bob")
        allow_everyone()
        self.client.force_login(self.user)

    def start(self, text="Hello", turns=None):
        services.enable(self.user)
        provider = FakeProvider(turns or [text_turn("Hi there!")])
        with mock.patch.object(services, "_provider_for", return_value=provider):
            response = self.client.post(reverse("assistant:conversation-create"), {"text": text})
        return response, Conversation.objects.filter(user=self.user).first()

    def test_home_offers_to_turn_on(self):
        response = self.client.get(reverse("assistant:home"))
        self.assertContains(response, reverse("assistant:enable"))
        self.client.post(reverse("assistant:enable"))
        self.assertTrue(services.preference_for(self.user).enabled)
        response = self.client.get(reverse("assistant:home"))
        self.assertContains(response, 'data-tour="assistant-new"')

    def test_home_prefills_prompt(self):
        services.enable(self.user)
        response = self.client.get(reverse("assistant:home") + "?prompt=Build+me+a+plan")
        self.assertContains(response, "Build me a plan")

    def test_create_writes_reply_inline(self):
        response, conversation = self.start()
        self.assertRedirects(
            response, reverse("assistant:conversation-detail", args=[conversation.pk])
        )
        response = self.client.get(response.url)
        self.assertContains(response, "Hi there!")

    def test_send_via_htmx_returns_thread(self):
        _response, conversation = self.start()
        provider = FakeProvider([text_turn("**Second** reply")])
        with mock.patch.object(services, "_provider_for", return_value=provider):
            response = self.client.post(
                reverse("assistant:message-send", args=[conversation.pk]),
                {"text": "More"},
                HTTP_HX_REQUEST="true",
            )
        self.assertContains(response, "<strong>Second</strong> reply")

    def test_pending_message_polls_itself(self):
        services.enable(self.user)
        with override_settings(ASSISTANT_RUN_INLINE=False):
            conversation = services.start_conversation(self.user, "Hi")
        reply = conversation.messages.get(role=MessageRole.ASSISTANT)
        response = self.client.get(reverse("assistant:message-fragment", args=[reply.pk]))
        self.assertContains(response, 'hx-trigger="every 1s')

    def test_other_users_conversation_is_hidden(self):
        _response, conversation = self.start()
        reply = conversation.messages.get(role=MessageRole.ASSISTANT)
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.get(
                reverse("assistant:conversation-detail", args=[conversation.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("assistant:message-fragment", args=[reply.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("assistant:conversation-delete", args=[conversation.pk])
            ).status_code,
            404,
        )

    def test_proposal_accept_and_ownership(self):
        exercise = Exercise.objects.filter(owner__isnull=True).first()
        args = {
            "name": "Plan",
            "summary": "s",
            "workouts": [
                {
                    "name": "A",
                    "prescriptions": [
                        {"exercise_id": exercise.pk, "set_count": 3, "min_reps": 5, "max_reps": 5}
                    ],
                }
            ],
        }
        _response, conversation = self.start(
            turns=[tool_turn("propose_program", args), text_turn("See the card below.")]
        )
        proposal = AssistantProposal.objects.get()
        detail = self.client.get(reverse("assistant:conversation-detail", args=[conversation.pk]))
        self.assertContains(detail, reverse("assistant:proposal-accept", args=[proposal.pk]))

        self.client.force_login(self.other)
        response = self.client.post(reverse("assistant:proposal-accept", args=[proposal.pk]))
        self.assertEqual(response.status_code, 404)

        self.client.force_login(self.user)
        response = self.client.post(reverse("assistant:proposal-accept", args=[proposal.pk]))
        program = Program.objects.get(owner=self.user, name="Plan")
        self.assertRedirects(
            response,
            reverse("programs:program-detail", args=[program.pk]),
            fetch_redirect_response=False,
        )

    def test_proposal_dismiss(self):
        payload = {"name": "P", "summary": "", "workouts": []}
        conversation = Conversation.objects.create(
            user=self.user, title="t", provider="anthropic", model="m", key_source="shared"
        )
        message = AssistantMessage.objects.create(
            conversation=conversation, role=MessageRole.ASSISTANT
        )
        proposal = proposals.record(message, proposals.PROGRAM, payload)
        self.client.post(reverse("assistant:proposal-dismiss", args=[proposal.pk]))
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, ProposalStatus.DISMISSED)

    def test_delete_conversation(self):
        _response, conversation = self.start()
        self.client.post(reverse("assistant:conversation-delete", args=[conversation.pk]))
        self.assertFalse(Conversation.objects.exists())

    def test_key_save_validates_and_never_renders_key(self):
        response = self.client.post(reverse("assistant:key-save"), {"api_key": "not-a-key"})
        self.assertContains(response, "doesn")
        self.client.post(reverse("assistant:key-save"), {"api_key": "sk-ant-valid-key-0000"})
        response = self.client.get(reverse("assistant:settings"))
        self.assertContains(response, "…0000")
        self.assertNotContains(response, "sk-ant-valid-key-0000")
        self.client.post(reverse("assistant:key-remove"))
        self.assertFalse(services.preference_for(self.user).has_own_key)

    def test_admin_settings_is_staff_only(self):
        url = reverse("assistant:admin-settings")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url, {"enabled": "on", "shared_key_access": "nobody", "daily_token_limit": 5000}
        )
        self.assertRedirects(response, url)
        self.assertEqual(AssistantSettings.load().daily_token_limit, 5000)

    def test_ask_link_only_for_users_who_can_use_it(self):
        response = self.client.get(reverse("nutrition:diet-plan-list"))
        self.assertNotContains(response, reverse("assistant:home") + "?prompt=")
        services.enable(self.user)
        response = self.client.get(reverse("nutrition:diet-plan-list"))
        self.assertContains(response, reverse("assistant:home") + "?prompt=")


class FormattingTests(TestCase):
    def test_escapes_and_renders_subset(self):
        html = formatting.render_reply(
            "# Plan\n**Bold** <script>x</script>\n\n- one\n- two\n\n1. first\n[link](http://evil)"
        )
        self.assertIn("<p><strong>Plan</strong></p>", html)
        self.assertIn("<strong>Bold</strong> &lt;script&gt;", html)
        self.assertIn("<ul><li>one</li><li>two</li></ul>", html)
        self.assertIn("<ol><li>first</li></ol>", html)
        self.assertNotIn("<a ", html)


class _FakeStream:
    def __init__(self, events, message):
        self.events = events
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self.events)

    def get_final_message(self):
        return self.message


class AnthropicProviderTests(TestCase):
    def make_message(self, content, stop_reason="end_turn"):
        from anthropic.types import Message, Usage

        return Message.model_validate(
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": content,
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": Usage(
                    input_tokens=10, output_tokens=5, cache_read_input_tokens=100
                ).model_dump(),
            }
        )

    def run_provider(self, message, model="claude-opus-5-5"):
        client = mock.MagicMock()
        events = [mock.Mock(type="text", text="Hel"), mock.Mock(type="text", text="lo")]
        client.beta.messages.stream.return_value = _FakeStream(events, message)
        client.messages.stream.return_value = _FakeStream(events, message)
        seen = []
        with mock.patch("anthropic.Anthropic", return_value=client):
            provider = AnthropicProvider(api_key="k", model=model, effort="low")
            result = provider.run_turn(
                system="sys",
                tools=tools.definitions(),
                history=[provider.user_message("hi")],
                on_text=seen.append,
            )
        return client, result, seen

    def test_streams_text_and_sends_expected_request(self):
        client, result, seen = self.run_provider(
            self.make_message([{"type": "text", "text": "Hello"}])
        )
        self.assertEqual(seen, ["Hel", "Hello"])
        self.assertEqual(result.text, "Hello")
        self.assertEqual(result.stop_reason, "end")
        self.assertEqual(result.input_tokens, 110)
        kwargs = client.beta.messages.stream.call_args.kwargs
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertTrue(all(tool["eager_input_streaming"] for tool in kwargs["tools"]))

    def test_refusal_fallback_can_be_turned_off(self):
        client = mock.MagicMock()
        message = self.make_message([{"type": "text", "text": "x"}])
        client.messages.stream.return_value = _FakeStream([], message)
        with mock.patch("anthropic.Anthropic", return_value=client):
            AnthropicProvider(
                api_key="k", model="claude-opus-5-5", refusal_fallback=False
            ).run_turn(system="s", tools=[], history=[], on_text=lambda t: None)
        client.beta.messages.stream.assert_not_called()
        self.assertNotIn("fallbacks", client.messages.stream.call_args.kwargs)

    def test_models_without_fallback_use_plain_stream(self):
        client, _result, _seen = self.run_provider(
            self.make_message([{"type": "text", "text": "x"}]), model="claude-haiku-4-5"
        )
        kwargs = client.messages.stream.call_args.kwargs
        self.assertNotIn("output_config", kwargs)
        client.beta.messages.stream.assert_not_called()

    def test_tool_use(self):
        _client, result, _seen = self.run_provider(
            self.make_message(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "tool_use", "id": "t1", "name": "list_meal_slots", "input": {}},
                ],
                stop_reason="tool_use",
            )
        )
        self.assertEqual(result.stop_reason, "tool_use")
        self.assertEqual(result.tool_calls[0].name, "list_meal_slots")
        # Thinking blocks are kept, unchanged, for the next request.
        self.assertEqual(result.raw[0]["content"][0]["signature"], "sig")
        results = AnthropicProvider(api_key="", model="m").tool_results_message(
            [mock.Mock(call=result.tool_calls[0], content="{}", is_error=False)]
        )
        self.assertEqual(results[0]["content"][0]["tool_use_id"], "t1")

    def test_refusal_discards_output(self):
        _client, result, _seen = self.run_provider(
            self.make_message([{"type": "text", "text": "partial"}], stop_reason="refusal")
        )
        self.assertEqual(result.stop_reason, "refusal")
        self.assertEqual(result.raw, [])

    def test_errors_become_user_facing(self):
        import anthropic
        import httpx2

        error = anthropic.AuthenticationError(
            "bad",
            response=httpx2.Response(401, request=httpx2.Request("POST", "https://x")),
            body=None,
        )
        client = mock.MagicMock()
        client.beta.messages.stream.side_effect = error
        with mock.patch("anthropic.Anthropic", return_value=client):
            with self.assertRaises(ProviderError):
                AnthropicProvider(api_key="k", model="claude-opus-5-5").run_turn(
                    system="s", tools=[], history=[], on_text=lambda t: None
                )

    def test_echoable_content_drops_declined_models_internal_blocks(self):
        blocks = [
            mock.Mock(**{"to_dict.return_value": {"type": "thinking", "thinking": ""}}),
            mock.Mock(**{"to_dict.return_value": {"type": "text", "text": "Part"}}),
            mock.Mock(**{"to_dict.return_value": {"type": "fallback"}}),
            mock.Mock(**{"to_dict.return_value": {"type": "text", "text": "rest"}}),
        ]
        self.assertEqual(
            [b["type"] for b in _echoable_content(blocks)],
            ["text", "text"],
        )


class OllamaProviderTests(TestCase):
    def test_parses_streamed_tool_call_and_text(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": "Let me check"}}),
            json.dumps(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"function": {"name": "search_foods", "arguments": {"query": "oat"}}}
                        ],
                    }
                }
            ),
            json.dumps({"done": True, "prompt_eval_count": 30, "eval_count": 7}),
        ]
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_lines.return_value = [line.encode() for line in lines]
        with mock.patch("apps.assistant.providers.requests.post", return_value=response) as post:
            provider = OllamaProvider(base_url="http://ollama:11434/", model="qwen3:8b")
            result = provider.run_turn(
                system="sys", tools=tools.definitions(), history=[], on_text=lambda t: None
            )
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(body["tools"][0]["type"], "function")
        self.assertEqual(result.stop_reason, "tool_use")
        self.assertEqual(result.tool_calls[0].input, {"query": "oat"})
        self.assertEqual((result.input_tokens, result.output_tokens), (30, 7))
        self.assertEqual(
            provider.tool_results_message([mock.Mock(call=result.tool_calls[0], content="{}")]),
            [{"role": "tool", "tool_name": "search_foods", "content": "{}"}],
        )
