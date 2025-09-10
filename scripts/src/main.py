import asyncio
import json
import logging
import os
import signal
import sys

import lights_app

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
        self.logger.info(f"Received signal {signum}, initiating shutdown...")
        self._shutdown_event.set()

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