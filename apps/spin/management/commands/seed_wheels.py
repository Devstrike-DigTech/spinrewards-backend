"""
Seeds the four default wheels per the PRD:
    Standard (₦200 min, multipliers 1×, top ₦5000)
    Power    (₦500 min, 3× multiplier, top ₦15000)
    Mega     (₦2000 min, 10× multiplier, top ₦100000)
    Welcome  (free, special boosted-RTP pool)

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
        'max_stake': Decimal('5000'),
        'is_welcome_only': False,
        'rtp_target': Decimal('92.00'),  # ~8% house edge
        'segments': [
            # (position, label, multiplier, weight, color)
            (0, 'Loss',      Decimal('0'),    50, '#3a3a3a'),
            (1, '0.5×',      Decimal('0.5'),  20, '#5a5a5a'),
            (2, 'Push',      Decimal('1'),    15, '#888888'),
            (3, '2×',        Decimal('2'),    10, '#1A237E'),
            (4, '3×',        Decimal('3'),     4, '#0d47a1'),
            (5, '5×',        Decimal('5'),     1, '#C9961A'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.POWER,
        'name': 'Power Wheel',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('500'),
        'max_stake': Decimal('15000'),
        'is_welcome_only': False,
        'rtp_target': Decimal('90.00'),
        'segments': [
            (0, 'Loss',      Decimal('0'),    60, '#3a3a3a'),
            (1, 'Push',      Decimal('1'),    20, '#888888'),
            (2, '3×',        Decimal('3'),    15, '#1A237E'),
            (3, '5×',        Decimal('5'),     4, '#C9961A'),
            (4, '10×',       Decimal('10'),    1, '#FFD700'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.MEGA,
        'name': 'Mega Wheel',
        'currency_type': Wheel.CurrencyType.COIN,
        'min_stake': Decimal('2000'),
        'max_stake': Decimal('100000'),
        'is_welcome_only': False,
        'rtp_target': Decimal('88.00'),
        'segments': [
            (0, 'Loss',      Decimal('0'),    70, '#3a3a3a'),
            (1, '2×',        Decimal('2'),    15, '#1A237E'),
            (2, '5×',        Decimal('5'),    10, '#0d47a1'),
            (3, '10×',       Decimal('10'),    4, '#C9961A'),
            (4, '50×',       Decimal('50'),    1, '#FFD700'),
        ],
    },
    {
        'wheel_type': Wheel.WheelType.WELCOME,
        'name': 'Welcome Spin',
        'currency_type': Wheel.CurrencyType.COIN,  # ignored — free
        'min_stake': Decimal('0'),
        'max_stake': Decimal('0'),
        'is_welcome_only': True,
        'rtp_target': Decimal('200.00'),  # boosted: marketing-funded
        'segments': [
            # Welcome wheel: every spin wins something. No "Loss" segment.
            # `multiplier` is in NGN (since stake is 0, payout = multiplier × 1).
            # Wait — that math doesn't work for stake=0. We need a different
            # model for free spins: multiplier becomes a flat NGN payout.
            # For now, give a flat ₦500 to ₦5000 win, weighted toward smaller.
            #
            # Hack: when stake_amount is 0, the spin engine treats multiplier
            # as a flat NGN credit. We'll fix this properly once welcome flow
            # is exercised. For seeding, use small amounts.
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
    help = 'Seed the default wheels (Standard, Power, Mega, Welcome).'

    def handle(self, *args, **options):
        with db_transaction.atomic():
            for cfg in WHEELS:
                segments_cfg = cfg.pop('segments')
                wheel, created = Wheel.objects.update_or_create(
                    wheel_type=cfg['wheel_type'],
                    defaults=cfg,
                )
                action = 'Created' if created else 'Updated'
                self.stdout.write(
                    self.style.SUCCESS(f'{action} wheel: {wheel.name}')
                )

                # Replace all segments — admin can edit live afterwards.
                wheel.segments.all().delete()
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
                    f'  Seeded {len(segments_cfg)} segments. '
                    f'Computed RTP: {wheel.computed_rtp}%'
                )

        self.stdout.write(self.style.SUCCESS('\nAll wheels seeded.'))