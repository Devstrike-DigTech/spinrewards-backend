"""
Seeds the four default wheels with NON-OVERLAPPING stake ranges:
    Standard: [200, 500)
    Power:    [500, 2000)
    Mega:     [2000, 100001)
    Welcome:  free (range 0,0)

⚠️  DEVELOPMENT VALUES ONLY ⚠️
═══════════════════════════════════════════════════════════════════════
The probability weights below are TEST VALUES that may not hit the RTP
percentages labeled. They produce varied outcomes for development and
testing. Operations team will tune segments via admin panel before launch.
═══════════════════════════════════════════════════════════════════════

Run with:
    docker compose run --rm api python manage.py seed_wheels

Idempotent: running multiple times updates existing wheels without
duplicating them.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from apps.spin.models import Wheel, WheelSegment


WHEELS = [
    {
        'wheel_type': Wheel.WheelType.STANDARD,
        'name': 'Standard Wheel',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('200'),
        'max_stake': Decimal('500'),  # exclusive upper bound
        'is_welcome_only': False,
        'rtp_target': Decimal('92.00'),
        'segments': [
            (0, 'Loss',  Decimal('0'),    50, '#3a3a3a'),
            (1, '0.5×',  Decimal('0.5'),  20, '#5a5a5a'),
            (2, 'Push',  Decimal('1'),    15, '#888888'),
            (3, '2×',    Decimal('2'),    10, '#1A237E'),
            (4, '3×',    Decimal('3'),     4, '#0d47a1'),
            (5, '5×',    Decimal('5'),     1, '#C9961A'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.POWER,
        'name': 'Power Wheel',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('500'),
        'max_stake': Decimal('2000'),  # exclusive
        'is_welcome_only': False,
        'rtp_target': Decimal('90.00'),
        'segments': [
            (0, 'Loss',  Decimal('0'),    60, '#3a3a3a'),
            (1, 'Push',  Decimal('1'),    20, '#888888'),
            (2, '3×',    Decimal('3'),    15, '#1A237E'),
            (3, '5×',    Decimal('5'),     4, '#C9961A'),
            (4, '10×',   Decimal('10'),    1, '#FFD700'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.MEGA,
        'name': 'Mega Wheel',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('2000'),
        'max_stake': Decimal('100001'),  # exclusive — accepts up to 100,000
        'is_welcome_only': False,
        'rtp_target': Decimal('88.00'),
        'segments': [
            (0, 'Loss',  Decimal('0'),    70, '#3a3a3a'),
            (1, '2×',    Decimal('2'),    15, '#1A237E'),
            (2, '5×',    Decimal('5'),    10, '#0d47a1'),
            (3, '10×',   Decimal('10'),    4, '#C9961A'),
            (4, '50×',   Decimal('50'),    1, '#FFD700'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.WELCOME,
        'name': 'Welcome Spin',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('0'),
        'max_stake': Decimal('0'),  # welcome wheels exempt from range checks
        'is_welcome_only': True,
        'rtp_target': Decimal('200.00'),
        'segments': [
            (0, '₦100',     Decimal('100'),    50, '#888888'),
            (1, '₦200',     Decimal('200'),    25, '#5a5a5a'),
            (2, '₦500',     Decimal('500'),    15, '#1A237E'),
            (3, '₦1000',    Decimal('1000'),    7, '#0d47a1'),
            (4, '₦2500',    Decimal('2500'),    2, '#C9961A'),
            (5, '₦5000',    Decimal('5000'),    1, '#FFD700'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed the default wheels with non-overlapping stake ranges.'

    def handle(self, *args, **options):
        with db_transaction.atomic():
            # Delete existing wheels first to avoid clean()'s overlap check
            # rejecting an update where ranges have changed.
            existing_count = Wheel.objects.count()
            if existing_count:
                self.stdout.write(
                    f'Removing {existing_count} existing wheels for fresh seed...'
                )
                Wheel.objects.all().delete()

            for cfg in WHEELS:
                segments_cfg = cfg.pop('segments')
                wheel = Wheel.objects.create(**cfg)
                self.stdout.write(
                    self.style.SUCCESS(f'Created wheel: {wheel.name}')
                )

                for pos, label, mult, weight, color in segments_cfg:
                    WheelSegment.objects.create(
                        wheel=wheel,
                        position=pos,
                        label=label,
                        multiplier=mult,
                        probability_weight=weight,
                        color=color,
                    )
                self.stdout.write(
                    f'  Range: [{wheel.min_stake}, {wheel.max_stake}) | '
                    f'Segments: {len(segments_cfg)} | '
                    f'Computed RTP: {wheel.computed_rtp}%'
                )

        self.stdout.write(self.style.SUCCESS('\nAll wheels seeded.'))