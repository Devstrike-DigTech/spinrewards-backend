from django.urls import path
from .webhook_views import PaystackWebhookView, FlutterwaveWebhookView

urlpatterns = [
    path('paystack/', PaystackWebhookView.as_view(), name='webhook-paystack'),
    path('flutterwave/', FlutterwaveWebhookView.as_view(), name='webhook-flutterwave'),
]
