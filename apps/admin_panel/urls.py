# from django.urls import path

# from .views import (
#     # Dashboard
#     AdminDevLoginView,
#     DashboardView,
#     FinancialsView,

#     # RTP
#     RTPListCreateView,
#     RTPDetailView,

#     # Users
#     AdminUserListView,
#     AdminUserDetailView,
#     AdminUserSpinsView,
#     AdminUserTransactionsView,

#     # Withdrawals
#     AdminWithdrawalListView,
#     AdminWithdrawalApproveView,
#     AdminWithdrawalRejectView,

#     # KYC
#     AdminKYCQueueView,
#     AdminKYCApproveView,
#     AdminKYCRejectView,

#     # Fraud
#     AdminFraudMonitorView,

#     # Audit Logs
#     AdminAuditLogsView,
# )

# urlpatterns = [
#     # ─── Screen 1: Dashboard ─────────────────────────────
#     path("dashboard/", DashboardView.as_view(), name="admin-dashboard"),

#     # ─── Screen 2: Financials ────────────────────────────
#     path("financials/", FinancialsView.as_view(), name="admin-financials"),

#     # ─── Screen 3: RTP Control ───────────────────────────
#     path("rtp/", RTPListCreateView.as_view(), name="admin-rtp-list-create"),
#     path("rtp/<uuid:wheel_id>/", RTPDetailView.as_view(), name="admin-rtp-detail"),

#     # ─── Screen 4: Users ─────────────────────────────────
#     path("users/", AdminUserListView.as_view(), name="admin-users"),
#     path("users/<int:user_id>/", AdminUserDetailView.as_view(), name="admin-user-detail"),
#     path("users/<int:user_id>/spins/", AdminUserSpinsView.as_view(), name="admin-user-spins"),
#     path("users/<int:user_id>/transactions/", AdminUserTransactionsView.as_view(), name="admin-user-transactions"),

#     # ─── Screen 5: Withdrawals ───────────────────────────
#     path("withdrawals/", AdminWithdrawalListView.as_view(), name="admin-withdrawals"),
#     path("withdrawals/<uuid:withdrawal_id>/approve/", AdminWithdrawalApproveView.as_view(), name="admin-withdrawal-approve"),
#     path("withdrawals/<uuid:withdrawal_id>/reject/", AdminWithdrawalRejectView.as_view(), name="admin-withdrawal-reject"),

#     # ─── Screen 6: KYC ───────────────────────────────────
#     path("kyc/queue/", AdminKYCQueueView.as_view(), name="admin-kyc-queue"),
#     path("kyc/<uuid:kyc_id>/approve/", AdminKYCApproveView.as_view(), name="admin-kyc-approve"),
#     path("kyc/<uuid:kyc_id>/reject/", AdminKYCRejectView.as_view(), name="admin-kyc-reject"),

#     # ─── Screen 7: Fraud ─────────────────────────────────
#     path("fraud/", AdminFraudMonitorView.as_view(), name="admin-fraud"),

#     # ─── Screen 8: Audit Logs ────────────────────────────
#     path("audit-logs/", AdminAuditLogsView.as_view(), name="admin-audit-logs"),
#     path("auth/admin-dev-login/", AdminDevLoginView.as_view(), name="admin-dev-login"),
# ]