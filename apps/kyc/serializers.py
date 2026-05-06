"""KYC serializers — request validation and response shaping."""
from rest_framework import serializers

from .models import BankAccount, KYCDocument, KYCProfile


class KYCSubmitSerializer(serializers.Serializer):
    """POST /api/v1/kyc/submit/"""
    full_name = serializers.CharField(max_length=200, required=True)
    nin = serializers.CharField(min_length=11, max_length=11, required=True)
    bvn = serializers.CharField(min_length=11, max_length=11, required=True)
    date_of_birth = serializers.DateField(required=True)
    phone_number = serializers.CharField(
        max_length=20, required=False, allow_blank=True,
    )
    bank_code = serializers.CharField(max_length=10, required=True)
    account_number = serializers.CharField(
        min_length=10, max_length=10, required=True,
    )
    document_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_nin(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('NIN must contain only digits.')
        return value

    def validate_bvn(self, value):
        if not value.isdigit():
            raise serializers.ValidationError('BVN must contain only digits.')
        return value

    def validate_account_number(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                'Account number must contain only digits.'
            )
        return value


class ResolveBankSerializer(serializers.Serializer):
    """POST /api/v1/kyc/resolve-bank/"""
    bank_code = serializers.CharField(max_length=10, required=True)
    account_number = serializers.CharField(
        min_length=10, max_length=10, required=True,
    )

    def validate_account_number(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                'Account number must contain only digits.'
            )
        return value


class DocumentUploadSerializer(serializers.Serializer):
    """POST /api/v1/kyc/upload-document/"""
    file = serializers.FileField(required=True)
    document_type = serializers.ChoiceField(
        choices=KYCDocument.DocumentType.choices,
        default=KYCDocument.DocumentType.UTILITY_BILL,
    )


class KYCDocumentResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = KYCDocument
        fields = [
            'id', 'document_type', 'original_filename',
            'file_size_bytes', 'content_type', 'status',
            'uploaded_at',
        ]


class BankAccountResponseSerializer(serializers.ModelSerializer):
    account_number_masked = serializers.SerializerMethodField()

    class Meta:
        model = BankAccount
        fields = [
            'id', 'bank_code', 'bank_name',
            'account_number_masked', 'account_name',
            'is_active', 'verified_at',
        ]

    def get_account_number_masked(self, obj) -> str:
        if not obj.account_number or len(obj.account_number) < 4:
            return '****'
        return f'****{obj.account_number[-4:]}'


class KYCStatusResponseSerializer(serializers.Serializer):
    overall_status = serializers.CharField()
    personal_info_status = serializers.CharField()
    personal_info_reason = serializers.CharField(allow_blank=True, required=False)
    bank_account_status = serializers.CharField()
    bank_account_reason = serializers.CharField(allow_blank=True, required=False)
    document_status = serializers.CharField()
    document_reason = serializers.CharField(allow_blank=True, required=False)
    can_withdraw = serializers.BooleanField()
    submitted_at = serializers.DateTimeField(allow_null=True, required=False)
    last_resubmission_at = serializers.DateTimeField(allow_null=True, required=False)