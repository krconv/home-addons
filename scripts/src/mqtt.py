import asyncio
import datetime
import json
import logging
import os
import re
import subprocess
import tempfile
import typing

import paho.mqtt.client

from . import utils


@utils.singleton
class MqttClient:
    _lock: asyncio.Lock = asyncio.Lock()
    _is_initialized: bool = False

    def __init__(self, logger: logging.Logger, addon_config: dict):
        self.logger = logger
        self._health_file = "/run/healthchecks/mqtt"

        # Try to get MQTT config from addon options first
        if addon_config and all(key in addon_config for key in ["mqtt_host", "mqtt_username", "mqtt_password"]):
            self._broker_host = addon_config["mqtt_host"]
            self._broker_port = addon_config.get("mqtt_port", 1883)
            self._username = addon_config["mqtt_username"]
            self._password = addon_config["mqtt_password"]
        else:
            # Fall back to bashio services to get MQTT configuration
            mqtt_config = self._get_mqtt_config_from_bashio()
            self._broker_host = mqtt_config["host"]
            self._broker_port = int(mqtt_config["port"])
            self._username = mqtt_config["username"]
            self._password = mqtt_config["password"]

    async def initialize(self) -> None:
        if self._is_initialized:
            return

        async with self._lock:
            if self._is_initialized:
                return

            self._client = paho.mqtt.client.Client()
            self._callbacks: list[
                tuple[str, re.Pattern, typing.Callable[[str, str], None]]
            ] = []
            self._connected = False
            self._connect_event = asyncio.Event()

            # Set up client callbacks
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            # Set up authentication
            self._client.username_pw_set(self._username, self._password)

            self._client.connect(self._broker_host, self._broker_port, 60)
            self._client.loop_start()

            # Wait for connection to be established (timeout after 10 seconds)
            try:
                await asyncio.wait_for(self._connect_event.wait(), 10)
            except asyncio.TimeoutError:
                self.logger.error("Failed to connect to MQTT broker within timeout")
                raise ConnectionError("MQTT connection timeout")

            self._is_initialized = True

    def publish(self, topic: str, payload: dict, qos: int = 0, retain: bool = False):
        self.logger.debug(f"Publishing to MQTT topic {topic}: {payload}")

        error_code, _ = self._client.publish(topic, json.dumps(payload), qos, retain)
        if error_code != paho.mqtt.client.MQTT_ERR_SUCCESS:
            raise Exception(f"Failed to publish to MQTT topic {topic}: {paho.mqtt.client.error_string(error_code)} ({error_code})")

    def subscribe(
        self, topic: str, callback: typing.Callable[[str, str], None], qos: int = 0
    ):
        self.logger.debug(f"Subscribing to MQTT topic {topic}")
        is_already_subscribed = any(
            topic == existing_topic for existing_topic, _, _ in self._callbacks
        )

        if not is_already_subscribed:
            error_code, _ = self._client.subscribe(topic, qos)

            if error_code != paho.mqtt.client.MQTT_ERR_SUCCESS:
                raise Exception(
                    f"Failed to subscribe to MQTT topic {topic}: {paho.mqtt.client.error_string(error_code)} ({error_code})"
                )

        self._callbacks.append((topic, self._convert_topic_to_regex(topic), callback))

    def unsubscribe(self, topic: str, callback: typing.Callable[[str, str], None]):
        """Unsubscribe from MQTT topic."""
        self.logger.debug(f"Unsubscribing from MQTT topic {topic}")

        matching_callbacks = [
            c for c in self._callbacks if c[0] == topic and c[1] == callback
        ]
        is_last_subscription = not any(
            c[0] == topic and c not in matching_callbacks for c in self._callbacks
        )
        if is_last_subscription:
            error_code, _ = self._client.unsubscribe(topic)
            if error_code != paho.mqtt.client.MQTT_ERR_SUCCESS:
                raise Exception(
                    f"Failed to unsubscribe from MQTT topic {topic}: {paho.mqtt.client.error_string(error_code)} ({error_code})"
                )

        for c in matching_callbacks:
            self._callbacks.remove(c)

    def _convert_topic_to_regex(self, topic: str) -> re.Pattern:
        topic = re.escape(topic)
        topic = topic.replace(r"\+", r"[^/]+")
        topic = topic.replace(r"\#", r".+")
        return re.compile(f"^{topic}$")

    def _on_connect(self, client, userdata, flags, error_code):
        """Callback for when client connects to broker."""
        if error_code == paho.mqtt.client.MQTT_ERR_SUCCESS:
            self._connected = True
            self.logger.info("Connected to MQTT broker")
            self._connect_event.set()
        else:
            self.logger.error(f"Failed to connect to MQTT broker: {paho.mqtt.client.error_string(error_code)} ({error_code})")
            self._connect_event.set()

    def _on_disconnect(self, client, userdata, error_code):
        """Callback for when client disconnects from broker."""
        self._connected = False
        if error_code != paho.mqtt.client.MQTT_ERR_SUCCESS:
            self.logger.warning(f"Unexpected disconnection from MQTT broker: {paho.mqtt.client.error_string(error_code)} ({error_code})")
        else:
            self.logger.info("Disconnected from MQTT broker")
        

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        self.logger.debug(f"Received MQTT message on {topic}: {payload}")

        for _, pattern, callback in self._callbacks:
            if pattern.match(topic):
                try:
                    callback(topic, payload)
                except Exception as e:
                    self.logger.error(f"Error in callback for topic {topic}: {e}")

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to broker."""
        return self._connected

    def _get_mqtt_config_from_bashio(self) -> dict:
        """When running in Home Assistant, we can query the Addon API for MQTT credentials"""
        script_content = """#!/usr/bin/with-contenv bashio
