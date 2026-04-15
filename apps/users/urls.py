from django.urls import path
from .views import TelegramAuthView, TokenRefreshView

urlpatterns = [
    path('telegram/', TelegramAuthView.as_view(), name='auth-telegram'),
    path('refresh/', TokenRefreshView.as_view(), name='auth-refresh'),
]
