# from django.urls import path
# from .webhook_views import PaystackWebhookView, FlutterwaveWebhookView

# urlpatterns = [
#     path('paystack/', PaystackWebhookView.as_view(), name='webhook-paystack'),
#     path('flutterwave/', FlutterwaveWebhookView.as_view(), name='webhook-flutterwave'),
# ]
from django.urls import path

from .webhook_views import (
    MonnifyWebhookView,
    NOWPaymentsWebhookView,
    PaystackWebhookView,
)

urlpatterns = [
    path('paystack/', PaystackWebhookView.as_view(), name='webhook-paystack'),
    path('monnify/', MonnifyWebhookView.as_view(), name='webhook-monnify'),
    path('nowpayments/', NOWPaymentsWebhookView.as_view(), name='webhook-nowpayments'),
]