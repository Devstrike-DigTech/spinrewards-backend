# from .base import *
# from decouple import config

# DEBUG = False

# ALLOWED_HOSTS = config('DJANGO_ALLOWED_HOSTS', default='').split(',')

# # ─── CORS ─────────────────────────────────────────────────────────────────────
# CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='').split(',')
# CORS_ALLOW_CREDENTIALS = True
# CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='').split(',')
# # CSRF_TRUSTED_ORIGINS = ['https://dev-api.spinrewardsgame.com', 'https://www.dev-api.spinrewardsgame.com',]

# # ─── Security ─────────────────────────────────────────────────────────────────
# SECURE_SSL_REDIRECT = True
# SECURE_HSTS_SECONDS = 31536000
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True
# SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True

# # ─── Logging ──────────────────────────────────────────────────────────────────
# LOGGING = {
#     'version': 1,
#     'disable_existing_loggers': False,
#     'formatters': {
#         'verbose': {
#             'format': '[{levelname}] {asctime} {module} {process:d} {thread:d}: {message}',
#             'style': '{',
#         },
#     },
#     'handlers': {
#         'console': {
#             'class': 'logging.StreamHandler',
#             'formatter': 'verbose',
#         },
#     },
#     'root': {
#         'handlers': ['console'],
#         'level': 'INFO',
#     },
#     'loggers': {
#         'django': {
#             'handlers': ['console'],
#             'level': 'WARNING',
#             'propagate': False,
#         },
#         'apps': {
#             'handlers': ['console'],
#             'level': 'INFO',
#             'propagate': False,
#         },
#     },
# }

# STATIC_ROOT = '/tmp/staticfiles'  # removed: /tmp is ephemeral in Railway

from .base import *
from decouple import config

DEBUG = False

# Fix ALLOWED_HOSTS parsing
ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    'spinrewardsgame.com',
    'dev-api.spinrewardsgame.com',
    'admin.spinrewardsgame.com',
    'plastery-unhampered-erline.ngrok-free.dev',
]

# Or parse properly:
# ALLOWED_HOSTS = [host.strip() for host in config('DJANGO_ALLOWED_HOSTS', default='').split(',') if host.strip()]

# ─── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'http://localhost:3000',
    'https://app.spinrewardsgame.com',
    'https://admin.spinrewardsgame.com',
    'https://dev-api.spinrewardsgame.com',
    'https://spinrewardsgame.com',
    'https://plastery-unhampered-erline.ngrok-free.dev/'
]
CORS_ALLOW_CREDENTIALS = True

# CSRF Trusted Origins - CRITICAL FOR ADMIN
CSRF_TRUSTED_ORIGINS = [
    'https://dev-api.spinrewardsgame.com',
    'https://spinrewardsgame.com',
    'https://admin.spinrewardsgame.com',
    'https://plastery-unhampered-erline.ngrok-free.dev/'
]

# ─── Security ─────────────────────────────────────────────────────────────────
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Additional CSRF settings for better compatibility
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SAMESITE = 'Lax'

# ─── Logging (keep as is) ─────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {module} {process:d} {thread:d}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.security.csrf': {  # Add this to debug CSRF issues
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}

STATIC_ROOT = BASE_DIR / 'staticfiles'