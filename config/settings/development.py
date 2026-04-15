from .base import *

DEBUG = True

ALLOWED_HOSTS = ['*']

# Allow all origins in development
CORS_ALLOW_ALL_ORIGINS = True

# Show emails in console during development
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# Easier to debug
CELERY_TASK_ALWAYS_EAGER = False  # Set True to run tasks synchronously in dev if needed

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {module}: {message}',
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
        'level': 'DEBUG',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'WARNING',  # Change to DEBUG to log all SQL queries
            'propagate': False,
        },
    },
}
