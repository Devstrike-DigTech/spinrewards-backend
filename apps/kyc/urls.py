from django.urls import path
from .views import KYCSubmitView, KYCStatusView, KYCDocumentView

urlpatterns = [
    path('', KYCSubmitView.as_view(), name='kyc-submit'),
    path('status/', KYCStatusView.as_view(), name='kyc-status'),
    path('document/', KYCDocumentView.as_view(), name='kyc-document'),
]
