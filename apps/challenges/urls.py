"""
Challenge player URL routes.

Mount at /api/v1/challenges/ in config/urls.py.
Admin routes live in apps/admin_panel/urls.py.
"""
from django.urls import path

from .views import ChallengeDetailView, ChallengeListView

urlpatterns = [
    path('', ChallengeListView.as_view(), name='challenge-list'),
    path('<uuid:challenge_id>/', ChallengeDetailView.as_view(), name='challenge-detail'),
]