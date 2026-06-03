"""apps/settings_app/urls.py"""
from django.urls import path

from .views import AdminSettingDetailView, AdminSettingsListView

urlpatterns = [
    path('', AdminSettingsListView.as_view(), name='admin-settings-list'),
    path('<str:key>/', AdminSettingDetailView.as_view(), name='admin-settings-detail'),
]
