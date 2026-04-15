from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = 'apps.users'
    default_auto_field = 'django.db.models.UUIDField'

    def ready(self):
        pass  # import signals here if needed
