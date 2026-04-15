from django.urls import path
from .views import WithdrawalView, WithdrawalListView, WithdrawalDetailView

urlpatterns = [
    path('', WithdrawalView.as_view(), name='withdrawal-create'),
    path('list/', WithdrawalListView.as_view(), name='withdrawal-list'),
    path('<uuid:pk>/', WithdrawalDetailView.as_view(), name='withdrawal-detail'),
]
