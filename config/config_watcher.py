"""
ConfigWatcher — reacts to file changes via OS filesystem events (inotify on Linux).

No polling — the OS notifies us the instant the file is saved.
Falls back to 5-second polling if watchdog is not available.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "telegram_folder": "",
    "min_message_length": 30,
    "load_history_limit": 50,
    "analyze_interval_minutes": 30,
    "digest_interval_hours": 3,
    "ignored_sources": [],
    "keywords_alert": [],
    "digest_max_items": 7,
    "digest_min_temperature": 5.0,
    "digest_rules": {
        "max_per_topic": 2,
        "always_include_alerts": True,
        "min_unique_sources_for_trend": 3,
        "dedup_threshold": 0.85,
    },
    "digest_engine": "legacy",     # "legacy" or "agent"
    "instant_alerts_temperature": True, # send breaking news immediately based on temp
    "instant_alerts_trend": True        # send breaking news immediately based on trend status
}



class ConfigWatcher:
    """
    Watches a JSON config file and fires callbacks when values change.

    Uses OS filesystem events (inotify on Linux inside Docker) via watchdog.
    Falls back to polling every 5 seconds if watchdog is not installed.
    """

    FALLBACK_POLL_INTERVAL = 5  # seconds, used only if watchdog unavailable

    def __init__(self, config_path: str = "/app/config/settings.json"):
        self.config_path = Path(config_path)
        self._config: dict[str, Any] = {}
        self._callbacks: dict[str, list[Callable]] = {}
        self._change_event = asyncio.Event()  # set by watchdog thread on file change

        # Load initial config
        self._config = self._load()
        logger.info(f"Config loaded from {self.config_path}: {self._config}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def load_topics(self) -> dict:
        """
        Load topics.json from the same directory as settings.json.
        Returns a dict of {canonical_label: {aliases: [...], alert: bool, ...}}.
        Falls back to empty dict if file not found.
        """
        topics_path = self.config_path.parent / "topics.json"
        if not topics_path.exists():
            logger.warning(f"topics.json not found at {topics_path}")
            return {}
        try:
            with open(topics_path, encoding="utf-8") as f:
                data = json.load(f)
            # Strip meta keys that start with "_"
            return {k: v for k, v in data.items() if not k.startswith("_")}
        except Exception as e:
            logger.error(f"Failed to load topics.json: {e}")
            return {}

    def on_change(self, key: str, callback: Callable[[Any], Any]) -> None:
        """Register a callback that fires when a specific key changes."""
        self._callbacks.setdefault(key, []).append(callback)

    def _load(self) -> dict[str, Any]:
        """Load config from JSON file, then apply env var overrides."""
        config = dict(DEFAULT_CONFIG)

        if self.config_path.exists():
            try:
                with open(self.config_path) as f:
                    data = json.load(f)
                for key in DEFAULT_CONFIG:
                    if key in data:
                        config[key] = data[key]
                logger.debug(f"Read config file: {self.config_path}")
            except Exception as e:
                logger.error(f"Failed to read config file: {e}")

        # Env vars always override file (backwards compatibility with .env)
        env_map = {
            "telegram_folder":          os.environ.get("TELEGRAM_FOLDER", ""),
            "min_message_length":       int(os.environ.get("MIN_MESSAGE_LENGTH", 0)) or None,
            "analyze_interval_minutes": int(os.environ.get("ANALYZE_INTERVAL_MINUTES", 0)) or None,
            "digest_interval_hours":    int(os.environ.get("DIGEST_INTERVAL_HOURS", 0)) or None,
        }
        for key, val in env_map.items():
            if val:
                config[key] = val

        return config

    async def _apply_changes(self) -> None:
        """Compare new config with current and fire callbacks for changed keys."""
        new_config = self._load()
        for key, new_val in new_config.items():
            old_val = self._config.get(key)
            if new_val != old_val:
                logger.info(f"Config changed: {key}: {old_val!r} → {new_val!r}")
                for cb in self._callbacks.get(key, []):
                    try:
                        result = cb(new_val)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Callback error for '{key}': {e}")
        self._config = new_config

    def _start_watchdog(self) -> bool:
        """
        Start watchdog PollingObserver in a background thread.
        We intentionally use PollingObserver (not the default inotify Observer)
        because inotify does NOT work reliably with Docker Desktop on Windows:
        file changes from the Windows host are invisible to Linux inotify.
        PollingObserver checks mtime every second — works on all platforms.
        """
        try:
            from watchdog.observers.polling import PollingObserver
            from watchdog.events import FileSystemEventHandler

            loop = asyncio.get_event_loop()
            watcher = self

            class Handler(FileSystemEventHandler):
                def on_modified(self, event):
                    if Path(event.src_path).resolve() == watcher.config_path.resolve():
                        logger.info("Config file changed — reloading...")
                        loop.call_soon_threadsafe(watcher._change_event.set)

                def on_created(self, event):
                    self.on_modified(event)

            observer = PollingObserver(timeout=3)  # poll every 3 seconds
            observer.schedule(Handler(), str(self.config_path.parent), recursive=False)
            observer.daemon = True
            observer.start()
            logger.info(f"Watching {self.config_path} via polling (3s interval)")
            return True

        except ImportError:
            logger.warning("watchdog not installed — falling back to polling every 5s")
            return False
        except Exception as e:
            logger.warning(f"watchdog failed to start ({e}) — falling back to polling")
            return False

    async def watch(self) -> None:
        """Main watch loop. Uses OS events if watchdog available, else polls."""
        if not self.config_path.parent.exists():
            logger.warning(f"Config directory not found: {self.config_path.parent}")

        use_watchdog = self._start_watchdog()

        if use_watchdog:
            # Event-driven: wait for watchdog to signal a change
            while True:
                await self._change_event.wait()
                self._change_event.clear()
                # Small debounce — some editors write in two passes
                await asyncio.sleep(0.2)
                await self._apply_changes()
        else:
            # Fallback: poll every N seconds
            while True:
                await asyncio.sleep(self.FALLBACK_POLL_INTERVAL)
                if self.config_path.exists():
                    await self._apply_changes()
