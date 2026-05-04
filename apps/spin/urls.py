
from django.urls import path

from .views import (
    ActiveWheelListView,
    SpinExecuteView,
    SpinHistoryView,
    WelcomeSpinView,
    WheelForStakeView,
    WheelListView,
)

urlpatterns = [
    path('', SpinExecuteView.as_view(), name='spin-execute'),
    path('welcome/', WelcomeSpinView.as_view(), name='spin-welcome'),

    # Wheel discovery
    path('wheels/', WheelListView.as_view(), name='spin-wheels'),
    path('wheels/active/', ActiveWheelListView.as_view(), name='spin-wheels-active'),
    path('wheels/for-stake/', WheelForStakeView.as_view(), name='spin-wheel-for-stake'),

    path('history/', SpinHistoryView.as_view(), name='spin-history'),
]