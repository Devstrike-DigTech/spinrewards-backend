from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework import status

from .services import KYCService
from .models import KYC
from .serializers import KYCSubmitSerializer, KYCStatusSerializer


class KYCSubmitView(APIView):
    """POST /api/v1/kyc/ — Submit KYC information."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = KYCSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        kyc = KYCService.submit(
            user=request.user,
            full_name=d['full_name'],
            nin=d['nin'],
            dob=d['dob'],
            bank_account=d['bank_account'],
            bank_code=d['bank_code'],
        )

        return Response({
            'success': True,
            'data': {
                'status': kyc.status,
                'account_name': kyc.account_name,
                'submitted_at': kyc.submitted_at,
                'message': 'Your KYC is under review. We\'ll notify you when approved.',
            }
        }, status=status.HTTP_201_CREATED)


class KYCStatusView(APIView):
    """GET /api/v1/kyc/status/ — Get KYC status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        kyc = getattr(request.user, 'kyc', None)
        if not kyc:
            return Response({'success': True, 'data': {'status': 'unverified'}})
        return Response({
            'success': True,
            'data': KYCStatusSerializer(kyc).data,
        })


class KYCDocumentView(APIView):
    """POST /api/v1/kyc/document/ — Upload KYC document."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]

    def post(self, request):
        document = request.FILES.get('document')
        document_type = request.data.get('document_type', '')

        if not document:
            return Response(
                {'error': True, 'code': 'VALIDATION_ERROR', 'message': 'No document provided.'},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        kyc = getattr(request.user, 'kyc', None)
        if not kyc:
            return Response(
                {'error': True, 'code': 'NOT_FOUND', 'message': 'Submit KYC information first.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # TODO: Upload to cloud storage (S3/Cloudinary) and save URL
        # For now, save to MEDIA_ROOT
        from django.core.files.storage import default_storage
        path = default_storage.save(f'kyc/{request.user.id}/{document.name}', document)
        kyc.document_url = path
        kyc.document_type = document_type
        kyc.save(update_fields=['document_url', 'document_type'])

        return Response({
            'success': True,
            'data': {'document_uploaded': True, 'document_type': document_type},
        })
