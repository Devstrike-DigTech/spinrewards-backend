"""
Seed default system settings into the database.

Idempotent — only inserts rows that don't yet exist. Does not overwrite
existing values.

Usage:
    python manage.py seed_settings
"""
from decimal import Decimal

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Seed default system settings (idempotent)'

    def handle(self, *args, **options):
        from apps.settings_app.models import SettingKey, SystemSetting

        created = 0
        skipped = 0

        for key, (default, description) in SettingKey.DEFAULTS.items():
            obj, was_created = SystemSetting.objects.get_or_create(
                key=key,
                defaults={
                    'value': Decimal(default),
                    'description': description,
                },
            )
            if was_created:
                self.stdout.write(self.style.SUCCESS(f'  + {key} = {default}'))
                created += 1
            else:
                self.stdout.write(f'  · {key} = {obj.value} (kept)')
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'\nSeed complete: {created} created, {skipped} already existed.'
            )
        )
