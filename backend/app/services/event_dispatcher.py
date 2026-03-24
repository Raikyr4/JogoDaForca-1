import json
from typing import Optional

from app.core.config import Settings
from app.repositories.redis_repository import RedisRepository
from app.services.connection_manager import ConnectionManager


class EventDispatcher:
    def __init__(
        self,
        settings: Settings,
        repository: RedisRepository,
        connection_manager: ConnectionManager,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.connection_manager = connection_manager

    @staticmethod
    def _channel(server_id: str) -> str:
        return f"server:{server_id}"

    async def send_to_player(self, player_id: str, payload: dict) -> None:
        player = await self.repository.get_player(player_id)
        if player is None:
            return
        if not player.get("connected"):
            return

        target_server = player.get("connected_server")
        if not target_server:
            return

        if target_server == self.settings.server_id:
            sent = await self.connection_manager.send_local(player_id, payload)
            if sent:
                return

        envelope = {"player_id": player_id, "payload": payload}
        await self.repository.publish_server_event(target_server, json.dumps(envelope))

    async def send_error(self, player_id: str, message: str) -> None:
        await self.send_to_player(player_id, {"type": "error", "message": message})


class ServerChannelSubscriber:
    def __init__(
        self,
        settings: Settings,
        repository: RedisRepository,
        connection_manager: ConnectionManager,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.connection_manager = connection_manager
        self._pubsub = self.repository.redis.pubsub()
        self._running = False

    async def start(self) -> None:
        self._running = True
        await self._pubsub.subscribe(self._channel(self.settings.server_id))

    async def stop(self) -> None:
        self._running = False
        await self._pubsub.unsubscribe(self._channel(self.settings.server_id))
        if hasattr(self._pubsub, "aclose"):
            await self._pubsub.aclose()
        else:
            await self._pubsub.close()

    @staticmethod
    def _channel(server_id: str) -> str:
        return f"server:{server_id}"

    async def pump_once(self) -> None:
        if not self._running:
            return
        message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
        if message is None:
            return
        data = message.get("data")
        if not isinstance(data, str):
            return

        envelope: Optional[dict]
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError:
            return

        player_id = envelope.get("player_id")
        payload = envelope.get("payload")
        if not isinstance(player_id, str) or not isinstance(payload, dict):
            return
        await self.connection_manager.send_local(player_id, payload)
