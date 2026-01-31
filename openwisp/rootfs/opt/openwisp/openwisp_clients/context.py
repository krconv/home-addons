from django.apps import apps


def get_clients_config_context(config=None):
    Client = apps.get_model("openwisp_clients", "Client")

    clients = []
    queryset = Client.objects.select_related("classification").prefetch_related(
        "mac_addresses"
    )
    for client in queryset.iterator():
        classification = None
        if client.classification_id:
            classification = {
                "id": client.classification_id,
                "name": client.classification.name,
                "description": client.classification.description,
            }
        clients.append(
            {
                "id": client.id,
                "name": client.name,
                "psk": client.psk,
                "classification": classification,
                "mac_addresses": [
                    mac.mac_address
                    for mac in client.mac_addresses.all()
                    if mac.mac_address
                ],
            }
        )

    return {"clients": clients}
