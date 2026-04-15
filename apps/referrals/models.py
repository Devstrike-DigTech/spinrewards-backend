import uuid
from django.db import models


class Referral(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'        # Registered, not yet deposited
        CONVERTED = 'converted', 'Converted'  # First deposit made, bonuses paid
        EXPIRED = 'expired', 'Expired'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    referrer = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='referrals_made'
    )
    referred_user = models.OneToOneField(
        'users.User', on_delete=models.CASCADE, related_name='referral_entry'
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    referrer_bonus_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    referee_bonus_amount = models.DecimalField(
        max_digits=15, decimal_places=2, default=0
    )
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'referrals'

    def __str__(self):
        return f'Referral({self.referrer} → {self.referred_user}, {self.status})'
