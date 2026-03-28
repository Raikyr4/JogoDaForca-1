from app.models.types import MatchState


def normalize_letter(raw: str) -> str:
    return raw.strip().upper()


def normalize_word_guess(raw: str) -> str:
    return "".join(raw.strip().upper().split())


def masked_word(word: str, correct_letters: list[str]) -> str:
    letters = set(correct_letters)
    return " ".join([char if char in letters else "_" for char in word])


def solved_word(word: str, correct_letters: list[str]) -> bool:
    return set(word).issubset(set(correct_letters))


def opponent_id(match: MatchState, player_id: str) -> str:
    return match["player_ids"][1] if match["player_ids"][0] == player_id else match["player_ids"][0]


def build_game_state_payload(match: MatchState, player_id: str, max_errors: int) -> dict:
    opp_id = opponent_id(match, player_id)
    your_errors = int(match["errors_by_player"].get(player_id, 0))
    opponent_errors = int(match["errors_by_player"].get(opp_id, 0))
    return {
        "type": "game_state",
        "match_id": match["match_id"],
        "status": match["status"],
        "round_number": match["current_round"],
        "total_rounds": match["total_rounds"],
        "theme": match["current_theme"],
        "masked_word": masked_word(match["current_word"], match["correct_letters"]),
        "correct_letters": match["correct_letters"],
        "wrong_letters": match["wrong_letters_by_player"].get(player_id, []),
        "opponent_wrong_letters": match["wrong_letters_by_player"].get(opp_id, []),
        "errors": your_errors,
        "opponent_errors": opponent_errors,
        "remaining_errors": max(0, max_errors - your_errors),
        "opponent_remaining_errors": max(0, max_errors - opponent_errors),
        "turn": match["turn"],
        "is_your_turn": match["status"] == "active" and match["turn"] == player_id,
        "can_guess": match["status"] == "active" and match["turn"] == player_id,
        "opponent": match["player_nicknames"][opp_id],
        "your_score": match["scores"].get(player_id, 0),
        "opponent_score": match["scores"].get(opp_id, 0),
        "word_length": len(match["current_word"]),
        "revealed_word": match["current_word"] if match["status"] == "finished" else None,
        "round_history": match["round_history"],
    }
