"""Run ``collectstatic`` only when dependencies have changed.

Speeds up startup time on cloud platforms. To disable this behavior, set
the ``COLLECTSTATIC_WHEN_DEPS_CHANGE`` environment variable to ``False``.
"""

import hashlib
import os
import subprocess
import sys

import django
import redis
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openwisp.settings")
_COLLECTSTATIC_DEBUG = os.environ.get("COLLECTSTATIC_DEBUG", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_COLLECTSTATIC_DEBUG_ALWAYS = os.environ.get("COLLECTSTATIC_DEBUG_ALWAYS", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _enable_collectstatic_debug():
    if not (_COLLECTSTATIC_DEBUG or _COLLECTSTATIC_DEBUG_ALWAYS):
        return
    try:
        from compress_staticfiles.storage import CompressStaticFilesStorage
    except Exception as exc:
        print(f"collectstatic debug: unable to import CompressStaticFilesStorage: {exc}", file=sys.stderr)
        return

    original_minify_css = CompressStaticFilesStorage._minify_css

    def _minify_css(self, path):
        print(f"collectstatic debug: minifying CSS {path}", file=sys.stderr)
        try:
            return original_minify_css(self, path)
        except Exception as exc:
            print(f"collectstatic debug: error minifying {path}: {exc}", file=sys.stderr)
            try:
                content = self.open(path).read()
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                os.makedirs("/data/logs", exist_ok=True)
                with open("/data/logs/collectstatic_failed.css", "w") as handle:
                    handle.write(content[:2000])
                print(
                    "collectstatic debug: wrote snippet to /data/logs/collectstatic_failed.css",
                    file=sys.stderr,
                )
            except Exception as dump_exc:
                print(f"collectstatic debug: failed to dump CSS snippet: {dump_exc}", file=sys.stderr)
            raise

    CompressStaticFilesStorage._minify_css = _minify_css


_enable_collectstatic_debug()
django.setup()


def get_pip_freeze_hash():
    try:
        pip_freeze_output = subprocess.check_output(["pip", "freeze"]).decode()
        return hashlib.sha256(pip_freeze_output.encode()).hexdigest()
    except subprocess.CalledProcessError as e:
        print(f"Error running 'pip freeze': {e}", file=sys.stderr)
        sys.exit(1)


def _run_collectstatic_in_process():
    from django.core.management import call_command

    print("collectstatic debug: running in-process collectstatic", file=sys.stderr)
    call_command("collectstatic", verbosity=2, interactive=False)


def run_collectstatic():
    try:
        if _COLLECTSTATIC_DEBUG or _COLLECTSTATIC_DEBUG_ALWAYS:
            _run_collectstatic_in_process()
        else:
            subprocess.run(
                [sys.executable, "manage.py", "collectstatic", "--noinput"], check=True
            )
    except subprocess.CalledProcessError as e:
        print(f"Error running 'collectstatic': {e}", file=sys.stderr)
        print("collectstatic debug: retrying in-process with verbose output", file=sys.stderr)
        _enable_collectstatic_debug()
        _run_collectstatic_in_process()
        sys.exit(1)


def main():
    if os.environ.get("COLLECTSTATIC_WHEN_DEPS_CHANGE", "true").lower() == "false":
        run_collectstatic()
        return
    redis_connection = redis.Redis.from_url(settings.CACHES["default"]["LOCATION"])
    current_pip_hash = get_pip_freeze_hash()
    cached_pip_hash = redis_connection.get("pip_freeze_hash")
    if not cached_pip_hash or cached_pip_hash.decode() != current_pip_hash:
        print("Changes in Python dependencies detected, running collectstatic...")
        run_collectstatic()
        redis_connection.set("pip_freeze_hash", current_pip_hash)
    else:
        print("No changes in Python dependencies, skipping collectstatic...")


if __name__ == "__main__":
    main()
