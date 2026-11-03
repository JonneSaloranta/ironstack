"""Personal-trainer coaching (apps.coaching) through the API - issue #37.

The same two sides as the web: a client asks a trainer to coach them,
imports the programs and diet plans their coach shares and pulls the
coach's later updates; a trainer answers requests, shares plans with
active clients and sees each client's training. Every rule stays in
apps.coaching.services; a CoachingError answers 400 with its message in
the user's language. Weights are canonical kg like the rest of the API.
Coaching context."""

from functools import wraps

from django.contrib.auth import get_user_model
from django.db import models
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics import achievements as achievement_services
from apps.analytics import dateranges
from apps.analytics import services as analytics_services
from apps.coaching import services as coaching_services
from apps.coaching.forms import active_clients_of
from apps.coaching.models import CoachingRelationship, CoachingRequest, CoachingRequestStatus
from apps.measurements import services as measurement_services
from apps.nutrition.models import DietPlan
from apps.programs.models import Program
from apps.workouts.models import WorkoutSession

from .models import ApiContext
from .serializers import (
    AchievementSerializer,
    DietPlanSerializer,
    PersonalRecordSerializer,
    ProgramSerializer,
    TrainingSummarySerializer,
)
from .views import _user_language

User = get_user_model()


# --------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------


class CoachingUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    display_name = serializers.CharField(source="public_display_name")


class CoachingRequestSerializer(serializers.ModelSerializer):
    coach = CoachingUserSerializer()
    coachee = CoachingUserSerializer()

    class Meta:
        model = CoachingRequest
        fields = ["id", "coach", "coachee", "status", "created_at", "responded_at"]


class CoachingRelationshipSerializer(serializers.ModelSerializer):
    coach = CoachingUserSerializer()
    coachee = CoachingUserSerializer()

    class Meta:
        model = CoachingRelationship
        fields = ["id", "coach", "coachee", "created_at", "ended_at"]


class CoachingRequestCreateSerializer(serializers.Serializer):
    username = serializers.CharField(help_text="The trainer's username.")


class SharedPlansSerializer(serializers.Serializer):
    programs = serializers.ListField(child=serializers.DictField())
    diet_plans = serializers.ListField(child=serializers.DictField())


class CoachUpdateSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    diff = serializers.CharField(
        allow_null=True, help_text="Unified diff of your copy against the coach's version."
    )


class ClientIdsSerializer(serializers.Serializer):
    clients = serializers.ListField(child=serializers.IntegerField())


class ClientMeasurementSerializer(serializers.Serializer):
    measurement_type = serializers.IntegerField(source="type.pk")
    name = serializers.CharField(source="type.name")
    unit_kind = serializers.CharField(source="type.unit_kind")
    latest_value = serializers.DecimalField(max_digits=10, decimal_places=4, allow_null=True)
    latest_recorded_at = serializers.DateTimeField(allow_null=True)


class AssignedProgramSerializer(serializers.Serializer):
    program = serializers.IntegerField()
    name = serializers.CharField()
    client_copy = serializers.IntegerField()
    last_session_at = serializers.DateTimeField(allow_null=True)


class ClientDetailSerializer(serializers.Serializer):
    client = CoachingUserSerializer()
    relationship = CoachingRelationshipSerializer()
    training_summary = TrainingSummarySerializer()
    highlights = AchievementSerializer(many=True)
    recent_records = PersonalRecordSerializer(many=True)
    measurements = ClientMeasurementSerializer(many=True)
    assigned_programs = AssignedProgramSerializer(many=True)


# --------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------


def coaching_errors(method):
    """Runs a view method in the user's language and answers a
    CoachingError with a 400 carrying its message."""

    @wraps(method)
    def wrapper(self, request, *args, **kwargs):
        with _user_language(request.user):
            try:
                return method(self, request, *args, **kwargs)
            except coaching_services.CoachingError as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

    return wrapper


