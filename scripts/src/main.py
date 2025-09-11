import asyncio
import json
import logging
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import lights_app, mqtt

class AppManager:
    """Main application manager for the Scripts add-on."""

    def __init__(self):
        self.addon_config = self._load_addon_config()
        self.logger = self._setup_logging()
        self.apps = []
        self._shutdown_event = asyncio.Event()

    def _load_addon_config(self) -> dict:
        """Load add-on configuration from Home Assistant."""
        options_file = "/data/options.json"
        if os.path.exists(options_file):
            with open(options_file, "r") as f:
                return json.load(f)

        # Fallback for development/testing - check for local config file
        local_config_file = "options.local.json"
        if os.path.exists(local_config_file):
            with open(local_config_file, "r") as f:
                return json.load(f)

        # No configuration found
        raise FileNotFoundError("No configuration found. Expected /data/options.json or options.local.json")

    def _setup_logging(self) -> logging.Logger:
        """Set up logging with the configured level."""
        log_level = self.addon_config.get("log_level", "info").upper()

        # Configure root logger
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)]
        )

        return logging.getLogger("scripts")

    async def initialize(self):
        """Initialize all enabled apps."""
        self.logger.info("Starting Scripts add-on")

        # Set up signal handlers for graceful shutdown
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, self._signal_handler)

        # Initialize enabled apps
        for app_config in self.addon_config.get("apps", []):
            if not app_config.get("enabled", True):
                self.logger.info(f"Skipping disabled app: {app_config['name']}")
                continue

            app_name = app_config["name"]
            self.logger.info(f"Initializing app: {app_name}")

            try:
                app = await self._create_app(app_name, app_config)
                if app:
                    self.apps.append(app)
                    self.logger.info(f"Successfully initialized app: {app_name}")
            except Exception as e:
                self.logger.error(f"Failed to initialize app {app_name}: {e}")
                # Continue with other apps rather than failing completely

        # Start HTTP health endpoint
        await self._start_http_health()

    async def _create_app(self, app_name: str, app_config: dict):
        """Create and initialize an app instance."""
        if app_name == "lights":
            app = lights_app.LightsApp(
                logger=logging.getLogger(f"scripts.{app_name}"),
                addon_config=self.addon_config,
                app_config=app_config
            )
            await app.initialize()
            return app
        else:
            self.logger.warning(f"Unknown app type: {app_name}")
            return None

    def _signal_handler(self, signum, _):
        """Handle shutdown signals."""
        if self._shutdown_event.is_set():
            self.logger.warning(
                f"Received signal {signum} during shutdown; forcing immediate exit"
            )
            os._exit(128 + int(signum))
        else:
            self.logger.info(f"Received signal {signum}, initiating shutdown...")
            self._shutdown_event.set()

            if self._health_server is not None:
                self._health_server.shutdown()

    async def run(self):
        """Run the application manager."""
        await self.initialize()

        if not self.apps:
            self.logger.error("No apps initialized successfully, exiting")
            return

        self.logger.info(f"Running with {len(self.apps)} active apps")

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        self.logger.info("Shutting down...")
        # Apps are designed to clean up automatically when the event loop stops

    async def health_check(self):
        """Perform health checks on running apps."""
        # This could be extended to check app health
        return len(self.apps) > 0

    async def _start_http_health(self) -> None:
        """Expose a minimal HTTP endpoint using stdlib http.server on 0.0.0.0:8787."""
        manager = self

        class HealthHandler(BaseHTTPRequestHandler):
            def do_GET(self):  # type: ignore[override]
                status_code, body = manager._compute_health_sync()
                self.send_response(status_code)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, format, *args):  # noqa: A003
                # Suppress default access logs to keep logs clean
                return

        try:
            self._health_server = HTTPServer(("0.0.0.0", 8787), HealthHandler)
            thread = threading.Thread(target=self._health_server.serve_forever, daemon=True)
            thread.start()
            self.logger.info("HTTP health endpoint listening on 0.0.0.0:8787")
        except Exception as e:
            self.logger.error(f"Failed to start HTTP health endpoint: {e}")

    def _compute_health_sync(self) -> tuple[int, str]:
        """Return (status_code, body) for health endpoint (sync)."""
        try:
            apps_ok = len(self.apps) > 0
            try:
                mqtt_client = mqtt.MqttClient(self.logger, self.addon_config)
                mqtt_ok = mqtt_client.is_connected
            except Exception:
                mqtt_ok = False

            healthy = apps_ok and mqtt_ok
            return (200, "connected\n") if healthy else (500, "disconnected\n")
        except Exception as e:
            self.logger.debug(f"Health computation error: {e}")
            return (500, "disconnected\n")


async def main():
    """Main entry point."""
    manager = AppManager()

    try:
        await manager.run()
    except KeyboardInterrupt:
        manager.logger.info("Received keyboard interrupt")
    except Exception as e:
        manager.logger.error(f"Unexpected error: {e}")
        sys.exit(1)
    finally:
        manager.logger.info("Scripts add-on stopped")


if __name__ == "__main__":
    asyncio.run(main())
