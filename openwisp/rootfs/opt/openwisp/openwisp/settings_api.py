from .settings_addon import *  # noqa: F401,F403

ROOT_URLCONF = "openwisp.urls_api"
LOGIN_REDIRECT_URL = "account_change_password"
ACCOUNT_LOGOUT_REDIRECT_URL = LOGIN_REDIRECT_URL
