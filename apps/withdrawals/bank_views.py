"""
Saved bank account views for the withdrawals app.

Endpoints:
  GET    /api/v1/withdrawals/banks/                         Bank list
  GET    /api/v1/withdrawals/saved-accounts/                List user's saved accounts
  POST   /api/v1/withdrawals/saved-accounts/                Add new account (resolve + match + save)
  DELETE /api/v1/withdrawals/saved-accounts/<uuid>/         Soft-delete an account
  POST   /api/v1/withdrawals/saved-accounts/<uuid>/set-default/  Set as default
"""
import logging

from django.core.cache import cache
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.kyc.models import BankAccount
from apps.kyc.providers import get_provider
from apps.kyc.serializers import BankAccountResponseSerializer
from apps.kyc.views import BANKS_CACHE_KEY, BANKS_CACHE_TTL

from .bank_account_service import BankAccountError, BankAccountService

logger = logging.getLogger(__name__)


class AddBankAccountSerializer(serializers.Serializer):
    bank_code = serializers.CharField(max_length=10, required=True)
    account_number = serializers.CharField(
        min_length=10, max_length=10, required=True,
    )

    def validate_account_number(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('Account number must contain only digits.')
        return value


def _bank_error_response(e: BankAccountError):
    """Map a BankAccountError to a standardised API error response."""
    payload = {'error': True, 'code': e.code, 'message': e.message}
    if e.extra:
        payload.update(e.extra)

    if e.code == 'KYC_REQUIRED':
        http = status.HTTP_403_FORBIDDEN
    elif e.code == 'NOT_FOUND':
        http = status.HTTP_404_NOT_FOUND
    elif e.code == 'PROVIDER_ERROR':
        http = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        http = status.HTTP_400_BAD_REQUEST
    return Response(payload, status=http)


# ─── Banks list (mirror of /kyc/banks/) ──────────────────────────────────────

class WithdrawalBanksListView(APIView):
    """GET /api/v1/withdrawals/banks/  — bank list (24h cache)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        banks = cache.get(BANKS_CACHE_KEY)
        if banks is None:
            try:
                provider = get_provider()
                banks = provider.list_banks()
                cache.set(BANKS_CACHE_KEY, banks, BANKS_CACHE_TTL)
            except KYCProviderError as e:
                logger.warning('Banks list fetch failed: %s', e)
                return Response(
                    {'error': True, 'code': 'PROVIDER_ERROR',
                     'message': 'Unable to fetch banks list.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
        return Response({'success': True, 'data': {'banks': banks}})


# ─── Saved accounts CRUD ─────────────────────────────────────────────────────

class SavedAccountsView(APIView):
    """
    GET  /api/v1/withdrawals/saved-accounts/   List my saved accounts
    POST /api/v1/withdrawals/saved-accounts/   Add new (resolve + match + save)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        accounts = BankAccountService.list_active(request.user)
        data = BankAccountResponseSerializer(accounts, many=True).data
        return Response({'success': True, 'data': {'accounts': data}})

    def post(self, request):
        serializer = AddBankAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            resolved = BankAccountService.resolve_and_match(
                user=request.user,
                bank_code=serializer.validated_data['bank_code'],
                account_number=serializer.validated_data['account_number'],
            )
            account = BankAccountService.save_account(request.user, resolved)
        except BankAccountError as e:
            return _bank_error_response(e)

        logger.info(
            'Bank account added: user=%s bank=%s acct=%s',
            request.user.id, account.bank_code, account.account_number,
        )

        return Response(
            {
                'success': True,
                'message': 'Bank account verified and saved.',
                'data': BankAccountResponseSerializer(account).data,
            },
            status=status.HTTP_201_CREATED,
        )


class SavedAccountDetailView(APIView):
    """
    DELETE /api/v1/withdrawals/saved-accounts/<uuid>/             Soft-delete
    POST   /api/v1/withdrawals/saved-accounts/<uuid>/set-default/ Set as default
    """
    permission_classes = [IsAuthenticated]

    def _get(self, request, account_id):
        try:
            return BankAccount.objects.get(
                id=account_id, user=request.user, is_active=True,
            )
        except BankAccount.DoesNotExist:
            return None

    def delete(self, request, account_id):
        account = self._get(request, account_id)
        if not account:
            return Response(
                {'error': True, 'code': 'NOT_FOUND',
                 'message': 'Bank account not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            BankAccountService.soft_delete(account, request.user)
        except BankAccountError as e:
            return _bank_error_response(e)
        return Response({'success': True, 'message': 'Bank account removed.'})


class SavedAccountSetDefaultView(APIView):
    """POST /api/v1/withdrawals/saved-accounts/<uuid>/set-default/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, account_id):
        try:
            account = BankAccount.objects.get(
                id=account_id, user=request.user, is_active=True,
            )
        except BankAccount.DoesNotExist:
            return Response(
                {'error': True, 'code': 'NOT_FOUND',
                 'message': 'Bank account not found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            BankAccountService.set_default(account, request.user)
        except BankAccountError as e:
            return _bank_error_response(e)

        return Response({
            'success': True,
            'message': 'Default account updated.',
            'data': BankAccountResponseSerializer(account).data,
        })