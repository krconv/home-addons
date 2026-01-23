from django.db import migrations, models
import django.db.models.deletion

import openwisp_clients.models


def migrate_client_macs(apps, schema_editor):
    Client = apps.get_model("openwisp_clients", "Client")
    ClientMacAddress = apps.get_model("openwisp_clients", "ClientMacAddress")
    for client in Client.objects.all().iterator():
        mac = getattr(client, "mac_address", None)
        if mac is None:
            continue
        ClientMacAddress.objects.create(client=client, mac_address=mac)


class Migration(migrations.Migration):
    dependencies = [
        ("openwisp_clients", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientMacAddress",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "client",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mac_addresses", to="openwisp_clients.client"),
                ),
                (
                    "mac_address",
                    models.CharField(blank=True, default="", max_length=17, unique=True, validators=[openwisp_clients.models.validate_mac]),
                ),
            ],
            options={
                "verbose_name": "Client MAC address",
                "verbose_name_plural": "Client MAC addresses",
            },
        ),
        migrations.AlterField(
            model_name="client",
            name="psk",
            field=models.CharField(
                blank=True,
                default="",
                max_length=64,
                validators=[openwisp_clients.models.validate_psk],
            ),
        ),
        migrations.RunPython(migrate_client_macs, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="client",
            name="mac_address",
        ),
    ]
