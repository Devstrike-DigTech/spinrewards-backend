from django.apps import AppConfig


class ChallengesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.challenges'
    label = 'challenges'
    verbose_name = 'Challenges'

    def ready(self):
        import apps.challenges.signals  # noqa: F401