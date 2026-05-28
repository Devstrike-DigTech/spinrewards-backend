"""
Admin dashboard URL routes.

All prefixed with /api/v1/admin/ in config/urls.py.
"""
from django.urls import path

from apps.admin_panel.auth_views import AdminChangePasswordView, AdminLoginView, AdminLogoutView, AdminMeView


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

from .challenge_views import (
    AdminChallengeCompletionsView,
    AdminChallengeDetailView,
    AdminChallengeListCreateView,
    AdminChallengeParticipantsView,
)
from .auth_views import (
    AdminChangePasswordView,
    AdminLoginView,
    AdminLogoutView,
    AdminMeView,
)

from .admin_mgmt_views import (
    AdminDetailView,
    AdminListCreateView,
    AdminResetPasswordView,
    AdminRolesView,
)
from .referral_views import AdminReferralsView
 

urlpatterns = [
     #Auth — email + password login (no token required)
    path('auth/login/', AdminLoginView.as_view(), name='admin-login'),
    path('auth/logout/', AdminLogoutView.as_view(), name='admin-logout'),
    path('auth/me/', AdminMeView.as_view(), name='admin-me'),
    path('auth/change-password/', AdminChangePasswordView.as_view(), name='admin-change-password'),

    # Admin management (super_admin only)
    path('roles/',                              AdminRolesView.as_view(),         name='admin-roles'),
    path('admins/',                             AdminListCreateView.as_view(),    name='admin-admins-list-create'),
    path('admins/<uuid:admin_id>/',             AdminDetailView.as_view(),        name='admin-admins-detail'),
    path('admins/<uuid:admin_id>/reset-password/', AdminResetPasswordView.as_view(), name='admin-admins-reset-password'),
 
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
    # Screen 9 — Challenges
    path('challenges/', AdminChallengeListCreateView.as_view(), name='admin-challenges-list-create'),
    path('challenges/<uuid:challenge_id>/', AdminChallengeDetailView.as_view(), name='admin-challenges-detail'),
    path('challenges/<uuid:challenge_id>/participants/', AdminChallengeParticipantsView.as_view(), name='admin-challenges-participants'),
    path('challenges/<uuid:challenge_id>/completions/', AdminChallengeCompletionsView.as_view(), name='admin-challenges-completions'),

    # Screen 10 — Referrals
    path('referrals/', AdminReferralsView.as_view(), name='admin-referrals-list'),
]