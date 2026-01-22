from django.contrib import admin

from .models import Client, ClientClassification, ClientClassificationSubnet


class ClientClassificationSubnetInline(admin.TabularInline):
    model = ClientClassificationSubnet
    extra = 0


@admin.register(ClientClassification)
class ClientClassificationAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name",)
    inlines = (ClientClassificationSubnetInline,)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "mac_address", "classification")
    list_filter = ("classification",)
    search_fields = ("name", "mac_address")
