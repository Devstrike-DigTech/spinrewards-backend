# """
# KYC HTTP endpoints.

#   POST /api/v1/kyc/submit/             Submit/resubmit KYC application
#   POST /api/v1/kyc/resolve-bank/       Live bank account name resolution
#   POST /api/v1/kyc/upload-document/    Upload utility bill or bank statement
#   GET  /api/v1/kyc/status/             Current KYC status (per section)
#   GET  /api/v1/kyc/banks/              List of supported banks (cached 24h)
# """
# import logging

# from django.core.cache import cache
# from rest_framework import status
# from rest_framework.parsers import FormParser, MultiPartParser
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework.views import APIView

# from .providers import get_provider
# from .providers.base import KYCProviderError
# from .serializers import (
#     DocumentUploadSerializer,
#     KYCDocumentResponseSerializer,
#     KYCStatusResponseSerializer,
#     KYCSubmitSerializer,
#     ResolveBankSerializer,
# )
# from .services import KYCService, KYCServiceError

# logger = logging.getLogger(__name__)


# BANKS_CACHE_KEY = 'kyc:banks_list'
# BANKS_CACHE_TTL = 60 * 60 * 24      # 24 hours


# class KYCSubmitView(APIView):
#     """POST /api/v1/kyc/submit/"""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         serializer = KYCSubmitSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         try:
#             KYCService.submit(
#                 user=request.user,
#                 payload=serializer.validated_data,
#             )
#         except KYCServiceError as e:
#             return Response(
#                 {'error': True, 'code': 'VALIDATION_ERROR', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         # Always return current status snapshot
#         status_data = KYCService.get_status(request.user)
#         return Response(
#             {
#                 'success': True,
#                 'data': KYCStatusResponseSerializer(status_data).data,
#             },
#             status=status.HTTP_200_OK,
#         )


# class ResolveBankView(APIView):
#     """POST /api/v1/kyc/resolve-bank/"""
#     permission_classes = [IsAuthenticated]

#     def post(self, request):
#         serializer = ResolveBankSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         try:
#             result = KYCService.resolve_bank(
#                 bank_code=serializer.validated_data['bank_code'],
#                 account_number=serializer.validated_data['account_number'],
#             )
#         except KYCServiceError as e:
#             return Response(
#                 {'error': True, 'code': 'BANK_RESOLVE_FAILED', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         return Response(
#             {'success': True, 'data': result},
#             status=status.HTTP_200_OK,
#         )


# class UploadDocumentView(APIView):
#     """POST /api/v1/kyc/upload-document/"""
#     permission_classes = [IsAuthenticated]
#     parser_classes = [MultiPartParser, FormParser]

#     def post(self, request):
#         serializer = DocumentUploadSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         try:
#             doc = KYCService.upload_document(
#                 user=request.user,
#                 file=serializer.validated_data['file'],
#                 document_type=serializer.validated_data['document_type'],
#             )
#         except KYCServiceError as e:
#             return Response(
#                 {'error': True, 'code': 'INVALID_DOCUMENT', 'message': str(e)},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         return Response(
#             {'success': True, 'data': KYCDocumentResponseSerializer(doc).data},
#             status=status.HTTP_201_CREATED,
#         )


# class KYCStatusView(APIView):
#     """GET /api/v1/kyc/status/"""
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         status_data = KYCService.get_status(request.user)
#         return Response(
#             {
#                 'success': True,
#                 'data': KYCStatusResponseSerializer(status_data).data,
#             },
#             status=status.HTTP_200_OK,
#         )


# class BanksListView(APIView):
#     """
#     GET /api/v1/kyc/banks/

#     List of supported Nigerian banks. Cached for 24 hours.
#     """
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         banks = cache.get(BANKS_CACHE_KEY)

