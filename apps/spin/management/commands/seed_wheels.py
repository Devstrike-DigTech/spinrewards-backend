"""
Seeds the four default wheels with NON-OVERLAPPING stake ranges.

⚠️  DEVELOPMENT VALUES ONLY ⚠️
═══════════════════════════════════════════════════════════════════════
The probability weights below are TEST VALUES. Operations team will
tune segments via admin panel before launch.
═══════════════════════════════════════════════════════════════════════

Idempotency strategy:
  - If a wheel of the same wheel_type already exists, UPDATE it in place
    (does not delete; preserves Spin history references)
  - If a wheel doesn't exist, CREATE it
  - Segments are also updated in place (matched by wheel + position)
  - Extra segments (positions beyond what we configure) are deactivated
    rather than deleted, again preserving Spin references

This makes the command safe to run on every deploy, including production
deploys where real Spin records reference the wheels.

Run with:
    docker compose run --rm api python manage.py seed_wheels
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
        'max_stake': Decimal('500'),
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
        'max_stake': Decimal('2000'),
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
        'max_stake': Decimal('100001'),
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
        'max_stake': Decimal('0'),
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
    help = 'Seed/update the default wheels. Idempotent — safe to re-run.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-existing',
            action='store_true',
            help='Skip wheels that already exist (only create missing ones).',
        )

    def handle(self, *args, **options):
        skip_existing = options['skip_existing']

        with db_transaction.atomic():
            for cfg in WHEELS:
                segments_cfg = cfg.pop('segments')
                wheel_type = cfg['wheel_type']
                wheel_name = cfg['name']

                # ── Find or create wheel by wheel_type ──
                # We use wheel_type (not name) because wheel_type is a stable
                # identifier; admin might rename a wheel via the panel.
                existing = Wheel.objects.filter(wheel_type=wheel_type).first()

                if existing:
                    if skip_existing:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Skipped (already exists): {existing.name}'
                            )
                        )
                        cfg['segments'] = segments_cfg
                        continue

                    # ── Update in place ──
                    # IMPORTANT: We avoid changing min_stake/max_stake on
                    # existing wheels because that could break overlap
                    # validation if other wheels haven't been processed yet.
                    # If you need to change ranges, do it via admin panel
                    # OR run with all wheels deactivated first.
                    for field in ['name', 'currency_type', 'is_welcome_only',
                                  'rtp_target']:
                        setattr(existing, field, cfg[field])

                    # Range updates: only apply if they don't conflict with
                    # the wheel's currently active state. We try to update,
                    # and if validation fails we keep the existing range.
                    try:
                        existing.min_stake = cfg['min_stake']
                        existing.max_stake = cfg['max_stake']
                        existing.save()
                        self.stdout.write(
                            self.style.SUCCESS(f'Updated wheel: {existing.name}')
                        )
                    except Exception as e:
                        # Range update failed (probably overlap during a
                        # mid-update state). Keep existing range.
                        existing.refresh_from_db()
                        for field in ['name', 'currency_type', 'is_welcome_only',
                                      'rtp_target']:
                            setattr(existing, field, cfg[field])
                        existing.save()
                        self.stdout.write(
                            self.style.WARNING(
                                f'Updated wheel (kept existing range): {existing.name} '
                                f'— range update would conflict: {e}'
                            )
                        )

                    wheel = existing
                else:
                    # ── Create new wheel ──
                    wheel = Wheel.objects.create(**cfg)
                    self.stdout.write(
                        self.style.SUCCESS(f'Created wheel: {wheel.name}')
                    )

                # ── Update or create segments by position ──
                configured_positions = set()
                for pos, label, mult, weight, color in segments_cfg:
                    configured_positions.add(pos)
                    WheelSegment.objects.update_or_create(
                        wheel=wheel,
                        position=pos,
                        defaults={
                            'label': label,
                            'multiplier': mult,
                            'probability_weight': weight,
                            'color': color,
                            'is_active': True,
                        },
                    )

                # ── Deactivate (don't delete) extra segments ──
                # Spins reference segments via PROTECT, so we deactivate
                # rather than delete. Keeps history intact.
                extra_segments = wheel.segments.exclude(
                    position__in=configured_positions
                )
                if extra_segments.exists():
                    count = extra_segments.update(is_active=False)
                    self.stdout.write(
                        f'  Deactivated {count} extra segment(s) '
                        f'(positions beyond configured)'
                    )

                self.stdout.write(
                    f'  Range: [{wheel.min_stake}, {wheel.max_stake}) | '
                    f'Segments: {len(segments_cfg)} | '
                    f'Computed RTP: {wheel.computed_rtp}%'
                )

                # Restore for next iteration's skip-existing check
                cfg['segments'] = segments_cfg

        self.stdout.write(self.style.SUCCESS('\nAll wheels seeded.'))