class CoachingView(APIView):
    api_context = ApiContext.COACHING


def _trainer_only(user):
    if not user.is_personal_trainer:
        return Response(
            {"detail": "Only a personal trainer can do this."}, status=status.HTTP_403_FORBIDDEN
        )
    return None


# --------------------------------------------------------------------
# Requests and relationships
# --------------------------------------------------------------------


class CoachingRequestListView(CoachingView):
    """`GET coaching/requests/` - pending requests the user sent (as a
    client) or received (as a trainer). `POST {username}` asks a trainer
    to coach you."""

    @extend_schema(responses=CoachingRequestSerializer(many=True))
    def get(self, request):
        pending = CoachingRequest.objects.filter(status=CoachingRequestStatus.PENDING).filter(
            models.Q(coach=request.user) | models.Q(coachee=request.user)
        )
        return Response(
            CoachingRequestSerializer(pending.select_related("coach", "coachee"), many=True).data
        )

    @extend_schema(
        request=CoachingRequestCreateSerializer, responses={201: CoachingRequestSerializer}
    )
    @coaching_errors
    def post(self, request):
        serializer = CoachingRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coach = get_object_or_404(User, username=serializer.validated_data["username"])
        coaching_request = coaching_services.send_coaching_request(request.user, coach)
        coaching_request.refresh_from_db()
        return Response(
            CoachingRequestSerializer(coaching_request).data, status=status.HTTP_201_CREATED
        )


class CoachingRequestRespondView(CoachingView):
    """`POST coaching/requests/<id>/accept/` or `.../decline/` - a
    trainer answering a request to them."""

    accept = True

    @extend_schema(request=None, responses=CoachingRequestSerializer)
    @coaching_errors
    def post(self, request, pk):
        coaching_request = get_object_or_404(CoachingRequest, pk=pk, coach=request.user)
        if self.accept:
            coaching_services.accept_coaching_request(coaching_request, acting_user=request.user)
        else:
            coaching_services.decline_coaching_request(coaching_request, acting_user=request.user)
        return Response(CoachingRequestSerializer(coaching_request).data)


class CoachListView(CoachingView):
    """`GET coaching/coaches/` - the user's active coaches."""

    @extend_schema(responses=CoachingRelationshipSerializer(many=True))
    def get(self, request):
        relationships = coaching_services.coaches_of(request.user).select_related("coachee")
        return Response(CoachingRelationshipSerializer(relationships, many=True).data)


class ClientListView(CoachingView):
    """`GET coaching/clients/` - a trainer's active clients."""

    @extend_schema(responses=CoachingRelationshipSerializer(many=True))
    def get(self, request):
        relationships = coaching_services.clients_of(request.user).select_related("coach")
        return Response(CoachingRelationshipSerializer(relationships, many=True).data)


class RelationshipEndView(CoachingView):
    """`POST coaching/relationships/<id>/end/` - either side ends it; the
    history of having been coached stays."""

    @extend_schema(request=None, responses=CoachingRelationshipSerializer)
    @coaching_errors
    def post(self, request, pk):
        relationship = get_object_or_404(
            CoachingRelationship.objects.filter(
                models.Q(coach=request.user) | models.Q(coachee=request.user)
            ),
            pk=pk,
        )
        coaching_services.end_coaching_relationship(relationship, acting_user=request.user)
        return Response(CoachingRelationshipSerializer(relationship).data)


