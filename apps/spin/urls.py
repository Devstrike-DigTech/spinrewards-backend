# from django.urls import path
# from .views import SpinView, SpinTiersView, SpinHistoryView

# urlpatterns = [
#     path('', SpinView.as_view(), name='spin'),
#     path('tiers/', SpinTiersView.as_view(), name='spin-tiers'),
#     path('history/', SpinHistoryView.as_view(), name='spin-history'),
# ]
from django.urls import path

from .views import (
    SpinExecuteView,
    SpinHistoryView,
    WelcomeSpinView,
    WheelListView,
)

urlpatterns = [
    path('', SpinExecuteView.as_view(), name='spin-execute'),
    path('welcome/', WelcomeSpinView.as_view(), name='spin-welcome'),
    path('wheels/', WheelListView.as_view(), name='spin-wheels'),
    path('history/', SpinHistoryView.as_view(), name='spin-history'),
]