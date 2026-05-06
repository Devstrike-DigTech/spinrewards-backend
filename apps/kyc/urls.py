from django.urls import path

from .views import (
    BanksListView,
    KYCStatusView,
    KYCSubmitView,
    ResolveBankView,
    UploadDocumentView,
)

urlpatterns = [
    path('submit/', KYCSubmitView.as_view(), name='kyc-submit'),
    path('resolve-bank/', ResolveBankView.as_view(), name='kyc-resolve-bank'),
    path('upload-document/', UploadDocumentView.as_view(), name='kyc-upload-document'),
    path('status/', KYCStatusView.as_view(), name='kyc-status'),
    path('banks/', BanksListView.as_view(), name='kyc-banks'),
]