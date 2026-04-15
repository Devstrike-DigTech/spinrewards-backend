from django.urls import path
from .views import DepositView, DepositListView

urlpatterns = [
    path('', DepositView.as_view(), name='deposit-create'),
    path('list/', DepositListView.as_view(), name='deposit-list'),
]
