from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('openwisp_controller.urls')),
    path('', include('openwisp_users.urls')),
]

if getattr(settings, 'USE_OPENWISP_RADIUS', False):
    urlpatterns.append(path('', include('openwisp_radius.urls')))

if getattr(settings, 'USE_OPENWISP_TOPOLOGY', False):
    urlpatterns.append(path('', include('openwisp_network_topology.urls')))

if getattr(settings, 'USE_OPENWISP_FIRMWARE', False):
    urlpatterns.append(path('', include('openwisp_firmware.urls')))

if getattr(settings, 'USE_OPENWISP_MONITORING', False):
    urlpatterns.append(path('', include('openwisp_monitoring.urls')))

# Serve static and media files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)