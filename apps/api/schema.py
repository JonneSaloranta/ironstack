"""The API's committed OpenAPI contract - docs/API.md "Versioning".

Each released API version has its schema committed under docs/api/
(`openapi-v1.yaml`, ...). `manage.py api_schema` regenerates them and
`manage.py api_schema --check` (also run by the test suite) fails when
the code no longer matches - so every change to what a client sees
shows up as a diff of that file in review, and CI's breaking-change
check (.github/workflows/ci.yml, oasdiff) compares it against master.
"""

from django.conf import settings
from django.utils import translation
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.renderers import OpenApiYamlRenderer

#: Every API version still served, oldest first. Adding "v2" here (and
#: to REST_FRAMEWORK["ALLOWED_VERSIONS"] and config.urls) gives it its
#: own committed schema; v1's file stays as it is.
VERSIONS = ["v1"]


def schema_path(version):
    return settings.BASE_DIR / "docs" / "api" / f"openapi-{version}.yaml"


def build_schema(version):
    """The schema of API `version` as YAML text. Always generated in
    English, and `info.version` is the API version rather than the
    app's release number - the contract only changes when the API does,
    not on every release."""
    with translation.override("en"):
        schema = SchemaGenerator(api_version=version).get_schema(request=None, public=True)
        schema["info"]["version"] = version
        return OpenApiYamlRenderer().render(schema, renderer_context={}).decode()


def outdated_versions():
    """API versions whose committed schema doesn't match the code."""
    outdated = []
    for version in VERSIONS:
        path = schema_path(version)
        if not path.exists() or path.read_text() != build_schema(version):
            outdated.append(version)
    return outdated
