from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Client, ClientClassification, ClientMacAddress


def _schedule_config_rerender():
    from openwisp_controller.config.models import Config

    def _rerender():
        for config in Config.objects.all().iterator(chunk_size=200):
            config.update_status_if_checksum_changed()

    transaction.on_commit(_rerender)


@receiver(post_save, sender=Client)
@receiver(post_delete, sender=Client)
def _client_changed(*args, **kwargs):
    _schedule_config_rerender()


@receiver(post_save, sender=ClientMacAddress)
@receiver(post_delete, sender=ClientMacAddress)
def _client_mac_changed(*args, **kwargs):
    _schedule_config_rerender()
