import uuid
from django.db import models


class DailyReward(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        'users.User', on_delete=models.CASCADE, related_name='daily_rewards'
    )
    streak_day = models.PositiveIntegerField()
    reward_amount = models.DecimalField(max_digits=15, decimal_places=2)
    reward_type = models.CharField(
        max_length=10, choices=[('coin', 'Coin'), ('cash', 'Cash')], default='coin'
    )
    transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='daily_reward',
    )
    claimed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'daily_rewards'
        ordering = ['-claimed_at']

    def __str__(self):
        return f'DailyReward(user={self.user}, day={self.streak_day}, amount={self.reward_amount})'
