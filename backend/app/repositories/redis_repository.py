import json
import uuid
from typing import Optional

from redis.asyncio import Redis

from app.core.config import Settings
from app.models.types import MatchState, Player


class RedisRepository:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.settings = settings

    @staticmethod
    def _player_key(player_id: str) -> str:
        return f"player:{player_id}"

    @staticmethod
    def _match_key(match_id: str) -> str:
        return f"match:{match_id}"

    @staticmethod
    def _heartbeat_key(player_id: str) -> str:
        return f"heartbeat:{player_id}"

    @staticmethod
    def _room_key(room_id: str) -> str:
        return f"lobby:room:{room_id}"

    @staticmethod
    def _nickname_key(nickname: str) -> str:
        normalized = nickname.strip().lower()
        return f"nickname:active:{normalized}"

    @staticmethod
    def _server_heartbeat_key(server_id: str) -> str:
        return f"server:heartbeat:{server_id}"

    async def _set_json(self, key: str, value: dict) -> None:
        await self.redis.set(key, json.dumps(value))

    async def _get_json(self, key: str) -> Optional[dict]:
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def save_player(self, player: Player) -> None:
        await self._set_json(self._player_key(player["player_id"]), player)

    async def get_player(self, player_id: str) -> Optional[Player]:
        data = await self._get_json(self._player_key(player_id))
        return data  # type: ignore[return-value]

    async def list_player_ids(self) -> list[str]:
        cursor = 0
        player_ids: list[str] = []
        while True:
            cursor, keys = await self.redis.scan(cursor=cursor, match="player:*", count=200)
            for key in keys:
                player_ids.append(str(key).removeprefix("player:"))
            if cursor == 0:
                break
        return player_ids

    async def save_match(self, match: MatchState) -> None:
        await self._set_json(self._match_key(match["match_id"]), match)

    async def get_match(self, match_id: str) -> Optional[MatchState]:
        data = await self._get_json(self._match_key(match_id))
        return data  # type: ignore[return-value]

    async def save_room(self, room: dict) -> None:
        await self._set_json(self._room_key(room["room_id"]), room)
        await self.redis.sadd(self.settings.lobby_rooms_set_key, room["room_id"])

    async def get_room(self, room_id: str) -> Optional[dict]:
        return await self._get_json(self._room_key(room_id))

    async def list_rooms(self) -> list[dict]:
        room_ids = await self.redis.smembers(self.settings.lobby_rooms_set_key)
        rooms: list[dict] = []
        for room_id in room_ids:
            room = await self.get_room(room_id)
            if room is not None:
                rooms.append(room)
        return rooms

    async def delete_room(self, room_id: str) -> None:
        await self.redis.srem(self.settings.lobby_rooms_set_key, room_id)
        await self.redis.delete(self._room_key(room_id))

    async def enqueue_player(self, player_id: str) -> None:
        await self.redis.lrem(self.settings.queue_key, 0, player_id)
        await self.redis.rpush(self.settings.queue_key, player_id)

    async def enqueue_front_player(self, player_id: str) -> None:
        await self.redis.lrem(self.settings.queue_key, 0, player_id)
        await self.redis.lpush(self.settings.queue_key, player_id)

    async def dequeue_player(self, player_id: str) -> None:
        await self.redis.lrem(self.settings.queue_key, 0, player_id)

    async def pop_waiting_player_id(self) -> Optional[str]:
        return await self.redis.lpop(self.settings.queue_key)

    async def list_waiting_players(self) -> list[str]:
        return await self.redis.lrange(self.settings.queue_key, 0, -1)

    async def waiting_count(self) -> int:
        return await self.redis.llen(self.settings.queue_key)

    async def queue_position(self, player_id: str) -> Optional[int]:
        players = await self.list_waiting_players()
        try:
            return players.index(player_id) + 1
        except ValueError:
            return None

    async def set_heartbeat(self, player_id: str, timestamp: int) -> None:
        await self.redis.set(
            self._heartbeat_key(player_id),
            str(timestamp),
            ex=self.settings.heartbeat_ttl_seconds,
        )

    async def set_server_heartbeat(self, server_id: str, timestamp: int) -> None:
        await self.redis.set(
            self._server_heartbeat_key(server_id),
            str(timestamp),
            ex=self.settings.server_heartbeat_ttl_seconds,
        )

    async def is_server_alive(self, server_id: str) -> bool:
        if not server_id:
            return False
        return bool(await self.redis.exists(self._server_heartbeat_key(server_id)))

    async def add_active_match(self, match_id: str) -> None:
        await self.redis.sadd(self.settings.active_matches_key, match_id)

    async def remove_active_match(self, match_id: str) -> None:
        await self.redis.srem(self.settings.active_matches_key, match_id)

    async def active_matches_count(self) -> int:
        return await self.redis.scard(self.settings.active_matches_key)

    async def active_match_ids(self) -> list[str]:
        ids = await self.redis.smembers(self.settings.active_matches_key)
        return sorted(ids)

    async def add_reconnect_deadline(
        self,
        match_id: str,
        player_id: str,
        deadline_timestamp: int,
    ) -> None:
        token = f"{match_id}:{player_id}"
        await self.redis.zadd(self.settings.reconnect_zset_key, {token: deadline_timestamp})

    async def remove_reconnect_deadline(self, match_id: str, player_id: str) -> None:
        token = f"{match_id}:{player_id}"
        await self.redis.zrem(self.settings.reconnect_zset_key, token)

    async def read_expired_deadlines(self, now_timestamp: int) -> list[str]:
        return await self.redis.zrangebyscore(self.settings.reconnect_zset_key, "-inf", now_timestamp)

    async def acquire_lock(self, key: str, ttl_ms: int = 5000) -> Optional[str]:
        token = str(uuid.uuid4())
        acquired = await self.redis.set(key, token, nx=True, px=ttl_ms)
        if acquired:
            return token
        return None

    async def release_lock(self, key: str, token: str) -> None:
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(script, 1, key, token)

    async def publish_server_event(self, server_id: str, payload: str) -> None:
        await self.redis.publish(f"server:{server_id}", payload)

    async def claim_nickname(self, nickname: str, player_id: str, ttl_seconds: int) -> bool:
        key = self._nickname_key(nickname)
        acquired = await self.redis.set(key, player_id, ex=ttl_seconds, nx=True)
        return bool(acquired)

    async def get_nickname_owner(self, nickname: str) -> Optional[str]:
        key = self._nickname_key(nickname)
        owner = await self.redis.get(key)
        if not owner:
            return None
        return str(owner)

    async def refresh_nickname_claim(self, nickname: str, player_id: str, ttl_seconds: int) -> bool:
        key = self._nickname_key(nickname)
        current_owner = await self.redis.get(key)
        if current_owner != player_id:
            return False
        await self.redis.expire(key, ttl_seconds)
        return True

    async def release_nickname_if_owner(self, nickname: str, player_id: str) -> None:
        key = self._nickname_key(nickname)
        script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        await self.redis.eval(script, 1, key, player_id)
