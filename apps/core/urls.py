from django.urls import path

from .views import DashboardView, healthcheck, service_worker, web_manifest
from .views_backup import BackupDownloadView, BackupListView, BackupRestoreView

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("healthz/", healthcheck, name="healthcheck"),
    # Served at the site root deliberately, not under /static/ — see
    # apps.core.views._serve_static_root_file.
    path("sw.js", service_worker, name="service-worker"),
    path("manifest.json", web_manifest, name="web-manifest"),
    path("backups/", BackupListView.as_view(), name="backup-list"),
    path("backups/<str:name>/download/", BackupDownloadView.as_view(), name="backup-download"),
    path("backups/<str:name>/restore/", BackupRestoreView.as_view(), name="backup-restore"),
]
