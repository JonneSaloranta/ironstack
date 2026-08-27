from django.urls import path

from .views import DashboardView, healthcheck, robots_txt, service_worker, web_manifest
from .views_backup import (
    BackupDeleteView,
    BackupDownloadView,
    BackupListView,
    BackupRestoreView,
)
from .views_feedback import FeedbackCreateView, FeedbackListView
from .views_seo import SeoSettingsView

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("healthz/", healthcheck, name="healthcheck"),
    # Served at the site root deliberately, not under /static/ — see
    # apps.core.views._serve_static_root_file.
    path("sw.js", service_worker, name="service-worker"),
    path("manifest.json", web_manifest, name="web-manifest"),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("backups/", BackupListView.as_view(), name="backup-list"),
    path("backups/<str:name>/download/", BackupDownloadView.as_view(), name="backup-download"),
    path("backups/<str:name>/restore/", BackupRestoreView.as_view(), name="backup-restore"),
    path("backups/<str:name>/delete/", BackupDeleteView.as_view(), name="backup-delete"),
    path("feedback/", FeedbackCreateView.as_view(), name="feedback-create"),
    path("feedback/admin/", FeedbackListView.as_view(), name="feedback-list"),
    path("seo/admin/", SeoSettingsView.as_view(), name="seo-settings"),
]
