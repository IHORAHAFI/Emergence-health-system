"""Django settings for the AI-Powered Smart Medical Emergency System."""
import os
from datetime import timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me-before-deploying-0123456789")
DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "https://*.onrender.com").split(",")

INSTALLED_APPS = [
    "daphne",  # ASGI server so runserver also serves WebSockets
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "accounts",
    "hospitals",
    "ambulances",
    "emergency",
    "notifications",
    "ai_recommendation",
    "medical_rag",
    "patient_monitoring",
    "channels",
    "api",
]

ASGI_APPLICATION = "config.asgi.application"
# In-memory layer works for a single development process. For production / several
# processes set REDIS_URL (pip install channels-redis).
if os.getenv("REDIS_URL"):
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer",
                                  "CONFIG": {"hosts": [os.getenv("REDIS_URL")]}}}
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware", 
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "accounts.context_processors.navigation",
    ]},
}]

WSGI_APPLICATION = "config.wsgi.application"

# PostgreSQL when DB_NAME is set, otherwise SQLite for quick local development.
if os.getenv("DATABASE_URL"):  # Render / hosted Postgres
    import dj_database_url
    DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)}
elif os.getenv("DB_NAME"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }}

AUTH_USER_MODEL = "accounts.User"

TIME_ZONE = "Africa/Kigali"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/home/"
LOGOUT_REDIRECT_URL = "/"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
}
CORS_ALLOW_ALL_ORIGINS = DEBUG
# Allow the hosted Flutter web app (Render static site) to call the API.
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://[\w-]+\.onrender\.com$"]

# Notifications: keep SIMULATE=True during development (logs instead of sending).
NOTIFICATIONS_SIMULATE = os.getenv("NOTIFICATIONS_SIMULATE", "True") == "True"
FCM_SERVER_KEY = os.getenv("FCM_SERVER_KEY", "")
# SMS: simulate (default, nothing is sent) | africastalking | twilio | generic
SMS_PROVIDER = os.getenv("SMS_PROVIDER", "simulate").lower()
SMS_SENDER_ID = os.getenv("SMS_SENDER_ID", "")
AT_USERNAME = os.getenv("AT_USERNAME", "")
AT_API_KEY = os.getenv("AT_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
SMS_API_URL = os.getenv("SMS_API_URL", "")
SMS_API_KEY = os.getenv("SMS_API_KEY", "")

# A patient cannot open a second emergency within this many minutes while one is active.
DUPLICATE_WINDOW_MINUTES = int(os.getenv("DUPLICATE_WINDOW_MINUTES", "30"))
# Optional: national emergency number shown to patients.
EMERGENCY_HOTLINE = os.getenv("EMERGENCY_HOTLINE", "912")

# AI model storage
AI_MODEL_PATH = BASE_DIR / "ai_recommendation" / "priority_model.joblib"
AMBULANCE_AVG_SPEED_KMH = float(os.getenv("AMBULANCE_AVG_SPEED_KMH", "40"))


# --- Languages (interface only; medical content stays in its reviewed language) ---
from django.conf import locale as _locale  # noqa: E402
from django.utils.translation import gettext_lazy as _  # noqa: E402

_locale.LANG_INFO.setdefault("rw", {"bidi": False, "code": "rw", "name": "Kinyarwanda", "name_local": "Ikinyarwanda"})
LANGUAGES = [("en", _("English")), ("rw", _("Kinyarwanda")), ("fr", _("French"))]
LANGUAGE_CODE = "en"
USE_I18N = True
LOCALE_PATHS = [BASE_DIR / "locale"]

# AI assistant: stored conversations are deleted after this many days (python manage.py purge_assistant_history)
ASSISTANT_HISTORY_DAYS = int(os.getenv("ASSISTANT_HISTORY_DAYS", "30"))

# Operator siren: one alert per request every ALERT_INTERVAL_SECONDS until accepted; AI escalation after N alerts
ALERT_INTERVAL_SECONDS = int(os.getenv("ALERT_INTERVAL_SECONDS", "120"))
ALERT_ESCALATE_AFTER = int(os.getenv("ALERT_ESCALATE_AFTER", "3"))
# Siren file played on the operator dashboard (relative to static/)
ALERT_SIREN_FILE = os.getenv("ALERT_SIREN_FILE", "audio/police_wail_emergency_siren.mp3")
