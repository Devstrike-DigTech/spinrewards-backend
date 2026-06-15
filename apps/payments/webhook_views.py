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

from rest_framework import status
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
    
class NOWPaymentsPayoutWebhookView(APIView):
    """
    POST /webhooks/nowpayments-payout/
 
    NowPayments calls this when a crypto withdrawal payout finishes.
    Verifies the HMAC-SHA512 signature and applies the result.
 
    Different endpoint from the deposit webhook — different payload shape,
    different signing secret (NOWPAYMENTS_PAYOUT_WEBHOOK_SECRET).
    """
    permission_classes = [AllowAny]  # Verified by signature, not auth
 
    def post(self, request):
        # Lazy imports to keep top-of-file clean
        from apps.withdrawals.providers.nowpayments_payout import (
            NOWPaymentsPayoutProvider,
        )
        from apps.withdrawals.models import Withdrawal
        from apps.withdrawals.services import WithdrawalService
 
        provider = NOWPaymentsPayoutProvider()
 
        # ─── Verify signature ──────────────────────────────────────────
        signature_valid = provider.verify_webhook_signature(
            payload_bytes=request.body,
            headers=request.headers,
        )
        if not signature_valid:
            logger.warning(
                'NowPayments payout webhook: signature verification FAILED. '
                'headers=%s', dict(request.headers),
            )
            return Response(
                {'error': True, 'code': 'INVALID_SIGNATURE'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
 
        # ─── Parse payload ─────────────────────────────────────────────
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as e:
            logger.error(
                'NowPayments payout webhook: invalid JSON: %s body=%s',
                e, request.body[:500],
            )
            return Response(
                {'error': True, 'code': 'INVALID_PAYLOAD'},
                status=status.HTTP_400_BAD_REQUEST,
            )
 
        # ─── Extract event ─────────────────────────────────────────────
        event = provider.parse_webhook(payload)
        if event is None:
            # Intermediate status (creating/sending/waiting) — ack but do nothing
            logger.info(
                'NowPayments payout webhook: non-actionable status, ignoring',
            )
            return Response({'received': True}, status=status.HTTP_200_OK)
 
        # ─── Find the matching withdrawal ──────────────────────────────
        reference = event['reference']
        try:
            withdrawal = Withdrawal.objects.get(reference=reference)
        except Withdrawal.DoesNotExist:
            logger.warning(
                'NowPayments payout webhook for unknown withdrawal: ref=%s',
                reference,
            )
            # Return 200 anyway to prevent NowPayments from retrying forever
            return Response({'received': True}, status=status.HTTP_200_OK)
 
        if withdrawal.rail != Withdrawal.Rail.CRYPTO:
            logger.warning(
                'Payout webhook received for non-crypto withdrawal: %s rail=%s',
                withdrawal.id, withdrawal.rail,
            )
            return Response({'received': True}, status=status.HTTP_200_OK)
 
        # ─── Apply the event ───────────────────────────────────────────
        if event['event_type'] == 'completed':
            WithdrawalService.complete_from_payout_webhook(
                withdrawal,
                tx_hash=event.get('tx_hash', ''),
            )
            logger.info(
                'Crypto withdrawal completed via webhook: %s tx_hash=%s',
                withdrawal.id, event.get('tx_hash', '')[:20],
            )
 
        elif event['event_type'] == 'failed':
            WithdrawalService._mark_failed(
                withdrawal,
                reason=f'NowPayments payout failed (webhook): {payload.get("status")}',
            )
            logger.info(
                'Crypto withdrawal marked failed via webhook: %s',
                withdrawal.id,
            )
 
        return Response({'received': True}, status=status.HTTP_200_OK)