from django.urls import path
from .views import WalletView, TransactionListView

urlpatterns = [
    path('', WalletView.as_view(), name='wallet-detail'),
    path('transactions/', TransactionListView.as_view(), name='wallet-transactions'),
]
