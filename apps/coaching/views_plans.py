from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.nutrition.models import DietPlan
from apps.programs.models import Program

from . import services


@login_required
def shared_plan_list(request):
    """"Plans from my coaches" — the coach-shared subset of
    apps.programs.services.visible_to/apps.nutrition.services.
    diet_plans_visible_to, which both also return the requesting
    user's own rows and (for programs) system templates; this view
    only wants what an active coach has actually shared."""
    from apps.nutrition.services import diet_plans_visible_to
    from apps.programs.services import visible_to as programs_visible_to

    coach_ids = services.active_coach_ids_for(request.user)
    programs = programs_visible_to(request.user).filter(
        shared_with_clients=request.user, owner_id__in=coach_ids
    )
    diet_plans = diet_plans_visible_to(request.user).filter(
        shared_with_clients=request.user, user_id__in=coach_ids
    )
    imported_program_ids = set(
        Program.objects.filter(owner=request.user, imported_from__in=programs).values_list(
            "imported_from_id", flat=True
        )
    )
    imported_diet_plan_ids = set(
        DietPlan.objects.filter(user=request.user, imported_from__in=diet_plans).values_list(
            "imported_from_id", flat=True
        )
    )
    return render(
        request,
        "coaching/shared_plan_list.html",
        {
            "programs": programs,
            "diet_plans": diet_plans,
            "imported_program_ids": imported_program_ids,
            "imported_diet_plan_ids": imported_diet_plan_ids,
        },
    )


@login_required
def shared_program_import(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source = get_object_or_404(Program, pk=pk)
    try:
        copy = services.import_program_from_coach(source, request.user)
        messages.success(request, _("Imported “%(name)s”.") % {"name": copy.name})
        return redirect("programs:program-detail", pk=copy.pk)
    except services.CoachingError as error:
        messages.error(request, str(error))
        return redirect("coaching:shared-plan-list")


@login_required
def shared_diet_plan_import(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source = get_object_or_404(DietPlan, pk=pk)
    try:
        copy = services.import_diet_plan_from_coach(source, request.user)
        messages.success(request, _("Imported “%(name)s”.") % {"name": copy.name})
        return redirect("nutrition:diet-plan-detail", pk=copy.pk)
    except services.CoachingError as error:
        messages.error(request, str(error))
        return redirect("coaching:shared-plan-list")


@login_required
def program_update_preview(request, pk):
    program = get_object_or_404(Program, pk=pk, owner=request.user)
    if not services.program_update_available(program):
        messages.info(request, _("This program is already up to date."))
        return redirect("programs:program-detail", pk=program.pk)
    diff = services.program_update_diff(program)
    return render(
        request, "coaching/program_update_diff.html", {"program": program, "diff": diff}
    )


@login_required
def program_update_apply(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    program = get_object_or_404(Program, pk=pk, owner=request.user)
    try:
        services.apply_program_update(program, acting_user=request.user)
        messages.success(request, _("Program updated from your coach's latest version."))
    except services.CoachingError as error:
        messages.error(request, str(error))
    return redirect("programs:program-detail", pk=program.pk)


@login_required
def diet_plan_update_preview(request, pk):
    plan = get_object_or_404(DietPlan, pk=pk, user=request.user)
    if not services.diet_plan_update_available(plan):
        messages.info(request, _("This diet plan is already up to date."))
        return redirect("nutrition:diet-plan-detail", pk=plan.pk)
    diff = services.diet_plan_update_diff(plan)
    return render(
        request, "coaching/diet_plan_update_diff.html", {"plan": plan, "diff": diff}
    )


@login_required
def diet_plan_update_apply(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    plan = get_object_or_404(DietPlan, pk=pk, user=request.user)
    try:
        services.apply_diet_plan_update(plan, acting_user=request.user)
        messages.success(request, _("Diet plan updated from your coach's latest version."))
    except services.CoachingError as error:
        messages.error(request, str(error))
    return redirect("nutrition:diet-plan-detail", pk=plan.pk)
