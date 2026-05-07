"""
Management command to create admin users for the dashboard.

Usage:
    # Create a standalone admin (no Telegram account needed):
    python manage.py create_admin \
        --email admin@spinrewards.com \
        --password SecurePass123 \
        --name "Richard Uzor"

    # Link to an existing Telegram user:
    python manage.py create_admin \
        --email admin@spinrewards.com \
        --password SecurePass123 \
        --name "Richard Uzor" \
        --telegram-id 123456789
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = 'Create an admin user for the dashboard (email + password login)'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='Admin email address')
        parser.add_argument('--password', required=True, help='Admin password (min 8 chars)')
        parser.add_argument('--name', default='', help='Display name e.g. "John Admin"')
        parser.add_argument(
            '--telegram-id',
            type=int,
            default=None,
            help='Link to existing user by telegram_id (optional)',
        )

    def handle(self, *args, **options):
        # All model imports inside handle() so Django apps are fully loaded
        from apps.admin_panel.models import AdminProfile
        from apps.users.models import User

        email = options['email'].strip().lower()
        password = options['password']
        display_name = options['name'].strip()
        telegram_id = options['telegram_id']

        if len(password) < 8:
            raise CommandError('Password must be at least 8 characters.')

        with transaction.atomic():
            # ── Find or create the User ────────────────────────────────
            if telegram_id:
                try:
                    user = User.objects.get(telegram_id=telegram_id)
                    self.stdout.write(
                        self.style.SUCCESS(f'Found existing user: {user}')
                    )
                except User.DoesNotExist:
                    raise CommandError(
                        f'No user found with telegram_id={telegram_id}. '
                        f'The user must log in to the mini app first, '
                        f'or omit --telegram-id to create a standalone admin.'
                    )
            else:
                # Create a synthetic User for admin-only access.
                # Deterministic negative telegram_id from email — idempotent.
                fake_tid = -(abs(hash(email)) % (10 ** 9))

                first = display_name.split()[0] if display_name else 'Admin'
                last = ' '.join(display_name.split()[1:]) if display_name else ''

                user, created = User.objects.get_or_create(
                    telegram_id=fake_tid,
                    defaults={
                        'first_name': first,
                        'last_name': last,
                        'username': email.split('@')[0],
                        'is_staff': True,
                    },
                )
                if created:
                    user.set_unusable_password()
                    user.save()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Created new User (telegram_id={fake_tid})'
                        )
                    )
                else:
                    self.stdout.write(
                        f'Using existing User (telegram_id={fake_tid})'
                    )

            # Ensure staff access
            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=['is_staff'])
                self.stdout.write('Set is_staff=True on user.')

            # ── Create or update AdminProfile ─────────────────────────
            profile, created = AdminProfile.objects.get_or_create(
                user=user,
                defaults={
                    'email': email,
                    'display_name': display_name,
                },
            )

            if not created:
                profile.email = email
                profile.display_name = display_name

            profile.set_password(password)
            profile.is_active = True
            profile.save()

            action = 'Created' if created else 'Updated'
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n{action} admin profile successfully!\n'
                    f'  Email:        {email}\n'
                    f'  Display name: {display_name or "(none set)"}\n'
                    f'  User ID:      {user.id}\n'
                    f'\nAdmin can now log in at:\n'
                    f'  POST /api/v1/admin/auth/login/\n'
                    f'  Body: {{"email": "{email}", "password": "****"}}'
                )
            )