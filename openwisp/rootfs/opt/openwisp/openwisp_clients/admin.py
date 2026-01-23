from django import forms
from django.contrib import admin

from .models import (
    Client,
    ClientClassification,
    ClientClassificationSubnet,
    ClientMacAddress,
)


class ClientClassificationSubnetInline(admin.TabularInline):
    model = ClientClassificationSubnet
    extra = 0


@admin.register(ClientClassification)
class ClientClassificationAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name",)
    inlines = (ClientClassificationSubnetInline,)


class ClientAdminForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = "__all__"

    class Media:
        js = ("openwisp_clients/psk-generator.js",)


class ClientMacAddressInline(admin.TabularInline):
    model = ClientMacAddress
    extra = 1


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    form = ClientAdminForm
    list_display = ("name", "macs", "classification")
    list_filter = ("classification",)
    search_fields = ("name", "mac_addresses__mac_address")
    inlines = (ClientMacAddressInline,)

    def macs(self, obj):
        return ", ".join(
            mac.mac_address or "(blank)" for mac in obj.mac_addresses.all()
        )

    macs.short_description = "MAC addresses"
