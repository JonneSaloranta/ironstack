from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views import View

from apps.core.mixins import StaffRequiredMixin

from . import proposals, services
from .forms import ApiKeyForm, AssistantSettingsForm, MessageForm
from .models import (
    AssistantMessage,
    AssistantProposal,
    AssistantSettings,
    Conversation,
    KeySource,
    ProposalKind,
)


def _require_post(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    return None


def _owned_conversation(request, pk):
    return get_object_or_404(Conversation, pk=pk, user=request.user)


def _usage_context(user, access):
    """Today's allowance on the shared provider, for the page to show."""
    if access.key_source != KeySource.SHARED:
        return {}
    remaining = services.remaining_shared_tokens(user)
    return {
        "tokens_used_today": services.tokens_used_today(user),
        "tokens_remaining": remaining,
        "daily_limit": AssistantSettings.load().daily_token_limit,
    }


@login_required
def home(request):
    preference = services.preference_for(request.user)
    access = services.access_for(request.user, preference)
    form = MessageForm(initial={"text": request.GET.get("prompt", "")[:1000]})
    return render(
        request,
        "assistant/home.html",
        {
            "preference": preference,
            "access": access,
            "form": form,
            "conversations": Conversation.objects.filter(user=request.user)[:50],
            **_usage_context(request.user, access),
        },
    )


@login_required
def conversation_create(request):
    if response := _require_post(request):
        return response
    form = MessageForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Write a message first."))
        return redirect("assistant:home")
    try:
        conversation = services.start_conversation(request.user, form.cleaned_data["text"])
    except services.AssistantError as error:
        messages.error(request, str(error))
        return redirect("assistant:home")
    return redirect("assistant:conversation-detail", pk=conversation.pk)


def _thread_context(conversation):
    thread = list(conversation.messages.prefetch_related("proposals"))
    return {
        "conversation": conversation,
        "thread": thread,
        "in_progress": any(message.is_in_progress for message in thread),
    }


@login_required
def conversation_detail(request, pk):
    conversation = _owned_conversation(request, pk)
    return render(
        request,
        "assistant/conversation_detail.html",
        {**_thread_context(conversation), "form": MessageForm()},
    )


@login_required
def message_send(request, pk):
    if response := _require_post(request):
        return response
    conversation = _owned_conversation(request, pk)
    form = MessageForm(request.POST)
    error = None
    if form.is_valid():
        try:
            services.send_message(conversation, form.cleaned_data["text"])
        except services.AssistantError as exc:
            error = str(exc)
    else:
        error = _("Write a message first.")
    if not request.headers.get("HX-Request"):
        if error:
            messages.error(request, error)
        return redirect("assistant:conversation-detail", pk=conversation.pk)
    return render(
        request,
        "assistant/_thread.html",
        {**_thread_context(conversation), "send_error": error},
    )


@login_required
def message_fragment(request, pk):
    """One bubble, polled by the page while its reply is being written."""
    message = get_object_or_404(AssistantMessage, pk=pk, conversation__user=request.user)
    return render(request, "assistant/_message.html", {"message": message})


@login_required
def conversation_delete(request, pk):
    if response := _require_post(request):
        return response
    _owned_conversation(request, pk).delete()
    messages.success(request, _("Conversation deleted."))
    return redirect("assistant:home")


def _owned_proposal(request, pk):
    return get_object_or_404(
        AssistantProposal.objects.select_related("message__conversation"),
        pk=pk,
        message__conversation__user=request.user,
    )


@login_required
def proposal_accept(request, pk):
    if response := _require_post(request):
        return response
    proposal = _owned_proposal(request, pk)
    try:
        created = proposals.accept(proposal)
    except proposals.ProposalError as error:
        messages.error(
            request,
            _("This suggestion can't be created anymore: %(error)s") % {"error": error},
        )
        return redirect("assistant:conversation-detail", pk=proposal.message.conversation_id)
    if proposal.kind == ProposalKind.DIET_PLAN:
        messages.success(
            request, _("Diet plan created. It isn't active until you choose to use it.")
        )
        return redirect("nutrition:diet-plan-detail", pk=created.pk)
    messages.success(request, _("Program created. Review it and make it yours."))
    return redirect("programs:program-detail", pk=created.pk)


@login_required
def proposal_dismiss(request, pk):
    if response := _require_post(request):
        return response
    proposal = _owned_proposal(request, pk)
    proposals.dismiss(proposal)
    return redirect(
        reverse("assistant:conversation-detail", args=[proposal.message.conversation_id])
        + f"#proposal-{proposal.pk}"
    )


@login_required
def settings_view(request):
    preference = services.preference_for(request.user)
    access = services.access_for(request.user, preference)
    return render(
        request,
        "assistant/settings.html",
        {
            "preference": preference,
            "access": access,
            "key_form": ApiKeyForm(),
            "shared_available": services.shared_provider_configured(),
            **_usage_context(request.user, access),
        },
    )


@login_required
def enable(request):
    if response := _require_post(request):
        return response
    services.enable(request.user)
    messages.success(request, _("AI assistant turned on."))
    return redirect("assistant:home")


@login_required
def disable(request):
    if response := _require_post(request):
        return response
    services.disable(request.user)
    messages.success(request, _("AI assistant turned off."))
    return redirect("assistant:settings")


@login_required
def key_save(request):
    if response := _require_post(request):
        return response
    form = ApiKeyForm(request.POST)
    if form.is_valid():
        services.set_own_key(request.user, form.cleaned_data["api_key"])
        messages.success(request, _("API key saved."))
        return redirect("assistant:settings")
    preference = services.preference_for(request.user)
    access = services.access_for(request.user, preference)
    return render(
        request,
        "assistant/settings.html",
        {
            "preference": preference,
            "access": access,
            "key_form": form,
            "shared_available": services.shared_provider_configured(),
            **_usage_context(request.user, access),
        },
    )


@login_required
def key_remove(request):
    if response := _require_post(request):
        return response
    services.remove_own_key(request.user)
    messages.success(request, _("API key removed."))
    return redirect("assistant:settings")


class AdminSettingsView(StaffRequiredMixin, View):
    """Profile → Administration → AI assistant: who may use the
    instance's own provider, the daily limit, and who has been using it."""

    template_name = "assistant/admin_settings.html"

    def _render(self, request, form):
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "shared_available": services.shared_provider_configured(),
                "provider": settings.ASSISTANT_PROVIDER,
                "usage": services.usage_summary(),
            },
        )

    def get(self, request):
        return self._render(request, AssistantSettingsForm(instance=AssistantSettings.load()))

    def post(self, request):
        form = AssistantSettingsForm(request.POST, instance=AssistantSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, _("Assistant settings saved."))
            return redirect("assistant:admin-settings")
        return self._render(request, form)