class ClientDetailView(CoachingView):
    """`GET coaching/clients/<username>/` - a trainer's view of one active
    client, as on the web: training in the last 30 days, highlights, the
    latest personal records, the latest reading of each body measurement
    and the shared programs they've imported. 404 unless the client is
    actively coached by the user - accepting the request is the client's
    consent to this."""

    @extend_schema(responses=ClientDetailSerializer)
    def get(self, request, username):
        member = get_object_or_404(User, username=username)
        relationship = coaching_services.get_active_relationship(
            coach=request.user, coachee=member
        )
        if relationship is None:
            raise Http404
        measurements = []
        for measurement_type in measurement_services.visible_to(member):
            latest = measurement_services.latest_for(member, measurement_type)
            measurements.append(
                {
                    "type": measurement_type,
                    "latest_value": latest.value if latest else None,
                    "latest_recorded_at": latest.recorded_at if latest else None,
                }
            )
        assigned = []
        for program in Program.objects.filter(owner=request.user, shared_with_clients=member):
            copy = program.imported_copies.filter(owner=member).first()
            if copy is None:
                continue
            last_session = (
                WorkoutSession.objects.filter(user=member, program=copy)
                .order_by("-started_at")
                .first()
            )
            assigned.append(
                {
                    "program": program.pk,
                    "name": program.name,
                    "client_copy": copy.pk,
                    "last_session_at": last_session.started_at if last_session else None,
                }
            )
        with _user_language(request.user):
            data = ClientDetailSerializer(
                {
                    "client": member,
                    "relationship": relationship,
                    "training_summary": analytics_services.training_summary_canonical(
                        member, dateranges.resolve(dateranges.DEFAULT_RANGE)
                    ),
                    "highlights": achievement_services.highlights_for(member),
                    "recent_records": analytics_services.pr_history(
                        member, dateranges.resolve("all"), limit=10
                    ),
                    "measurements": measurements,
                    "assigned_programs": assigned,
                }
            ).data
        return Response(data)


# --------------------------------------------------------------------
# Shared plans - the client's side
# --------------------------------------------------------------------


class SharedPlanListView(CoachingView):
    """`GET coaching/shared/` - the programs and diet plans the user's
    active coaches have shared with them, each with `imported` when the
    user already has their own copy."""

    @extend_schema(responses=SharedPlansSerializer)
    def get(self, request):
        from apps.nutrition.services import diet_plans_visible_to
        from apps.programs.services import visible_to as programs_visible_to

        coach_ids = coaching_services.active_coach_ids_for(request.user)
        programs = programs_visible_to(request.user).filter(
            shared_with_clients=request.user, owner_id__in=coach_ids
        )
        diet_plans = diet_plans_visible_to(request.user).filter(
            shared_with_clients=request.user, user_id__in=coach_ids
        )
        imported_programs = set(
            Program.objects.filter(owner=request.user, imported_from__in=programs).values_list(
                "imported_from_id", flat=True
            )
        )
        imported_plans = set(
            DietPlan.objects.filter(user=request.user, imported_from__in=diet_plans).values_list(
                "imported_from_id", flat=True
            )
        )
        return Response(
            {
                "programs": [
                    {
                        "id": p.pk, "name": p.name, "coach": p.owner_id,
                        "imported": p.pk in imported_programs,
                    }
                    for p in programs
                ],
                "diet_plans": [
                    {
                        "id": d.pk, "name": d.name, "coach": d.user_id,
                        "imported": d.pk in imported_plans,
                    }
                    for d in diet_plans
                ],
            }
        )


