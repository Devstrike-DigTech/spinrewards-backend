from django.urls import path
from .views import SpinView, SpinTiersView, SpinHistoryView

urlpatterns = [
    path('', SpinView.as_view(), name='spin'),
    path('tiers/', SpinTiersView.as_view(), name='spin-tiers'),
    path('history/', SpinHistoryView.as_view(), name='spin-history'),
]