set -euo pipefail

# Get MQTT configuration using bashio services and output as JSON
MQTT_HOST=$(bashio::services mqtt "host")
MQTT_PORT=$(bashio::services mqtt "port")
MQTT_USER=$(bashio::services mqtt "username")
MQTT_PASSWORD=$(bashio::services mqtt "password")

# Basic validation to ensure discovery succeeded
if [ -z "${MQTT_HOST:-}" ] || [ -z "${MQTT_PORT:-}" ] || [ -z "${MQTT_USER:-}" ]; then
  bashio::log.error "Failed to retrieve MQTT service configuration via bashio"
  exit 1
fi

# Output as JSON to stdout
cat << EOF
{
    "host": "$MQTT_HOST",
    "port": $MQTT_PORT,
    "username": "$MQTT_USER",
    "password": "$MQTT_PASSWORD"
}
EOF"""

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".sh", delete=False
            ) as temp_file:
                temp_file.write(script_content)
                temp_file.close()
                script_path = temp_file.name
                os.chmod(script_path, 0o755)

                try:
                    result = subprocess.run(
                        [script_path], capture_output=True, text=True, check=True
                    )
                except subprocess.CalledProcessError as e:
                    # Provide a descriptive error with stdout/stderr hints
                    msg_parts = [
                        "Bashio MQTT discovery failed.",
                        f"exit_code={e.returncode}",
                    ]
                    if e.stdout:
                        msg_parts.append(f"stdout={e.stdout.strip()}")
                    if e.stderr:
                        msg_parts.append(f"stderr={e.stderr.strip()}")

                    # Add common-cause guidance
                    hints = []
                    if not os.path.exists("/usr/bin/with-contenv"):
                        hints.append("not running in a Home Assistant base image (with-contenv missing)")
                    if not os.environ.get("SUPERVISOR_TOKEN"):
                        hints.append("SUPERVISOR_TOKEN not set (likely not under Supervisor)")
                    hints.append("MQTT add-on/service may be absent or not discovered by bashio")
                    hints.append(
                        "Provide mqtt_host/mqtt_port/mqtt_username/mqtt_password in add-on options (or options.local.json for dev) to bypass bashio"
                    )

                    hint_msg = "; ".join(hints)
                    raise RuntimeError("; ".join(msg_parts) + f"; hints: {hint_msg}") from e

                return json.loads(result.stdout)
        finally:
            try:
                os.unlink(script_path)
            except NameError:
                pass

    # File-based health reporting removed in favor of HTTP watchdog
