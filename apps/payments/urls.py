# from django.urls import path
# from .views import DepositView, DepositListView

# urlpatterns = [
#     path('', DepositView.as_view(), name='deposit-create'),
#     path('list/', DepositListView.as_view(), name='deposit-list'),
# ]

from django.urls import path

from .views import (
    DepositDetailView,
    DepositInitiateView,
    DepositListView,
    VirtualAccountView,
    payment_callback_page,
)

urlpatterns = [
    path('', DepositInitiateView.as_view(), name='deposit-initiate'),
    path('list/', DepositListView.as_view(), name='deposit-list'),
    path('virtual-account/', VirtualAccountView.as_view(), name='virtual-account'),
    path('<uuid:deposit_id>/', DepositDetailView.as_view(), name='deposit-detail'),
    path('callback/', payment_callback_page, name='payment-callback'),
]