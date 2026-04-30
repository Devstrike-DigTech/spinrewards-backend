# import json
# import logging
# from rest_framework.views import APIView
# from rest_framework.permissions import AllowAny
# from rest_framework.response import Response

# from .services import PaymentService

# logger = logging.getLogger(__name__)


# class PaystackWebhookView(APIView):
#     """POST /api/v1/webhooks/paystack/ — Paystack event handler."""
#     permission_classes = [AllowAny]
#     authentication_classes = []  # No JWT for webhooks

#     def post(self, request):
#         signature = request.headers.get('X-Paystack-Signature', '')
#         payload_bytes = request.body  # Raw bytes for HMAC verification

#         try:
#             payload = json.loads(payload_bytes)
#         except json.JSONDecodeError:
#             logger.warning('Paystack webhook: invalid JSON body')
#             return Response({'received': True})  # Always 200 to prevent retries

#         try:
#             PaymentService.handle_paystack_webhook(payload, payload_bytes, signature)
#         except Exception as e:
#             logger.exception('Error processing Paystack webhook: %s', e)

#         return Response({'received': True})


# class FlutterwaveWebhookView(APIView):
#     """POST /api/v1/webhooks/flutterwave/ — Flutterwave event handler."""
#     permission_classes = [AllowAny]
#     authentication_classes = []

#     def post(self, request):
#         signature = request.headers.get('Verif-Hash', '')

#         try:
#             PaymentService.handle_flutterwave_webhook(request.data, signature)
#         except Exception as e:
#             logger.exception('Error processing Flutterwave webhook: %s', e)

#         return Response({'received': True})

"""
Webhook receivers — one view per provider.

All three views follow the same shape:
    1. Read raw bytes for HMAC verification
    2. Verify signature → return 401 if invalid (NOT 200)
    3. Parse JSON → return 200 if not a JSON body (provider may health-check)
    4. Hand off to PaymentService.process_deposit_completion
    5. Return 200 (provider stops retrying)

Critical:
  - permission_classes = [AllowAny] + authentication_classes = []
    Webhooks come from the provider, not from a user. JWT is irrelevant.
  - Must accept raw bytes (request.body) BEFORE Django parses JSON,
    because HMAC is computed over the exact bytes sent.
  - Return 401 (not 200) on bad signatures so attackers can't probe.
"""
import json
import logging

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Deposit
from .providers.monnify import MonnifyProvider
from .providers.nowpayments import NOWPaymentsProvider
from .providers.paystack import PaystackProvider
from .services import PaymentService

logger = logging.getLogger(__name__)


def _process(view_self, provider_impl, provider_name: str, request):
    """
    Shared webhook processing logic. Returns a DRF Response.

    On success, returns 200 with a small body. On invalid signature, returns
    401 (this is a security measure — don't tell unauthenticated callers
    what's wrong).
    """
    payload_bytes = request.body
    headers = request.META  # WSGI-style headers (HTTP_*)
    # Also expose normal-cased headers for provider classes to find
    # them either way:
    headers_dict = {k: v for k, v in request.headers.items()}
    # Merge so the provider can use either style
    merged_headers = {**headers, **headers_dict}

    if not provider_impl.verify_webhook_signature(payload_bytes, merged_headers):
        logger.warning('%s webhook: invalid signature (rejected)', provider_name)
        return Response({'error': 'Invalid signature'}, status=401)

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        # Some providers send health-check pings with empty bodies.
        logger.info('%s webhook: empty/invalid JSON body, ignoring', provider_name)
        return Response({'received': True}, status=200)

    parsed = provider_impl.parse_webhook(payload)
    if parsed is None:
        # Webhook is signed and valid, but it's not an event we care about
        # (test event, intermediate status, etc.). Acknowledge it.
        return Response({'received': True}, status=200)

    try:
        deposit = PaymentService.process_deposit_completion(provider_name, parsed)
    except Exception as e:
        # Returning 500 would cause provider to retry. We've already
        # verified the signature, so we accept the webhook (200) but log
        # the error for ops to investigate.
        logger.exception('%s webhook processing error: %s', provider_name, e)
        return Response({'received': True, 'processed': False}, status=200)

    return Response(
        {'received': True, 'deposit_id': str(deposit.id) if deposit else None},
        status=200,
    )


class PaystackWebhookView(APIView):
    """POST /api/v1/webhooks/paystack/"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        return _process(self, PaystackProvider(), Deposit.Provider.PAYSTACK, request)


class MonnifyWebhookView(APIView):
    """POST /api/v1/webhooks/monnify/"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        return _process(self, MonnifyProvider(), Deposit.Provider.MONNIFY, request)


class NOWPaymentsWebhookView(APIView):
    """POST /api/v1/webhooks/nowpayments/"""
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        return _process(
            self, NOWPaymentsProvider(), Deposit.Provider.NOWPAYMENTS, request,
        )