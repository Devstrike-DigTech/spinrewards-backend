# from django.urls import path
# from .views import WithdrawalView, WithdrawalListView, WithdrawalDetailView

# urlpatterns = [
#     path('', WithdrawalView.as_view(), name='withdrawal-create'),
#     path('list/', WithdrawalListView.as_view(), name='withdrawal-list'),
#     path('<uuid:pk>/', WithdrawalDetailView.as_view(), name='withdrawal-detail'),
# ]

from django.urls import path

from .views import (
    WithdrawalCancelView,
    WithdrawalDetailView,
    WithdrawalLimitsView,
    WithdrawalListView,
    WithdrawalRequestManualReviewView,
    WithdrawalRequestView,
)

from .bank_views import (
    SavedAccountDetailView,
    SavedAccountSetDefaultView,
    SavedAccountsView,
    WithdrawalBanksListView,
)

urlpatterns = [
    # Tiered flow (auto under threshold, manual above)
    path('', WithdrawalRequestView.as_view(), name='withdrawal-request'),

    # Forced manual review flow (always admin approval)
    path('manual-review/', WithdrawalRequestManualReviewView.as_view(),
         name='withdrawal-request-manual-review'),

    # Read endpoints
    path('list/', WithdrawalListView.as_view(), name='withdrawal-list'),
    path('limits/', WithdrawalLimitsView.as_view(), name='withdrawal-limits'),

    # Per-withdrawal endpoints
    path('<uuid:withdrawal_id>/', WithdrawalDetailView.as_view(),
         name='withdrawal-detail'),
    path('<uuid:withdrawal_id>/cancel/', WithdrawalCancelView.as_view(),
         name='withdrawal-cancel'),

      # Bank list (mirror of /kyc/banks/)
    path('banks/', WithdrawalBanksListView.as_view(), name='withdrawal-banks'),

    # Saved accounts
    path('saved-accounts/',SavedAccountsView.as_view(), name='saved-accounts-list-create'),
    path('saved-accounts/<uuid:account_id>/',SavedAccountDetailView.as_view(), name='saved-accounts-detail'),
    path('saved-accounts/<uuid:account_id>/set-default/', SavedAccountSetDefaultView.as_view(), name='saved-accounts-set-default'),
]