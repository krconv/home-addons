from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

from openwisp_utils.admin_theme.menu import register_menu_group


class OpenWispClientsConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "openwisp_clients"
    verbose_name = "Clients"

    def ready(self):
        self.register_menu_groups()
        self.register_config_context()
        self.register_signals()

    def register_menu_groups(self):
        register_menu_group(
            position=95,
            config={
                "label": _("Clients"),
                "items": {
                    1: {
                        "label": _("Clients"),
                        "model": "openwisp_clients.Client",
                        "name": "changelist",
                        "icon": "ow-user-and-org",
                    },
                    2: {
                        "label": _("Client classifications"),
                        "model": "openwisp_clients.ClientClassification",
                        "name": "changelist",
                        "icon": "ow-category",
                    },
                },
                "icon": "ow-user-and-org",
            },
        )

    def register_config_context(self):
        from openwisp_controller.config.models import Config

        from .context import get_clients_config_context

        Config.register_context_function(get_clients_config_context)

    def register_signals(self):
        from . import signals  # noqa: F401
