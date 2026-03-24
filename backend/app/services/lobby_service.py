import asyncio
import logging
import time
import uuid

from app.core.config import Settings
from app.repositories.redis_repository import RedisRepository

logger = logging.getLogger("hangman.lobby")


class LobbyService:
    def __init__(self, settings: Settings, repository: RedisRepository) -> None:
        self.settings = settings
        self.repository = repository

    @staticmethod
    def _room_sort_key(room: dict) -> tuple[int, int, str]:
        room_id = str(room.get("room_id", ""))
        if room_id.startswith("sala-"):
            suffix = room_id.replace("sala-", "", 1)
            if suffix.isdigit():
                return (0, int(suffix), room_id)
        return (1, int(room.get("created_at", 0)), room_id)

    async def _wait_room_lock(
        self,
        room_id: str,
        retries: int = 8,
        delay_seconds: float = 0.05,
    ) -> str | None:
        lock_key = f"lock:room:{room_id}"
        for _ in range(retries):
            token = await self.repository.acquire_lock(lock_key, ttl_ms=4000)
            if token is not None:
                return token
            await asyncio.sleep(delay_seconds)
        return None

    async def ensure_default_rooms(self) -> None:
        existing_rooms = await self.repository.list_rooms()
        for room in existing_rooms:
            room_id = str(room.get("room_id", ""))
            if room_id.startswith("mineiro-") and room.get("status") != "in_game":
                await self.repository.delete_room(room_id)
                logger.info(
                    "lobby_room_removed_legacy",
                    extra={"event": "lobby_room_removed_legacy", "room_id": room_id},
                )

        existing_rooms = await self.repository.list_rooms()
        existing_ids = {room["room_id"] for room in existing_rooms}
        now = int(time.time())

        for index in range(1, self.settings.default_room_count + 1):
            room_id = f"sala-{index}"
            if room_id in existing_ids:
                continue
            room = {
                "room_id": room_id,
                "name": f"Sala {index}",
                "status": "waiting",
                "max_players": 2,
                "players": [],
                "match_id": None,
                "created_at": now,
                "updated_at": now,
            }
            await self.repository.save_room(room)
            logger.info(
                "lobby_room_created_default",
                extra={"event": "lobby_room_created_default", "room_id": room_id, "room_name": room["name"]},
            )

    async def create_room(self, name: str) -> dict:
        room_name = name.strip() if name else ""
        if not room_name:
            raise ValueError("Nome da sala e obrigatorio")
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        now = int(time.time())
        room = {
            "room_id": room_id,
            "name": room_name,
            "status": "waiting",
            "max_players": 2,
            "players": [],
            "match_id": None,
            "created_at": now,
            "updated_at": now,
        }
        await self.repository.save_room(room)
        logger.info(
            "lobby_room_created_custom",
            extra={"event": "lobby_room_created_custom", "room_id": room_id, "room_name": room_name},
        )
        return room

    async def snapshot(self) -> dict:
        await self.ensure_default_rooms()
        rooms = await self.repository.list_rooms()
        rooms = sorted(rooms, key=self._room_sort_key)

        decorated_rooms = []
        active_matches = 0
        waiting_rooms = 0
        waiting_players = 0

        for room in rooms:
            players_data = []
            for player_id in room.get("players", []):
                player = await self.repository.get_player(player_id)
                if player is None:
                    continue
                players_data.append(
                    {
                        "player_id": player_id,
                        "nickname": player["nickname"],
                        "connected": player["connected"],
                        "status": player["status"],
                    }
                )

            if room.get("status") == "in_game":
                active_matches += 1
            else:
                waiting_rooms += 1
                waiting_players += len(players_data)

            decorated_rooms.append(
                {
                    "room_id": room["room_id"],
                    "name": room["name"],
                    "status": room["status"],
                    "max_players": room.get("max_players", 2),
                    "players": players_data,
                    "match_id": room.get("match_id"),
                    "current_players": len(players_data),
                }
            )

        return {
            "timestamp": int(time.time()),
            "total_rooms": len(decorated_rooms),
            "active_matches": active_matches,
            "waiting_rooms": waiting_rooms,
            "waiting_players": waiting_players,
            "rooms": decorated_rooms,
        }

    async def remove_player_from_waiting_room(self, player_id: str) -> None:
        player = await self.repository.get_player(player_id)
        if player is None:
            return
        room_id = player.get("room_id")
        if not room_id:
            return

        lock_key = f"lock:room:{room_id}"
        token = await self.repository.acquire_lock(lock_key, ttl_ms=3000)
        if token is None:
            return

        try:
            room = await self.repository.get_room(room_id)
            if room is None:
                return
            if room.get("status") == "in_game":
                return
            room["players"] = [pid for pid in room.get("players", []) if pid != player_id]
            room["updated_at"] = int(time.time())
            await self.repository.save_room(room)

            player["room_id"] = None
            if player["status"] == "waiting":
                player["status"] = "idle"
            await self.repository.save_player(player)
            logger.info(
                "lobby_player_removed",
                extra={"event": "lobby_player_removed", "player_id": player_id, "room_id": room_id},
            )
        finally:
            await self.repository.release_lock(lock_key, token)

    async def join_room(self, player_id: str, room_id: str) -> dict:
        lock_key = f"lock:room:{room_id}"
        token = await self._wait_room_lock(room_id)
        if token is None:
            raise ValueError("Sala ocupada, tente novamente")

        try:
            room = await self.repository.get_room(room_id)
            if room is None:
                raise ValueError("Sala nao encontrada")

            player = await self.repository.get_player(player_id)
            if player is None:
                raise ValueError("Jogador nao encontrado")

            if player["status"] == "playing":
                raise ValueError("Voce ja esta em partida ativa")

            current_players = room.get("players", [])
            if player_id in current_players:
                return {"state": "already_joined", "room": room}

            if room.get("status") == "in_game":
                raise ValueError("Sala em jogo")

            if len(current_players) >= room.get("max_players", 2):
                raise ValueError("Sala lotada")

            if player.get("room_id") and player.get("room_id") != room_id:
                await self.remove_player_from_waiting_room(player_id)

            now = int(time.time())
            room["players"] = [*current_players, player_id]
            room["updated_at"] = now
            if len(room["players"]) >= room.get("max_players", 2):
                room["status"] = "in_game"
            await self.repository.save_room(room)

            player["room_id"] = room_id
            player["status"] = "waiting" if len(room["players"]) == 1 else "playing"
            player["queue_entered_at"] = now
            await self.repository.save_player(player)

            logger.info(
                "lobby_room_joined",
                extra={
                    "event": "lobby_room_joined",
                    "room_id": room_id,
                    "player_id": player_id,
                    "players_in_room": len(room["players"]),
                },
            )

            if len(room["players"]) >= room.get("max_players", 2):
                return {"state": "ready", "room": room, "player_ids": room["players"][:2]}

            return {"state": "waiting", "room": room}
        finally:
            await self.repository.release_lock(lock_key, token)

    async def bind_room_match(self, room_id: str, match_id: str) -> None:
        room = await self.repository.get_room(room_id)
        if room is None:
            return
        room["status"] = "in_game"
        room["match_id"] = match_id
        room["updated_at"] = int(time.time())
        await self.repository.save_room(room)

    async def reset_room_after_match(self, room_id: str) -> None:
        room = await self.repository.get_room(room_id)
        if room is None:
            return
        room["status"] = "waiting"
        room["players"] = []
        room["match_id"] = None
        room["updated_at"] = int(time.time())
        await self.repository.save_room(room)
        logger.info(
            "lobby_room_reset",
            extra={"event": "lobby_room_reset", "room_id": room_id},
        )
