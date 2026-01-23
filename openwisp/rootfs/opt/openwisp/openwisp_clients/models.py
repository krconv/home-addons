import re

from django.core.exceptions import ValidationError
from django.db import models
from swapper import get_model_name


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,255}$)([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\\.([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def validate_hostname(value):
    if not _HOSTNAME_RE.match(value):
        raise ValidationError("Enter a valid hostname (RFC 1123).")


def validate_mac(value):
    if value in ("", None):
        return
    if not _MAC_RE.match(value):
        raise ValidationError("Enter a valid MAC address (AA:BB:CC:DD:EE:FF).")


def validate_psk(value):
    if value in ("", None):
        return
    if len(value) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        return
    if 8 <= len(value) <= 63:
        return
    raise ValidationError("PSK must be 8-63 characters or 64 hex characters.")


class ClientClassification(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = "Client classification"
        verbose_name_plural = "Client classifications"

    def __str__(self):
        return self.name


class ClientClassificationSubnet(models.Model):
    classification = models.ForeignKey(
        ClientClassification, on_delete=models.CASCADE, related_name="subnet_links"
    )
    subnet = models.OneToOneField(
        get_model_name("openwisp_ipam", "Subnet"),
        on_delete=models.CASCADE,
        related_name="client_classification_link",
    )

    class Meta:
        verbose_name = "Client classification subnet"
        verbose_name_plural = "Client classification subnets"

    def __str__(self):
        return f"{self.classification} -> {self.subnet}"


class Client(models.Model):
    name = models.CharField(max_length=255, unique=True, validators=[validate_hostname])
    psk = models.CharField(
        max_length=64,
        blank=True,
        default="",
        validators=[validate_psk],
    )
    classification = models.ForeignKey(
        ClientClassification, on_delete=models.PROTECT, related_name="clients"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"

    def __str__(self):
        return self.name


class ClientMacAddress(models.Model):
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="mac_addresses"
    )
    mac_address = models.CharField(
        max_length=17,
        unique=True,
        blank=True,
        default="",
        validators=[validate_mac],
    )

    class Meta:
        verbose_name = "Client MAC address"
        verbose_name_plural = "Client MAC addresses"

    def __str__(self):
        return f"{self.client} - {self.mac_address or 'blank'}"
