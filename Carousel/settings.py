"""
Django settings for Carousel project.

Generated by 'django-admin startproject' using Django 5.0.4.

For more information on this file, see
https://docs.djangoproject.com/en/5.0/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.0/ref/settings/
"""

import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
from django.utils.translation import gettext_lazy as _


# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-=b*7t+5=y6&yoq&^7a+^6dld7l+cgt=*md%_50)o7czvwt9r2t'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    
    'CRE.apps.CreConfig',   # CRE App
    'notifications.apps.NotificationsConfig',     # notifications App
    'analytics.apps.AnalyticsConfig',       # analytics App
    'subscriptions.apps.SubscriptionsConfig',
    'payments.apps.PaymentsConfig',

    # django-allauth
    'allauth',
    'allauth.account',

    # Optional -- requires install using `django-allauth[socialacocunt]`.
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',

    # Third Party
    'rest_framework',
    'rest_framework.authtoken', # required since we'll use TokenAuthentication instead of Django's default SessionAuthentication
    'channels',
    'adrf',
    
    'dj_rest_auth',
    'dj_rest_auth.registration',

    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # django-allauth account middleware:
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = 'Carousel.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

AUTHENTICATION_BACKENDS = [
    # Needed to login by username in Django admin, regardless of `allauth`
    'django.contrib.auth.backends.ModelBackend',

    'CRE.backends.emailUserPhoneAuthBackend.CustomAuthBackend',  # Backend for Auth by email|phone|
    'allauth.account.auth_backends.AuthenticationBackend', # `allauth` specific authentication methods, such as login by email
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        # 'rest_framework.authentication.TokenAuthentication',
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        # Add other authentication classes as needed
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

REST_USE_JWT = True  # Enable JWT support for dj-rest-auth

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'AUTH_COOKIE': '__cess_token',  # Name of the access token cookie
    'AUTH_COOKIE_SECURE': False,  # Set to True in production (requires HTTPS)
    'AUTH_COOKIE_HTTP_ONLY': True,  # Prevent JavaScript access
    'AUTH_COOKIE_PATH': '/',  # Global path for the cookie
    'AUTH_COOKIE_SAMESITE': 'Lax',  # CSRF protection
    'REFRESH_COOKIE': '__fresh_token',  # Name of the refresh token cookie
    'REFRESH_COOKIE_PATH': '/',  # Global path for refresh cookie
    'REFRESH_COOKIE_SECURE': False,  # Set to True in production
    'REFRESH_COOKIE_HTTP_ONLY': True,  # Prevent JavaScript access
    'REFRESH_COOKIE_SAMESITE': 'Lax',
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

WSGI_APPLICATION = 'Carousel.wsgi.application'
# Load environment variables from .env file
try:
    load_dotenv()
except UnicodeDecodeError as e:
    print(f"Error loading .env file: {e}")


# Database
# https://docs.djangoproject.com/en/5.0/ref/settings/#databases
# Grant OWNER permission to user on DB 
# Error: MigrationSchemaMissing: Unable to create the django_migrations table => ALTER DATABASE db OWNER TO db_user;
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST'),
        'PORT': os.getenv('DB_PORT'),
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [os.getenv('REDIS_URL')],
        },
    },
}

# Celery configuration
CELERY_BROKER_URL = os.getenv('REDIS_URL')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL')

# Password validation
# https://docs.djangoproject.com/en/5.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

SITE_ID = 1


# Internationalization
# https://docs.djangoproject.com/en/5.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True

AUTH_USER_MODEL = 'CRE.CustomUser'

LANGUAGES = [
    ('en', _('English')),
    ('fr', _('French')),
    # Add more languages as needed
]

LANGUAGE_CODE = 'en'

LOCALE_PATHS = [
    os.path.join(BASE_DIR, 'locale'),
]


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.0/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# SMTP Settings
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv('SMTP_SERVER')                   # smtp-relay.sendinblue.com
EMAIL_USE_TLS = False                               # False
EMAIL_PORT = os.getenv('SMTP_PORT')                 # 587
EMAIL_HOST_USER = os.getenv('SMTP_LOGIN')               # your email address
EMAIL_HOST_PASSWORD = os.getenv('SMTP_PASSWORD')       # your password
DEFAULT_FROM_EMAIL = os.getenv('SMTP_LOGIN')  # email ending with @sendinblue.com

# print(f"EMAIL_HOST: {EMAIL_HOST}, EMAIL_PORT: {EMAIL_PORT}, EMAIL_HOST_USER: {EMAIL_HOST_USER}, EMAIL_HOST_PASSWORD: {EMAIL_HOST_PASSWORD}")

# django-allauth
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = "mandatory" # make email verification mandatory on sign-up

# <EMAIL_CONFIRM_REDIRECT_BASE_URL>/<key>
EMAIL_CONFIRM_REDIRECT_BASE_URL = \
    "http://localhost:8000/email/confirm/"

# <PASSWORD_RESET_CONFIRM_REDIRECT_BASE_URL>/<uidb64>/<token>/
PASSWORD_RESET_CONFIRM_REDIRECT_BASE_URL = \
    "http://localhost:8000/password/reset/confirm/"

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.getenv('GOOGLE_CLIENT_ID'),  
            "secret": os.getenv('GOOGLE_CLIENT_SECRET'),        
            "key": "",                               
        },
        "SCOPE": [
            "profile",
            "email",
        ],
        "AUTH_PARAMS": {
            "access_type": "online",
        },
        "VERIFIED_EMAIL": True,
    },
}
