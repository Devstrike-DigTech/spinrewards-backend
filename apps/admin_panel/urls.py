# from django.urls import path
# from .views import (
#     AdminRTPTierListView,
#     AdminRTPTierDetailView,
#     AdminUserListView,
#     AdminUserDetailView,
#     AdminKYCListView,
#     AdminKYCApproveView,
#     AdminKYCRejectView,
#     AdminWithdrawalListView,
#     AdminWithdrawalApproveView,
#     AdminWithdrawalRejectView,
#     AdminAnalyticsView,
#     AdminAuditLogListView,
# )

# urlpatterns = [
#     # RTP Config
#     path('rtp/tiers/', AdminRTPTierListView.as_view(), name='admin-rtp-tiers'),
#     path('rtp/tiers/<uuid:pk>/', AdminRTPTierDetailView.as_view(), name='admin-rtp-tier-detail'),

#     # Users
#     path('users/', AdminUserListView.as_view(), name='admin-users'),
#     path('users/<uuid:pk>/', AdminUserDetailView.as_view(), name='admin-user-detail'),

#     # KYC
#     path('kyc/', AdminKYCListView.as_view(), name='admin-kyc-list'),
#     path('kyc/<uuid:pk>/approve/', AdminKYCApproveView.as_view(), name='admin-kyc-approve'),
#     path('kyc/<uuid:pk>/reject/', AdminKYCRejectView.as_view(), name='admin-kyc-reject'),

#     # Withdrawals
#     path('withdrawals/', AdminWithdrawalListView.as_view(), name='admin-withdrawals'),
#     path('withdrawals/<uuid:pk>/approve/', AdminWithdrawalApproveView.as_view(), name='admin-withdrawal-approve'),
#     path('withdrawals/<uuid:pk>/reject/', AdminWithdrawalRejectView.as_view(), name='admin-withdrawal-reject'),

#     # Analytics
#     path('analytics/overview/', AdminAnalyticsView.as_view(), name='admin-analytics'),

#     # Audit Logs
#     path('audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-logs'),
# ]
