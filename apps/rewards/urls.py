from django.urls import path
from .views import DailyRewardStatusView, DailyRewardClaimView

urlpatterns = [
    path('daily/', DailyRewardStatusView.as_view(), name='rewards-daily-status'),
    path('daily/claim/', DailyRewardClaimView.as_view(), name='rewards-daily-claim'),
]
