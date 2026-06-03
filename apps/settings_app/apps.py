"""apps/settings_app/apps.py"""
from django.apps import AppConfig


class SettingsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.settings_app'
    label = 'settings_app'
    verbose_name = 'System Settings'
