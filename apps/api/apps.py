from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    verbose_name = "API"

    def ready(self):
        # Import side effect only — see apps.api.openapi's own
        # docstring for why this has to happen somewhere, and why
        # AppConfig.ready() (guaranteed to run once, after every app's
        # models are loaded) is the right place for it.
        from . import openapi  # noqa: F401
