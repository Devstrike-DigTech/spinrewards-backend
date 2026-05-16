"""
Referrals models.

ReferralCode   — unique shareable code per user (auto-generated on first request)
Referral       — tracks one referrer → referred_user relationship + status
"""
import uuid

from django.db import models
from django.utils import timezone


class ReferralCode(models.Model):
    """
    One unique code per user.

    Generated on first request (lazy). The code is short (8 chars),
    uppercase alphanumeric, and guaranteed unique.

    Example: SPIN-X7K2M9PQ
    """
    id   = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='my_referral_code',
    )
    code = models.CharField(max_length=20, unique=True, db_index=True)

    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'referral_codes'

    def __str__(self):
        return f'ReferralCode({self.code} → user={self.user_id})'


class Referral(models.Model):
    """
    A single referral relationship.

    Status flow:
      pending     → referred user registered but not yet deposited
      qualified   → referred user completed first deposit (reward fires)
      rewarded    → referrer received their reward
      rejected    → failed validation (self-referral, duplicate, etc.)
    """

    class Status(models.TextChoices):
        PENDING   = 'pending',   'Pending'
        QUALIFIED = 'qualified', 'Qualified'
        REWARDED  = 'rewarded',  'Rewarded'
        REJECTED  = 'rejected',  'Rejected'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    referrer      = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='referrals_made',
    )
    referred_user = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='referral_entry',
    )
    referral_code = models.ForeignKey(
        ReferralCode,
        on_delete=models.SET_NULL,
        null=True,
        related_name='referrals',
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )

    # Qualification
    qualified_at = models.DateTimeField(null=True, blank=True)
    rewarded_at  = models.DateTimeField(null=True, blank=True)

    # Rejection reason
    rejection_reason = models.CharField(max_length=200, blank=True)

    # Reward snapshot (what was given at time of reward)
    reward_snapshot = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'referrals'
        ordering = ['-created_at']

    def __str__(self):
        return (
            f'Referral({self.referrer_id} → {self.referred_user_id}, '
            f'status={self.status})'
        )

    @property
    def is_qualified(self) -> bool:
        return self.status in (
            self.Status.QUALIFIED, self.Status.REWARDED
        )