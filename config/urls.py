from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from apps.payments.views import payment_callback_page

urlpatterns = [
    path('django-admin/', admin.site.urls),
    path('payment/callback/', payment_callback_page, name='payment-callback'),

    # API v1
    path('api/v1/', include([
        path('auth/', include('apps.users.urls')),
        path('users/', include('apps.users.user_urls')),
        path('wallet/', include('apps.wallet.urls')),
        path('spin/', include('apps.spin.urls')),
        path('deposits/', include('apps.payments.urls')),
        path('withdrawals/', include('apps.withdrawals.urls')),
        path('kyc/', include('apps.kyc.urls')),
        path('referrals/', include('apps.referrals.urls')),
        path('challenges/', include('apps.challenges.urls')),
        path('webhooks/', include('apps.payments.webhook_urls')),
        path('admin/settings/', include('apps.settings_app.urls')),
        path('settings/', include('apps.settings_app.public_urls')),
        path('admin/', include('apps.admin_panel.urls')),
    ])),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
