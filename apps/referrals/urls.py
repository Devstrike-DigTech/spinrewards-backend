from django.urls import path
from .views import ReferralInfoView, ReferralListView

urlpatterns = [
    path('', ReferralInfoView.as_view(), name='referral-info'),
    path('list/', ReferralListView.as_view(), name='referral-list'),
]
