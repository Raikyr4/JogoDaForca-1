from typing import Dict, List, Literal, Optional, TypedDict


PlayerStatus = Literal["idle", "waiting", "playing", "finished"]
MatchStatus = Literal["active", "finished"]


class Player(TypedDict):
    player_id: str
    nickname: str
    status: PlayerStatus
    match_id: Optional[str]
    room_id: Optional[str]
    connected_server: Optional[str]
    connected: bool
    last_seen: int
    queue_entered_at: Optional[int]


class RoundResult(TypedDict):
    round_number: int
    word: str
    theme: str
    winner: Optional[str]
    reason: str
    errors: int
    player_errors: Dict[str, int]
    finished_at: int


class MatchState(TypedDict):
    match_id: str
    player_ids: List[str]
    player_nicknames: Dict[str, str]
    room_id: Optional[str]
    total_rounds: int
    current_round: int
    starting_player_id: str
    turn: str
    current_word: str
    current_theme: str
    correct_letters: List[str]
    wrong_letters_by_player: Dict[str, List[str]]
    errors_by_player: Dict[str, int]
    scores: Dict[str, int]
    round_history: List[RoundResult]
    status: MatchStatus
    winner: Optional[str]
    reason: Optional[str]
    disconnect_deadlines: Dict[str, Optional[int]]
    created_at: int
    updated_at: int