#         if banks is None:
#             try:
#                 provider = get_provider()
#                 banks = provider.list_banks()
#                 cache.set(BANKS_CACHE_KEY, banks, BANKS_CACHE_TTL)
#             except KYCProviderError as e:
#                 logger.warning('Banks list fetch failed: %s', e)
#                 return Response(
#                     {
#                         'error': True,
#                         'code': 'PROVIDER_ERROR',
#                         'message': 'Unable to fetch banks list. Please try again.',
#                     },
#                     status=status.HTTP_503_SERVICE_UNAVAILABLE,
#                 )

#         return Response(
#             {'success': True, 'data': {'banks': banks}},
#             status=status.HTTP_200_OK,
#         )

"""
KYC API views — UPDATED for NIN-only flow.

Endpoints:
  POST /api/v1/kyc/submit/               NIN-only submission. Locked after verification.
  GET  /api/v1/kyc/status/               Current status (NIN + document)
  POST /api/v1/kyc/upload-document/      Document upload (unchanged)
  GET  /api/v1/kyc/banks/                Bank list (kept for backward compat)
  POST /api/v1/kyc/resolve-bank/         DEPRECATED — returns 410 Gone
"""
import logging

from django.core.cache import cache
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .providers import get_provider
from .serializers import (
    DocumentUploadSerializer,
    KYCDocumentResponseSerializer,
    KYCStatusResponseSerializer,
    KYCSubmitSerializer,
)
from .services import KYCService, KYCServiceError

logger = logging.getLogger(__name__)

BANKS_CACHE_KEY = 'kyc:banks_list'
BANKS_CACHE_TTL = 60 * 60 * 24  # 24 hours


class KYCSubmitView(APIView):
    """POST /api/v1/kyc/submit/"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = KYCSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            KYCService.submit(
                user=request.user,
                payload=serializer.validated_data,
            )
        except KYCServiceError as e:
            msg = str(e)
            code = 'NIN_ALREADY_VERIFIED' if 'already verified' in msg.lower() else 'VALIDATION_ERROR'
            http_status = (
                status.HTTP_400_BAD_REQUEST
                if code == 'NIN_ALREADY_VERIFIED'
                else status.HTTP_400_BAD_REQUEST
            )
            return Response(
                {'error': True, 'code': code, 'message': msg},
                status=http_status,
            )

        status_data = KYCService.get_status(request.user)
        return Response(
            {'success': True, 'data': KYCStatusResponseSerializer(status_data).data},
            status=status.HTTP_200_OK,
        )


class KYCStatusView(APIView):
    """GET /api/v1/kyc/status/"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status_data = KYCService.get_status(request.user)
        return Response(
            {'success': True, 'data': KYCStatusResponseSerializer(status_data).data},
            status=status.HTTP_200_OK,
        )


class UploadDocumentView(APIView):
    """POST /api/v1/kyc/upload-document/"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            doc = KYCService.upload_document(
                user=request.user,
                file=serializer.validated_data['file'],
                document_type=serializer.validated_data['document_type'],
            )
        except KYCServiceError as e:
            return Response(
                {'error': True, 'code': 'INVALID_DOCUMENT', 'message': str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {'success': True, 'data': KYCDocumentResponseSerializer(doc).data},
            status=status.HTTP_201_CREATED,
        )


class BanksListView(APIView):
    """
    GET /api/v1/kyc/banks/

    Bank list. Cached for 24 hours.
    Same endpoint also exposed at /api/v1/withdrawals/banks/.
    """
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
                    {
                        'error': True,
                        'code': 'PROVIDER_ERROR',
                        'message': 'Unable to fetch banks list. Please try again.',
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response(
            {'success': True, 'data': {'banks': banks}},
            status=status.HTTP_200_OK,
        )


class ResolveBankDeprecatedView(APIView):
    """
    POST /api/v1/kyc/resolve-bank/

    DEPRECATED. Bank resolution + verification has moved to the withdrawals app.
    Returns 410 Gone with a pointer to the new endpoint.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        return Response(
            {
                'error': True,
                'code': 'ENDPOINT_DEPRECATED',
                'message': (
                    'Bank verification moved to withdrawal time. '
                    'Use POST /api/v1/withdrawals/saved-accounts/ instead.'
                ),
            },
            status=status.HTTP_410_GONE,
        )