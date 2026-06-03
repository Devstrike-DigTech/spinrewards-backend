"""
apps/settings_app/public_urls.py — Public endpoints (no auth required).

Add to config/urls.py:
    path('api/v1/settings/', include('apps.settings_app.public_urls')),
"""
from django.urls import path

from .public_views import PublicSettingsView

urlpatterns = [
    path('public/', PublicSettingsView.as_view(), name='public-settings'),
]