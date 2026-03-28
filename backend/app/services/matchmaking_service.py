import asyncio
import logging
import random
import time
import uuid

from app.core.config import Settings
from app.models.types import MatchState
from app.monitoring.metrics import Metrics
from app.repositories.redis_repository import RedisRepository
from app.services.event_dispatcher import EventDispatcher
from app.services.game_utils import build_game_state_payload
from app.services.word_bank import WordBank

logger = logging.getLogger("hangman.matchmaking")


class MatchmakingService:
    def __init__(
        self,
        settings: Settings,
        repository: RedisRepository,
        dispatcher: EventDispatcher,
        metrics: Metrics,
        word_bank: WordBank,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.dispatcher = dispatcher
        self.metrics = metrics
        self.word_bank = word_bank

    async def join_queue(self, player_id: str) -> None:
        player = await self.repository.get_player(player_id)
        if player is None:
            return

        player["status"] = "waiting"
        player["match_id"] = None
        player["queue_entered_at"] = int(time.time())
        await self.repository.save_player(player)
        await self.repository.enqueue_player(player_id)
        logger.info(
            "player_joined_queue",
            extra={
                "event": "player_joined_queue",
                "player_id": player_id,
                "nickname": player["nickname"],
            },
        )

        await self.broadcast_queue_updates()
        await self.try_match_players()

    async def remove_from_queue(self, player_id: str) -> None:
        await self.repository.dequeue_player(player_id)
        await self.broadcast_queue_updates()

    async def try_match_players(self) -> None:
        lock_key = "lock:matchmaking"
        lock_token = await self.repository.acquire_lock(lock_key, ttl_ms=3000)
        if lock_token is None:
            return

        try:
            while True:
                player_1 = await self._pop_next_valid_waiting_player()
                if player_1 is None:
                    break

                player_2 = await self._pop_next_valid_waiting_player()
                if player_2 is None:
                    await self.repository.enqueue_front_player(player_1["player_id"])
                    break

                await self._create_match(player_1, player_2)
        finally:
            await self.repository.release_lock(lock_key, lock_token)
            await self.broadcast_queue_updates()

    async def _pop_next_valid_waiting_player(self) -> dict | None:
        while True:
            player_id = await self.repository.pop_waiting_player_id()
            if player_id is None:
                return None

            player = await self.repository.get_player(player_id)
            if player is None:
                continue
            if player.get("status") != "waiting":
                continue
            return player

    async def _create_match(self, player_1: dict, player_2: dict) -> None:
        await self.create_match_for_players(player_1, player_2, room_id=None)

    async def create_match_for_players(self, player_1: dict, player_2: dict, room_id: str | None) -> MatchState:
        now = int(time.time())
        match_id = str(uuid.uuid4())

        player_ids = [player_1["player_id"], player_2["player_id"]]
        first_round = self.word_bank.random_entry()
        starting_player_id = random.choice(player_ids)

        match: MatchState = {
            "match_id": match_id,
            "player_ids": player_ids,
            "player_nicknames": {
                player_1["player_id"]: player_1["nickname"],
                player_2["player_id"]: player_2["nickname"],
            },
            "room_id": room_id,
            "total_rounds": 3,
            "current_round": 1,
            "starting_player_id": starting_player_id,
            "turn": starting_player_id,
            "current_word": first_round["word"],
            "current_theme": first_round["theme"],
            "correct_letters": [],
            "wrong_letters_by_player": {
                player_1["player_id"]: [],
                player_2["player_id"]: [],
            },
            "errors_by_player": {
                player_1["player_id"]: 0,
                player_2["player_id"]: 0,
            },
            "scores": {
                player_1["player_id"]: 0,
                player_2["player_id"]: 0,
            },
            "round_history": [],
            "status": "active",
            "winner": None,
            "reason": None,
            "disconnect_deadlines": {player_1["player_id"]: None, player_2["player_id"]: None},
            "created_at": now,
            "updated_at": now,
        }

        for player in (player_1, player_2):
            queue_entered_at = player.get("queue_entered_at")
            if queue_entered_at is not None:
                self.metrics.observe_queue_wait(max(0, now - int(queue_entered_at)))
            player["status"] = "playing"
            player["match_id"] = match_id
            player["queue_entered_at"] = None

        await self.repository.save_match(match)
        await self.repository.add_active_match(match_id)
        await self.repository.save_player(player_1)
        await self.repository.save_player(player_2)
        logger.info(
            "match_created",
            extra={
                "event": "match_created",
                "match_id": match_id,
                "player_1_id": player_1["player_id"],
                "player_2_id": player_2["player_id"],
                "theme": match["current_theme"],
                "word_size": len(match["current_word"]),
                "turn_player": starting_player_id,
            },
        )

        await asyncio.gather(
            self._notify_match_found(match, player_1["player_id"]),
            self._notify_match_found(match, player_2["player_id"]),
        )
        return match

    async def _notify_match_found(self, match: MatchState, player_id: str) -> None:
        opponent = match["player_ids"][1] if match["player_ids"][0] == player_id else match["player_ids"][0]

        await self.dispatcher.send_to_player(
            player_id,
            {
                "type": "match_found",
                "match_id": match["match_id"],
                "room_id": match.get("room_id"),
                "opponent": match["player_nicknames"][opponent],
                "round_number": match["current_round"],
                "total_rounds": match["total_rounds"],
                "theme": match["current_theme"],
                "message": "Partida encontrada",
            },
        )

        payload = build_game_state_payload(match, player_id, self.settings.max_errors)
        payload["message"] = "Rodada 1 iniciada. O jogo e por turnos."
        await self.dispatcher.send_to_player(player_id, payload)

    async def broadcast_queue_updates(self) -> None:
        waiting = await self.repository.list_waiting_players()
        for position, player_id in enumerate(waiting, start=1):
            await self.dispatcher.send_to_player(
                player_id,
                {
                    "type": "queue_update",
                    "position": position,
                    "message": "Aguardando adversario",
                },
            )
