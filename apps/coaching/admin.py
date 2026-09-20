from django.contrib import admin

from .models import CoachingRelationship, CoachingRequest


@admin.register(CoachingRequest)
class CoachingRequestAdmin(admin.ModelAdmin):
    list_display = ["coachee", "coach", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["coachee__username", "coach__username"]


@admin.register(CoachingRelationship)
class CoachingRelationshipAdmin(admin.ModelAdmin):
    list_display = ["coach", "coachee", "created_at", "ended_at"]
    search_fields = ["coach__username", "coachee__username"]
