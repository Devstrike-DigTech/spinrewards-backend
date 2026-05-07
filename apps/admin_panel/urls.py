"""
Admin dashboard URL routes.

All prefixed with /api/v1/admin/ in config/urls.py.
"""
from django.urls import path

from .views import (
    AdminAuditLogsView,
    AdminFraudMonitorView,
    AdminKYCApproveView,
    AdminKYCQueueView,
    AdminKYCRejectView,
    AdminUserDetailView,
    AdminUserSpinsView,
    AdminUserTransactionsView,
    AdminUsersListView,
    AdminWithdrawalApproveView,
    AdminWithdrawalRejectView,
    AdminWithdrawalsListView,
    DashboardView,
    FinancialsView,
    RTPControlDetailView,
    RTPControlListCreateView,
)

urlpatterns = [
    # Screen 1 — Dashboard
    path('dashboard/', DashboardView.as_view(), name='admin-dashboard'),

    # Screen 2 — Financials
    path('financials/', FinancialsView.as_view(), name='admin-financials'),

    # Screen 3 — RTP Control
    path('rtp/', RTPControlListCreateView.as_view(), name='admin-rtp-list-create'),
    path('rtp/<uuid:wheel_id>/', RTPControlDetailView.as_view(), name='admin-rtp-detail'),

    # Screen 4 — Users
    path('users/', AdminUsersListView.as_view(), name='admin-users-list'),
    path('users/<uuid:user_id>/', AdminUserDetailView.as_view(), name='admin-user-detail'),
    path('users/<uuid:user_id>/spins/', AdminUserSpinsView.as_view(), name='admin-user-spins'),
    path('users/<uuid:user_id>/transactions/', AdminUserTransactionsView.as_view(), name='admin-user-transactions'),

    # Screen 5 — Withdrawal Management
    path('withdrawals/', AdminWithdrawalsListView.as_view(), name='admin-withdrawals-list'),
    path('withdrawals/<uuid:withdrawal_id>/approve/', AdminWithdrawalApproveView.as_view(), name='admin-withdrawal-approve'),
    path('withdrawals/<uuid:withdrawal_id>/reject/', AdminWithdrawalRejectView.as_view(), name='admin-withdrawal-reject'),

    # Screen 6 — KYC Review Queue
    path('kyc/queue/', AdminKYCQueueView.as_view(), name='admin-kyc-queue'),
    path('kyc/<uuid:kyc_id>/approve/', AdminKYCApproveView.as_view(), name='admin-kyc-approve'),
    path('kyc/<uuid:kyc_id>/reject/', AdminKYCRejectView.as_view(), name='admin-kyc-reject'),

    # Screen 7 — Fraud & Risk Monitor
    path('fraud/', AdminFraudMonitorView.as_view(), name='admin-fraud-monitor'),

    # Screen 8 — Audit Logs
    path('audit-logs/', AdminAuditLogsView.as_view(), name='admin-audit-logs'),
]