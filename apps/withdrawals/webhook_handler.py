"""Paystack transfer webhook handler."""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from rest_framework import status as http_status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Withdrawal
from .services import WithdrawalService

logger = logging.getLogger(__name__)


class PaystackTransferWebhookView(APIView):
    """
    POST /api/v1/webhooks/paystack-transfer/

    Receives Paystack transfer.* events.
    HMAC-SHA512 signature verification using PAYSTACK_SECRET_KEY.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        secret = getattr(settings, 'PAYSTACK_SECRET_KEY', '').encode()
        signature = request.headers.get('X-Paystack-Signature', '')

        if not secret:
            logger.error('PAYSTACK_SECRET_KEY not configured')
            return Response(
                {'error': True, 'code': 'CONFIG_ERROR'},
                status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        expected = hmac.new(secret, request.body, hashlib.sha512).hexdigest()

        if not hmac.compare_digest(expected, signature):
            logger.warning('Paystack transfer webhook: invalid signature')
            return Response(
                {'error': True, 'code': 'INVALID_SIGNATURE'},
                status=http_status.HTTP_401_UNAUTHORIZED,
            )

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return Response(
                {'error': True, 'code': 'INVALID_JSON'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        event = payload.get('event', '')
        data = payload.get('data', {})

        if not event.startswith('transfer.'):
            return Response({'received': True}, status=http_status.HTTP_200_OK)

        reference = data.get('reference', '')
        transfer_code = data.get('transfer_code', '')

        withdrawal = (
            Withdrawal.objects.filter(reference=reference).first() or
            Withdrawal.objects.filter(provider_transfer_id=transfer_code).first()
        )

        if not withdrawal:
            logger.warning(
                'Paystack transfer webhook: not found ref=%s tx=%s',
                reference, transfer_code,
            )
            return Response({'received': True}, status=http_status.HTTP_200_OK)

        withdrawal.provider_response = payload
        withdrawal.save(update_fields=['provider_response'])

        if event == 'transfer.success':
            WithdrawalService.complete(withdrawal)
        elif event in ('transfer.failed', 'transfer.reversed'):
            reason = data.get('reason') or data.get('message') or 'Transfer failed'
            WithdrawalService.fail(withdrawal, reason)

        return Response({'received': True}, status=http_status.HTTP_200_OK)