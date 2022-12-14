from django.urls import path, include
from rest_framework.schemas import get_schema_view
from rest_framework.renderers import CoreJSONRenderer


urlpatterns = [
    path("auth/", include("api.views.auth.urls"), name="Auth URLS"),
    path(
        "docs",
        get_schema_view(
            title="Utano REST API",
            description="Utano REST API",
            version="1.0",
            renderer_classes=(CoreJSONRenderer,),
        ),
        name="Docs URLS",
    ),
    path(
        "health_institution/",
        include("api.views.health_institution.urls"),
        name="Health Institute URLS",
    ),
    path("staff/", include("api.views.staff.urls"), name="Staff URLS"),
]