class SharedProgramImportView(CoachingView):
    """`POST coaching/shared/programs/<id>/import/` - the user's own copy
    of a program their coach shared, which can later pull the coach's
    updates. 201 with the copy."""

    @extend_schema(request=None, responses={201: ProgramSerializer})
    @coaching_errors
    def post(self, request, pk):
        source = get_object_or_404(Program, pk=pk)
        copy = coaching_services.import_program_from_coach(source, request.user)
        return Response(
            ProgramSerializer(copy, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class SharedDietPlanImportView(CoachingView):
    """`POST coaching/shared/diet-plans/<id>/import/` - as for programs."""

    @extend_schema(request=None, responses={201: DietPlanSerializer})
    @coaching_errors
    def post(self, request, pk):
        source = get_object_or_404(DietPlan, pk=pk)
        copy = coaching_services.import_diet_plan_from_coach(source, request.user)
        return Response(
            DietPlanSerializer(copy, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class ProgramCoachUpdateView(CoachingView):
    """`GET coaching/programs/<id>/update/` - whether the coach changed
    the program this copy was imported from, and the diff. `POST`
    replaces the copy's workouts with the coach's version (name and
    description stay yours); not calling it is skipping."""

    @extend_schema(responses=CoachUpdateSerializer)
    def get(self, request, pk):
        program = get_object_or_404(Program, pk=pk, owner=request.user)
        available = coaching_services.program_update_available(program)
        with _user_language(request.user):
            diff = coaching_services.program_update_diff(program) if available else None
        return Response({"available": available, "diff": diff})

    @extend_schema(request=None, responses=ProgramSerializer)
    @coaching_errors
    def post(self, request, pk):
        program = get_object_or_404(Program, pk=pk, owner=request.user)
        if not coaching_services.program_update_available(program):
            return Response(
                {"detail": "This program is already up to date."}, status=status.HTTP_409_CONFLICT
            )
        coaching_services.apply_program_update(program, acting_user=request.user)
        program = Program.objects.get(pk=program.pk)
        return Response(ProgramSerializer(program, context={"request": request}).data)


class DietPlanCoachUpdateView(CoachingView):
    """`GET`/`POST coaching/diet-plans/<id>/update/` - as for programs."""

    @extend_schema(responses=CoachUpdateSerializer)
    def get(self, request, pk):
        plan = get_object_or_404(DietPlan, pk=pk, user=request.user)
        available = coaching_services.diet_plan_update_available(plan)
        with _user_language(request.user):
            diff = coaching_services.diet_plan_update_diff(plan) if available else None
        return Response({"available": available, "diff": diff})

    @extend_schema(request=None, responses=DietPlanSerializer)
    @coaching_errors
    def post(self, request, pk):
        plan = get_object_or_404(DietPlan, pk=pk, user=request.user)
        if not coaching_services.diet_plan_update_available(plan):
            return Response(
                {"detail": "This diet plan is already up to date."},
                status=status.HTTP_409_CONFLICT,
            )
        coaching_services.apply_diet_plan_update(plan, acting_user=request.user)
        plan = DietPlan.objects.get(pk=plan.pk)
        return Response(DietPlanSerializer(plan, context={"request": request}).data)


# --------------------------------------------------------------------
# Sharing - the trainer's side
# --------------------------------------------------------------------


class _ShareView(CoachingView):
    """`GET`/`PUT coaching/programs/<id>/clients/` (and `diet-plans/`) -
    which of the trainer's active clients a plan of theirs is shared
    with. `PUT {clients: [user ids]}` replaces the list; only active
    clients can be on it, as on the web."""

    def _plan(self, request, pk):
        raise NotImplementedError

    @extend_schema(responses=ClientIdsSerializer)
    def get(self, request, pk):
        denied = _trainer_only(request.user)
        if denied:
            return denied
        plan = self._plan(request, pk)
        return Response(
            {"clients": sorted(plan.shared_with_clients.values_list("pk", flat=True))}
        )

    @extend_schema(request=ClientIdsSerializer, responses=ClientIdsSerializer)
    def put(self, request, pk):
        denied = _trainer_only(request.user)
        if denied:
            return denied
        plan = self._plan(request, pk)
        serializer = ClientIdsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wanted = set(serializer.validated_data["clients"])
        clients = list(active_clients_of(request.user).filter(pk__in=wanted))
        if len(clients) != len(wanted):
            return Response(
                {"clients": "Only your active clients can be on the list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        plan.shared_with_clients.set(clients)
        return Response({"clients": sorted(wanted)})


class ProgramShareView(_ShareView):
    def _plan(self, request, pk):
        return get_object_or_404(Program, pk=pk, owner=request.user)


class DietPlanShareView(_ShareView):
    def _plan(self, request, pk):
        return get_object_or_404(DietPlan, pk=pk, user=request.user)
