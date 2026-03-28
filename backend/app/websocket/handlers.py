from fastapi import WebSocket, WebSocketDisconnect

from app.services.game_service import GameService
import logging

logger = logging.getLogger("hangman.ws")


async def websocket_handler(websocket: WebSocket, game_service: GameService) -> None:
    await websocket.accept()
    bound_player_id: str | None = None
    try:
        while True:
            data = await websocket.receive_json()
            event_type = data.get("type")

            if event_type in {"join_queue", "register_player"}:
                if bound_player_id is not None:
                    await websocket.send_json({"type": "error", "message": "Sessao ja iniciada"})
                    continue
                nickname = str(data.get("nickname", "")).strip()
                if not nickname:
                    await websocket.send_json({"type": "error", "message": "Nickname obrigatorio"})
                    continue
                if event_type == "join_queue":
                    bound_player_id = await game_service.join_queue(websocket, nickname)
                else:
                    bound_player_id = await game_service.register_player(websocket, nickname)
                continue

            if event_type == "join_room":
                player_id = str(data.get("player_id", "")).strip()
                room_id = str(data.get("room_id", "")).strip()
                if not player_id or not room_id:
                    await websocket.send_json({"type": "error", "message": "player_id e room_id obrigatorios"})
                    continue
                if bound_player_id is not None and bound_player_id != player_id:
                    await websocket.send_json({"type": "error", "message": "player_id invalido para este socket"})
                    continue
                try:
                    await game_service.join_room(player_id, room_id)
                except ValueError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                continue

            if event_type == "reconnect":
                player_id = str(data.get("player_id", "")).strip()
                if not player_id:
                    await websocket.send_json({"type": "error", "message": "player_id obrigatorio"})
                    continue
                if bound_player_id is not None and bound_player_id != player_id:
                    await websocket.send_json({"type": "error", "message": "Sessao ja vinculada ao socket"})
                    continue
                reconnected = await game_service.reconnect(websocket, player_id)
                if reconnected:
                    bound_player_id = player_id
                    logger.info(
                        "ws_reconnect",
                        extra={"event": "ws_reconnect", "player_id": player_id},
                    )
                continue

            if event_type == "guess_letter":
                player_id = str(data.get("player_id", "")).strip()
                match_id = str(data.get("match_id", "")).strip()
                letter = str(data.get("letter", "")).strip()
                if not player_id or not match_id or not letter:
                    await websocket.send_json({"type": "error", "message": "Dados incompletos"})
                    continue
                if bound_player_id is not None and bound_player_id != player_id:
                    await websocket.send_json({"type": "error", "message": "player_id invalido para este socket"})
                    continue
                await game_service.guess_letter(player_id, match_id, letter)
                continue

            if event_type == "guess_word":
                player_id = str(data.get("player_id", "")).strip()
                match_id = str(data.get("match_id", "")).strip()
                word = str(data.get("word", "")).strip()
                if not player_id or not match_id or not word:
                    await websocket.send_json({"type": "error", "message": "Dados incompletos"})
                    continue
                if bound_player_id is not None and bound_player_id != player_id:
                    await websocket.send_json({"type": "error", "message": "player_id invalido para este socket"})
                    continue
                await game_service.guess_word(player_id, match_id, word)
                continue

            if event_type == "heartbeat":
                player_id = str(data.get("player_id", "")).strip()
                if bound_player_id is not None and bound_player_id != player_id:
                    await websocket.send_json({"type": "error", "message": "player_id invalido para heartbeat"})
                    continue
                if player_id:
                    await game_service.heartbeat(player_id)
                continue

            await websocket.send_json({"type": "error", "message": "Evento desconhecido"})
    except WebSocketDisconnect:
        logger.info("ws_disconnected", extra={"event": "ws_disconnected"})
    finally:
        player_id = await game_service.connection_manager.unbind_websocket(websocket)
        if player_id is None:
            player_id = bound_player_id
        if player_id is not None:
            await game_service.disconnect(player_id)
