import asyncio
import logging
import time
import uuid
from typing import Optional

from fastapi import WebSocket

from app.core.config import Settings
from app.models.types import MatchState, Player
from app.monitoring.metrics import Metrics
from app.repositories.redis_repository import RedisRepository
from app.services.connection_manager import ConnectionManager
from app.services.event_dispatcher import EventDispatcher
from app.services.game_utils import (
    build_game_state_payload,
    normalize_letter,
    normalize_word_guess,
    opponent_id,
    solved_word,
)
from app.services.lobby_service import LobbyService
from app.services.matchmaking_service import MatchmakingService
from app.services.word_bank import WordBank

logger = logging.getLogger("hangman.game")


class GameService:
    def __init__(
        self,
        settings: Settings,
        repository: RedisRepository,
        dispatcher: EventDispatcher,
        connection_manager: ConnectionManager,
        matchmaking: MatchmakingService,
        lobby_service: LobbyService,
        word_bank: WordBank,
        metrics: Metrics,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.dispatcher = dispatcher
        self.connection_manager = connection_manager
        self.matchmaking = matchmaking
        self.lobby_service = lobby_service
        self.word_bank = word_bank
        self.metrics = metrics

    def _nickname_claim_ttl(self) -> int:
        return max(self.settings.heartbeat_ttl_seconds, self.settings.reconnect_timeout_seconds + 5)

    async def _server_is_alive(self, server_id: str | None) -> bool:
        if not server_id:
            return False
        if server_id == self.settings.server_id:
            return True
        return await self.repository.is_server_alive(server_id)

    async def _mark_player_waiting_reconnect(
        self,
        player: Player,
        now: int,
        *,
        preserve_nickname: bool = True,
        caused_by_server_failure: bool = False,
    ) -> None:
        player_id = player["player_id"]
        player["connected"] = False
        player["connected_server"] = None
        player["last_seen"] = now
        await self.repository.save_player(player)

        if player["status"] == "waiting":
            if not preserve_nickname:
                await self.repository.release_nickname_if_owner(player["nickname"], player_id)
            await self.lobby_service.remove_player_from_waiting_room(player_id)
            return

        match_id = player.get("match_id")
        if not match_id:
            if not preserve_nickname:
                await self.repository.release_nickname_if_owner(player["nickname"], player_id)
            return

        lock_key = f"lock:match:{match_id}"
        token = await self.repository.acquire_lock(lock_key, ttl_ms=5000)
        if token is None:
            return

        try:
            match = await self.repository.get_match(match_id)
            if match is None or match["status"] != "active":
                return
            if player_id not in match["player_ids"]:
                return

            deadline = now + self.settings.reconnect_timeout_seconds
            current_deadline = match["disconnect_deadlines"].get(player_id)
            if current_deadline is None or current_deadline > deadline:
                match["disconnect_deadlines"][player_id] = deadline
                match["updated_at"] = now
                await self.repository.save_match(match)
                await self.repository.add_reconnect_deadline(match_id, player_id, deadline)

            opp_id = opponent_id(match, player_id)
            if caused_by_server_failure:
                message = "Servidor do adversario caiu. Tentando migrar a sessao para outro backend."
            else:
                message = "Seu adversario desconectou. Aguardando reconexao por ate 30 segundos."
            await self.dispatcher.send_to_player(
                opp_id,
                {
                    "type": "opponent_disconnected",
                    "message": message,
                },
            )
        finally:
            await self.repository.release_lock(lock_key, token)

    async def _normalize_stale_player_connection(self, player: Player) -> Player:
        connected_server = player.get("connected_server")
        if not player.get("connected") or not connected_server:
            return player
        if await self._server_is_alive(connected_server):
            return player

        now = int(time.time())
        await self._mark_player_waiting_reconnect(
            player,
            now,
            preserve_nickname=True,
            caused_by_server_failure=True,
        )
        return player

    async def register_player(self, websocket: WebSocket, nickname: str) -> str:
        nickname = nickname.strip()
        if not nickname:
            raise ValueError("Nickname e obrigatorio")

        existing_player_id = await self.repository.get_nickname_owner(nickname)
        if existing_player_id:
            existing_player = await self.repository.get_player(existing_player_id)
            if existing_player is None:
                await self.repository.release_nickname_if_owner(nickname, existing_player_id)
            else:
                existing_player = await self._normalize_stale_player_connection(existing_player)
                if existing_player.get("connected"):
                    raise ValueError("Ja existe um jogador com este nickname conectado")
                resumed = await self._resume_disconnected_session_from_login(websocket, existing_player)
                if resumed:
                    return existing_player_id
                await self.repository.release_nickname_if_owner(nickname, existing_player_id)

        player_id = str(uuid.uuid4())
        nickname_claimed = await self.repository.claim_nickname(
            nickname,
            player_id,
            ttl_seconds=self._nickname_claim_ttl(),
        )
        if not nickname_claimed:
            raise ValueError("Ja existe um jogador com este nickname conectado")

        now = int(time.time())
        player: Player = {
            "player_id": player_id,
            "nickname": nickname,
            "status": "idle",
            "match_id": None,
            "room_id": None,
            "connected_server": self.settings.server_id,
            "connected": True,
            "last_seen": now,
            "queue_entered_at": None,
        }

        try:
            await self.repository.save_player(player)
            await self.repository.set_heartbeat(player_id, now)
            await self.connection_manager.bind_player(player_id, websocket)

            await self.connection_manager.send_local(
                player_id,
                {
                    "type": "connected",
                    "player_id": player_id,
                    "message": "Conectado com sucesso",
                },
            )
        except Exception:
            await self.repository.release_nickname_if_owner(nickname, player_id)
            raise
        logger.info(
            "player_connected",
            extra={
                "event": "player_connected",
                "player_id": player_id,
                "nickname": nickname,
            },
        )
        return player_id

    async def join_queue(self, websocket: WebSocket, nickname: str) -> str:
        player_id = await self.register_player(websocket, nickname)
        await self.matchmaking.join_queue(player_id)
        return player_id

    async def join_room(self, player_id: str, room_id: str) -> None:
        result = await self.lobby_service.join_room(player_id, room_id)
        state = result.get("state")
        room = result.get("room", {})

        if state in {"waiting", "already_joined"}:
            await self.dispatcher.send_to_player(
                player_id,
                {
                    "type": "room_joined",
                    "room_id": room_id,
                    "room_name": room.get("name", room_id),
                    "message": "Aguardando adversario nesta sala",
                },
            )
            return

        if state == "ready":
            player_ids: list[str] = result.get("player_ids", [])
            if len(player_ids) < 2:
                raise ValueError("Sala sem jogadores suficientes")

            player_1 = await self.repository.get_player(player_ids[0])
            player_2 = await self.repository.get_player(player_ids[1])
            if player_1 is None or player_2 is None:
                raise ValueError("Jogadores da sala nao encontrados")

            match = await self.matchmaking.create_match_for_players(player_1, player_2, room_id=room_id)
            await self.lobby_service.bind_room_match(room_id, match["match_id"])
            return

        raise ValueError("Falha ao entrar na sala")

    async def reconnect(self, websocket: WebSocket, player_id: str) -> bool:
        player = await self.repository.get_player(player_id)
        if player is None:
            await websocket.send_json({"type": "error", "message": "Sessao nao encontrada"})
            self.metrics.inc_errors("session_not_found")
            return False

        player = await self._normalize_stale_player_connection(player)

        nickname_reserved = await self.repository.refresh_nickname_claim(
            player["nickname"],
            player_id,
            ttl_seconds=self._nickname_claim_ttl(),
        )
        if not nickname_reserved:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "Este nickname esta em uso por outra sessao ativa",
                }
            )
            self.metrics.inc_errors("nickname_conflict")
            return False

        await self.connection_manager.bind_player(player_id, websocket)

        now = int(time.time())
        player["connected"] = True
        player["connected_server"] = self.settings.server_id
        player["last_seen"] = now
        await self.repository.save_player(player)
        await self.repository.set_heartbeat(player_id, now)
        self.metrics.inc_reconnections()

        await self.connection_manager.send_local(
            player_id,
            {"type": "reconnected", "message": "Reconectado com sucesso"},
        )
        logger.info(
            "player_reconnected",
            extra={
                "event": "player_reconnected",
                "player_id": player_id,
                "status": player["status"],
            },
        )

        if player["status"] == "waiting":
            await self.dispatcher.send_to_player(
                player_id,
                {
                    "type": "room_joined",
                    "room_id": player.get("room_id"),
                    "message": "Sessao aguardando adversario restaurada",
                },
            )
            return True

        match_id = player.get("match_id")
        if not match_id:
            return True

        match = await self.repository.get_match(match_id)
        if match is None:
            return True

        if match["status"] == "finished":
            await self._send_game_state(match, player_id)
            await self._send_game_over_to_player(match, player_id)
            return True

        deadline = match["disconnect_deadlines"].get(player_id)
        if deadline is not None:
            if deadline < now:
                await self._finish_by_abandonment(match, player_id)
                updated = await self.repository.get_match(match_id)
                if updated is not None:
                    await self._notify_game_over(updated)
                return True
            match["disconnect_deadlines"][player_id] = None
            match["updated_at"] = now
            await self.repository.save_match(match)
            await self.repository.remove_reconnect_deadline(match_id, player_id)
            opp_id = opponent_id(match, player_id)
            await self.dispatcher.send_to_player(
                opp_id,
                {"type": "reconnected", "message": "Seu adversario reconectou"},
            )

        await self._send_state_to_match_players(match)
        return True

    async def heartbeat(self, player_id: str) -> None:
        player = await self.repository.get_player(player_id)
        if player is None:
            return
        now = int(time.time())
        player["last_seen"] = now
        await self.repository.save_player(player)
        await self.repository.set_heartbeat(player_id, now)
        await self.repository.refresh_nickname_claim(
            player["nickname"],
            player_id,
            ttl_seconds=self._nickname_claim_ttl(),
        )

    async def disconnect(self, player_id: str) -> None:
        player = await self.repository.get_player(player_id)
        if player is None:
            return

        now = int(time.time())
        self.metrics.inc_disconnections()
        logger.info(
            "player_disconnected",
            extra={
                "event": "player_disconnected",
                "player_id": player_id,
                "status": player["status"],
            },
        )
        await self.repository.refresh_nickname_claim(
            player["nickname"],
            player_id,
            ttl_seconds=self.settings.reconnect_timeout_seconds + 5,
        )
        await self._mark_player_waiting_reconnect(player, now, preserve_nickname=True)
        logger.info(
            "disconnect_deadline_set",
            extra={
                "event": "disconnect_deadline_set",
                "match_id": player.get("match_id"),
                "player_id": player_id,
                "deadline": now + self.settings.reconnect_timeout_seconds,
            },
        )

    async def recover_players_from_dead_servers(self) -> None:
        player_ids = await self.repository.list_player_ids()
        now = int(time.time())
        for player_id in player_ids:
            player = await self.repository.get_player(player_id)
            if player is None:
                continue
            connected_server = player.get("connected_server")
            if not player.get("connected") or not connected_server:
                continue
            if await self._server_is_alive(connected_server):
                continue

            await self._mark_player_waiting_reconnect(
                player,
                now,
                preserve_nickname=True,
                caused_by_server_failure=True,
            )
            logger.warning(
                "player_marked_for_failover",
                extra={
                    "event": "player_marked_for_failover",
                    "player_id": player_id,
                    "failed_server_id": connected_server,
                    "status": player.get("status"),
                },
            )

    async def _resume_disconnected_session_from_login(self, websocket: WebSocket, player: Player) -> bool:
        player_id = player["player_id"]
        if player.get("status") != "playing":
            return False

        match_id = player.get("match_id")
        if not match_id:
            return False

        match = await self.repository.get_match(match_id)
        if match is None:
            return False

        now = int(time.time())
        deadline = match["disconnect_deadlines"].get(player_id)
        if deadline is not None and deadline < now:
            await self._finish_by_abandonment(match, player_id)
            updated = await self.repository.get_match(match_id)
            if updated is not None:
                await self._notify_game_over(updated)
            return False

        await self.connection_manager.bind_player(player_id, websocket)
        player["connected"] = True
        player["connected_server"] = self.settings.server_id
        player["last_seen"] = now
        await self.repository.save_player(player)
        await self.repository.set_heartbeat(player_id, now)
        await self.repository.refresh_nickname_claim(
            player["nickname"],
            player_id,
            ttl_seconds=self._nickname_claim_ttl(),
        )
        self.metrics.inc_reconnections()

        await self.connection_manager.send_local(
            player_id,
            {
                "type": "connected",
                "player_id": player_id,
                "message": "Sessao restaurada com sucesso",
            },
        )

        if deadline is not None:
            match["disconnect_deadlines"][player_id] = None
            match["updated_at"] = now
            await self.repository.save_match(match)
            await self.repository.remove_reconnect_deadline(match_id, player_id)
            opp_id = opponent_id(match, player_id)
            await self.dispatcher.send_to_player(
                opp_id,
                {"type": "reconnected", "message": "Seu adversario reconectou"},
            )

        await self._send_state_to_match_players(match)
        return True

    async def guess_letter(self, player_id: str, match_id: str, letter_raw: str) -> None:
        letter = normalize_letter(letter_raw)
        if len(letter) != 1 or not letter.isalpha():
            self.metrics.inc_errors("invalid_letter")
            await self.dispatcher.send_error(player_id, "Jogada invalida")
            return

        lock_key = f"lock:match:{match_id}"
        token = await self._wait_lock(lock_key, retries=20, delay=0.05)
        if token is None:
            self.metrics.inc_errors("busy_match")
            await self.dispatcher.send_error(player_id, "Partida ocupada, tente novamente")
            return

        match: Optional[MatchState] = None
        try:
            match = await self.repository.get_match(match_id)
            if match is None:
                self.metrics.inc_errors("missing_match")
                await self.dispatcher.send_error(player_id, "Partida nao encontrada")
                return

            if match["status"] != "active":
                await self._send_game_over_to_player(match, player_id)
                return

            if player_id not in match["player_ids"]:
                self.metrics.inc_errors("invalid_player")
                await self.dispatcher.send_error(player_id, "Voce nao participa desta partida")
                return

            if match["turn"] != player_id:
                self.metrics.inc_errors("invalid_turn")
                await self.dispatcher.send_error(player_id, "Nao e sua vez")
                return

            player_wrong_letters = match["wrong_letters_by_player"].get(player_id, [])
            if letter in match["correct_letters"] or letter in player_wrong_letters:
                self.metrics.inc_errors("repeated_letter")
                await self.dispatcher.send_error(player_id, "Letra ja utilizada")
                return

            opp_id = opponent_id(match, player_id)
            if letter in match["current_word"]:
                match["correct_letters"].append(letter)
                if solved_word(match["current_word"], match["correct_letters"]):
                    await self._complete_round(match, winner_id=player_id, reason="word_solved")
                else:
                    match["turn"] = opp_id
                    match["updated_at"] = int(time.time())
                    await self.repository.save_match(match)
            else:
                match["wrong_letters_by_player"].setdefault(player_id, []).append(letter)
                match["errors_by_player"][player_id] = match["errors_by_player"].get(player_id, 0) + 1
                if match["errors_by_player"][player_id] >= self.settings.max_errors:
                    await self._complete_round(match, winner_id=opp_id, reason="max_errors")
                else:
                    match["turn"] = opp_id
                    match["updated_at"] = int(time.time())
                    await self.repository.save_match(match)

            logger.info(
                "guess_processed",
                extra={
                    "event": "guess_processed",
                    "match_id": match_id,
                    "round": match["current_round"],
                    "player_id": player_id,
                    "letter": letter,
                    "hit": letter in match["current_word"],
                    "player_errors": match["errors_by_player"].get(player_id, 0),
                    "turn": match["turn"],
                },
            )

        finally:
            await self.repository.release_lock(lock_key, token)

        if match is None:
            return
        if match["status"] == "finished":
            await self._notify_game_over(match)
        else:
            await self._send_state_to_match_players(match)

    async def guess_word(self, player_id: str, match_id: str, word_raw: str) -> None:
        guess = normalize_word_guess(word_raw)
        if not guess or len(guess) < 2:
            self.metrics.inc_errors("invalid_word_guess")
            await self.dispatcher.send_error(player_id, "Palavra invalida")
            return

        lock_key = f"lock:match:{match_id}"
        token = await self._wait_lock(lock_key, retries=20, delay=0.05)
        if token is None:
            self.metrics.inc_errors("busy_match")
            await self.dispatcher.send_error(player_id, "Partida ocupada, tente novamente")
            return

        match: Optional[MatchState] = None
        try:
            match = await self.repository.get_match(match_id)
            if match is None:
                self.metrics.inc_errors("missing_match")
                await self.dispatcher.send_error(player_id, "Partida nao encontrada")
                return

            if match["status"] != "active":
                await self._send_game_over_to_player(match, player_id)
                return

            if player_id not in match["player_ids"]:
                self.metrics.inc_errors("invalid_player")
                await self.dispatcher.send_error(player_id, "Voce nao participa desta partida")
                return

            if match["turn"] != player_id:
                self.metrics.inc_errors("invalid_turn")
                await self.dispatcher.send_error(player_id, "Nao e sua vez")
                return

            opp_id = opponent_id(match, player_id)
            if guess == match["current_word"]:
                await self._complete_round(match, winner_id=player_id, reason="full_word_hit")
            else:
                match["scores"][opp_id] = match["scores"].get(opp_id, 0) + 1
                match["round_history"].append(
                    {
                        "round_number": match["current_round"],
                        "word": match["current_word"],
                        "theme": match["current_theme"],
                        "winner": opp_id,
                        "reason": "wrong_word_guess",
                        "errors": match["errors_by_player"].get(player_id, 0),
                        "player_errors": dict(match["errors_by_player"]),
                        "finished_at": int(time.time()),
                    }
                )
                await self._finish_match(match, winner_id=opp_id, reason="wrong_word_guess")

            logger.info(
                "word_guess_processed",
                extra={
                    "event": "word_guess_processed",
                    "match_id": match_id,
                    "round": match["current_round"],
                    "player_id": player_id,
                    "guess": guess,
                    "success": guess == match["current_word"],
                },
            )
        finally:
            await self.repository.release_lock(lock_key, token)

        if match is None:
            return
        if match["status"] == "finished":
            await self._notify_game_over(match)
        else:
            await self._send_state_to_match_players(match)

    async def resolve_expired_deadline(self, match_id: str, disconnected_player_id: str) -> None:
        lock_key = f"lock:match:{match_id}"
        token = await self.repository.acquire_lock(lock_key, ttl_ms=4000)
        if token is None:
            return
        try:
            match = await self.repository.get_match(match_id)
            if match is None or match["status"] != "active":
                await self.repository.remove_reconnect_deadline(match_id, disconnected_player_id)
                return

            deadline = match["disconnect_deadlines"].get(disconnected_player_id)
            now = int(time.time())
            if deadline is None or deadline > now:
                return

            await self._finish_by_abandonment(match, disconnected_player_id)
        finally:
            await self.repository.release_lock(lock_key, token)

        updated = await self.repository.get_match(match_id)
        if updated is not None and updated["status"] == "finished":
            await self._notify_game_over(updated)

    async def _wait_lock(self, key: str, retries: int, delay: float) -> Optional[str]:
        for _ in range(retries):
            token = await self.repository.acquire_lock(key, ttl_ms=5000)
            if token is not None:
                return token
            await asyncio.sleep(delay)
        return None

    def _round_start_player(self, match: MatchState, round_number: int) -> str:
        player_ids = match["player_ids"]
        base_index = 0 if match["starting_player_id"] == player_ids[0] else 1
        index = (base_index + (round_number - 1)) % 2
        return player_ids[index]

    async def _complete_round(self, match: MatchState, winner_id: str | None, reason: str) -> None:
        now = int(time.time())
        if winner_id is not None and winner_id in match["scores"]:
            match["scores"][winner_id] += 1

        match["round_history"].append(
            {
                "round_number": match["current_round"],
                "word": match["current_word"],
                "theme": match["current_theme"],
                "winner": winner_id,
                "reason": reason,
                "errors": max(match["errors_by_player"].values(), default=0),
                "player_errors": dict(match["errors_by_player"]),
                "finished_at": now,
            }
        )

        majority = (match["total_rounds"] // 2) + 1
        if winner_id is not None and match["scores"].get(winner_id, 0) >= majority:
            await self._finish_match(match, winner_id=winner_id, reason="best_of_three")
            return

        if match["current_round"] >= match["total_rounds"]:
            first_id, second_id = match["player_ids"]
            first_score = match["scores"].get(first_id, 0)
            second_score = match["scores"].get(second_id, 0)
            if first_score > second_score:
                await self._finish_match(match, winner_id=first_id, reason="best_of_three")
            elif second_score > first_score:
                await self._finish_match(match, winner_id=second_id, reason="best_of_three")
            else:
                await self._finish_match(match, winner_id=None, reason="best_of_three_draw")
            return

        next_round = match["current_round"] + 1
        used_words = {result["word"] for result in match["round_history"]}
        next_entry = self.word_bank.random_entry(exclude_words=used_words)

        match["current_round"] = next_round
        match["current_word"] = next_entry["word"]
        match["current_theme"] = next_entry["theme"]
        match["correct_letters"] = []
        match["wrong_letters_by_player"] = {pid: [] for pid in match["player_ids"]}
        match["errors_by_player"] = {pid: 0 for pid in match["player_ids"]}
        match["turn"] = self._round_start_player(match, next_round)
        match["updated_at"] = now

        await self.repository.save_match(match)
        logger.info(
            "round_started",
            extra={
                "event": "round_started",
                "match_id": match["match_id"],
                "round": next_round,
                "theme": match["current_theme"],
                "turn_player": match["turn"],
            },
        )

    async def _finish_by_abandonment(self, match: MatchState, disconnected_player_id: str) -> None:
        winner_id = opponent_id(match, disconnected_player_id)
        await self._finish_match(match, winner_id=winner_id, reason="abandonment")
        await self.repository.remove_reconnect_deadline(match["match_id"], disconnected_player_id)

    async def _finish_match(self, match: MatchState, winner_id: Optional[str], reason: str) -> None:
        match["status"] = "finished"
        match["winner"] = winner_id
        match["reason"] = reason
        match["updated_at"] = int(time.time())

        for pid in match["player_ids"]:
            match["disconnect_deadlines"][pid] = None
            await self.repository.remove_reconnect_deadline(match["match_id"], pid)

        await self.repository.save_match(match)
        await self.repository.remove_active_match(match["match_id"])
        self.metrics.inc_finished_matches(reason)
        logger.info(
            "match_finished",
            extra={
                "event": "match_finished",
                "match_id": match["match_id"],
                "winner_id": winner_id,
                "reason": reason,
                "scores": match["scores"],
            },
        )

        for pid in match["player_ids"]:
            player = await self.repository.get_player(pid)
            if player is None:
                continue
            player["status"] = "idle"
            player["match_id"] = None
            player["room_id"] = None
            player["queue_entered_at"] = None
            await self.repository.save_player(player)
            if not player.get("connected"):
                await self.repository.release_nickname_if_owner(player["nickname"], pid)

        room_id = match.get("room_id")
        if room_id:
            await self.lobby_service.reset_room_after_match(room_id)

    async def _notify_game_over(self, match: MatchState) -> None:
        for pid in match["player_ids"]:
            await self._send_game_state(match, pid)
            await self._send_game_over_to_player(match, pid)

    async def _send_game_over_to_player(self, match: MatchState, player_id: str) -> None:
        opp_id = opponent_id(match, player_id)
        winner = match.get("winner")

        history = []
        for item in match["round_history"]:
            winner_id = item.get("winner")
            history.append(
                {
                    **item,
                    "winner_nickname": match["player_nicknames"].get(winner_id) if winner_id else None,
                }
            )

        await self.dispatcher.send_to_player(
            player_id,
            {
                "type": "game_over",
                "winner": winner,
                "reason": match.get("reason") or "unknown",
                "is_draw": winner is None,
                "message": "Partida encerrada",
                "your_score": match["scores"].get(player_id, 0),
                "opponent_score": match["scores"].get(opp_id, 0),
                "round_history": history,
            },
        )

    async def _send_state_to_match_players(self, match: MatchState) -> None:
        for player_id in match["player_ids"]:
            await self._send_game_state(match, player_id)

    async def _send_game_state(self, match: MatchState, player_id: str) -> None:
        payload = build_game_state_payload(match, player_id, self.settings.max_errors)
        await self.dispatcher.send_to_player(player_id, payload)
