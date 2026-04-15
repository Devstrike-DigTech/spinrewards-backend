import json
import logging
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .services import PaymentService

logger = logging.getLogger(__name__)


class PaystackWebhookView(APIView):
    """POST /api/v1/webhooks/paystack/ — Paystack event handler."""
    permission_classes = [AllowAny]
    authentication_classes = []  # No JWT for webhooks

    def post(self, request):
        signature = request.headers.get('X-Paystack-Signature', '')
        payload_bytes = request.body  # Raw bytes for HMAC verification

        try:
            payload = json.loads(payload_bytes)
        except json.JSONDecodeError:
            logger.warning('Paystack webhook: invalid JSON body')
            return Response({'received': True})  # Always 200 to prevent retries

        try:
            PaymentService.handle_paystack_webhook(payload, payload_bytes, signature)
        except Exception as e:
            logger.exception('Error processing Paystack webhook: %s', e)

        return Response({'received': True})


class FlutterwaveWebhookView(APIView):
    """POST /api/v1/webhooks/flutterwave/ — Flutterwave event handler."""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.headers.get('Verif-Hash', '')

        try:
            PaymentService.handle_flutterwave_webhook(request.data, signature)
        except Exception as e:
            logger.exception('Error processing Flutterwave webhook: %s', e)

        return Response({'received': True})
