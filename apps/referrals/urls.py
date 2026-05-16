"""
Referrals player URL routes.

Mount at /api/v1/referrals/ in config/urls.py.
Admin routes live in apps/admin_panel/urls.py.
"""
from django.urls import path

from .views import (
    ApplyReferralCodeView,
    MyReferralCodeView,
    MyReferralsView,
)

urlpatterns = [
    path('my-code/',      MyReferralCodeView.as_view(),    name='referral-my-code'),
    path('apply/',        ApplyReferralCodeView.as_view(), name='referral-apply'),
    path('my-referrals/', MyReferralsView.as_view(),       name='referral-my-list'),
]