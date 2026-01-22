from django.db import migrations, models
import django.db.models.deletion
import openwisp_clients.models
import swapper


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        swapper.dependency("openwisp_ipam", "Subnet"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientClassification",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("description", models.TextField(blank=True)),
            ],
            options={
                "verbose_name": "Client classification",
                "verbose_name_plural": "Client classifications",
            },
        ),
        migrations.CreateModel(
            name="ClientClassificationSubnet",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "classification",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subnet_links", to="openwisp_clients.clientclassification"),
                ),
                (
                    "subnet",
                    models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="client_classification_link", to=swapper.get_model_name("openwisp_ipam", "Subnet")),
                ),
            ],
            options={
                "verbose_name": "Client classification subnet",
                "verbose_name_plural": "Client classification subnets",
            },
        ),
        migrations.CreateModel(
            name="Client",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True, validators=[openwisp_clients.models.validate_hostname])),
                ("mac_address", models.CharField(max_length=17, unique=True, validators=[openwisp_clients.models.validate_mac])),
                ("psk", models.CharField(max_length=64, validators=[openwisp_clients.models.validate_psk])),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "classification",
                    models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="clients", to="openwisp_clients.clientclassification"),
                ),
            ],
            options={
                "verbose_name": "Client",
                "verbose_name_plural": "Clients",
            },
        ),
    ]
