"""
Management command to create the FIRST admin (bootstrap).

After the first super_admin exists, create other admins via the dashboard API
(POST /api/v1/admin/admins/). This command is mainly for bootstrapping.

Usage:
    python manage.py create_admin \
        --email super@spinrewards.com \
        --password SecurePass123 \
        --name "Richard Uzor" \
        --role super_admin

Roles: super_admin, finance_admin, support_admin, risk_admin, read_only
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

VALID_ROLES = ['super_admin', 'finance_admin', 'support_admin', 'risk_admin', 'read_only']


class Command(BaseCommand):
    help = 'Create or update an admin (email + password + role)'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True)
        parser.add_argument('--password', required=True)
        parser.add_argument('--name', default='')
        parser.add_argument('--role', default='read_only', choices=VALID_ROLES)
        parser.add_argument('--telegram-id', type=int, default=None)

    def handle(self, *args, **options):
        from apps.admin_panel.models import AdminProfile
        from apps.users.models import User

        email = options['email'].strip().lower()
        password = options['password']
        display_name = options['name'].strip()
        role = options['role']
        telegram_id = options['telegram_id']

        if len(password) < 8:
            raise CommandError('Password must be at least 8 characters.')

        with transaction.atomic():
            if telegram_id:
                try:
                    user = User.objects.get(telegram_id=telegram_id)
                except User.DoesNotExist:
                    raise CommandError(f'No user with telegram_id={telegram_id}.')
            else:
                fake_tid = -(abs(hash(email)) % (10 ** 9))
                first = display_name.split()[0] if display_name else 'Admin'
                last = ' '.join(display_name.split()[1:]) if display_name else ''
                user, created = User.objects.get_or_create(
                    telegram_id=fake_tid,
                    defaults={
                        'first_name': first, 'last_name': last,
                        'username': email.split('@')[0], 'is_staff': True,
                    },
                )
                if created:
                    user.set_unusable_password()
                    user.save()

            if not user.is_staff:
                user.is_staff = True
                user.save(update_fields=['is_staff'])

            profile, created = AdminProfile.objects.get_or_create(
                user=user,
                defaults={'email': email, 'display_name': display_name, 'role': role},
            )
            if not created:
                profile.email = email
                if display_name:
                    profile.display_name = display_name
                profile.role = role
            profile.set_password(password)
            profile.is_active = True
            profile.save()

            action = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(
                f'\n{action} admin:\n'
                f'  Email: {email}\n'
                f'  Role:  {role}\n'
                f'  Name:  {display_name or "(none)"}\n'
                f'\nLogin: POST /api/v1/admin/auth/login/\n'
                f'  {{"email": "{email}", "password": "****"}}'
            ))