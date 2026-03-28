import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const PLAYER_STORAGE_KEY = "hangman_player_id";
const NICKNAME_STORAGE_KEY = "hangman_nickname";
const ROOM_POLL_INTERVAL_MS = 2000;

function buildWsUrl() {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ws`;
}

function reasonLabel(reason) {
  if (reason === "best_of_three") return "Melhor de 3 rodadas";
  if (reason === "best_of_three_draw") return "Empate apos 3 rodadas";
  if (reason === "word_solved") return "Palavra completada";
  if (reason === "max_errors") return "Forca completa (6 erros)";
  if (reason === "full_word_hit") return "Chute correto da palavra";
  if (reason === "wrong_word_guess") return "Chute de palavra errado";
  if (reason === "abandonment") return "Vitoria por abandono";
  return "Partida encerrada";
}

function initials(name) {
  if (!name) return "?";
  const tokens = String(name).trim().split(" ").filter(Boolean);
  if (tokens.length === 1) return tokens[0][0]?.toUpperCase() || "?";
  return `${tokens[0][0] || ""}${tokens[1][0] || ""}`.toUpperCase();
}

function roomSlots(room) {
  const players = room.players || [];
  return [players[0] || null, players[1] || null];
}

function compareRooms(left, right) {
  const leftId = String(left?.room_id || "");
  const rightId = String(right?.room_id || "");

  const leftSala = /^sala-(\d+)$/i.exec(leftId);
  const rightSala = /^sala-(\d+)$/i.exec(rightId);

  if (leftSala && rightSala) {
    return Number(leftSala[1]) - Number(rightSala[1]);
  }
  if (leftSala) return -1;
  if (rightSala) return 1;

  const leftCreated = Number(left?.created_at || 0);
  const rightCreated = Number(right?.created_at || 0);
  if (leftCreated !== rightCreated) return leftCreated - rightCreated;

  const leftName = String(left?.name || leftId);
  const rightName = String(right?.name || rightId);
  return leftName.localeCompare(rightName);
}

export default function App() {
  const wsUrl = useMemo(() => buildWsUrl(), []);
  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const heartbeatTimerRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const manualCloseRef = useRef(false);
  const playerIdRef = useRef(localStorage.getItem(PLAYER_STORAGE_KEY) || "");

  const [phase, setPhase] = useState("name");
  const [isConnected, setIsConnected] = useState(false);

  const [nicknameInput, setNicknameInput] = useState(localStorage.getItem(NICKNAME_STORAGE_KEY) || "");
  const [nickname, setNickname] = useState(localStorage.getItem(NICKNAME_STORAGE_KEY) || "");
  const [playerId, setPlayerId] = useState("");
  const [feedback, setFeedback] = useState("Digite seu nome para entrar no lobby");

  const [rooms, setRooms] = useState([]);
  const [activeMatches, setActiveMatches] = useState(0);
  const [waitingRooms, setWaitingRooms] = useState(0);
  const [newRoomName, setNewRoomName] = useState("");
  const [currentRoomId, setCurrentRoomId] = useState("");

  const [matchId, setMatchId] = useState("");
  const [opponent, setOpponent] = useState("");
  const [roundNumber, setRoundNumber] = useState(1);
  const [totalRounds, setTotalRounds] = useState(3);
  const [theme, setTheme] = useState("-");
  const [maskedWord, setMaskedWord] = useState("");
  const [correctLetters, setCorrectLetters] = useState([]);
  const [wrongLetters, setWrongLetters] = useState([]);
  const [opponentWrongLetters, setOpponentWrongLetters] = useState([]);
  const [errors, setErrors] = useState(0);
  const [opponentErrors, setOpponentErrors] = useState(0);
  const [remainingErrors, setRemainingErrors] = useState(6);
  const [opponentRemainingErrors, setOpponentRemainingErrors] = useState(6);
  const [turn, setTurn] = useState("");
  const [isYourTurn, setIsYourTurn] = useState(false);
  const [canGuess, setCanGuess] = useState(false);
  const [yourScore, setYourScore] = useState(0);
  const [opponentScore, setOpponentScore] = useState(0);
  const [roundHistory, setRoundHistory] = useState([]);
  const [revealedWord, setRevealedWord] = useState("");
  const [letterInput, setLetterInput] = useState("");
  const [wordInput, setWordInput] = useState("");

  const [winner, setWinner] = useState("");
  const [isDraw, setIsDraw] = useState(false);
  const [gameOverReason, setGameOverReason] = useState("");

  useEffect(() => {
    playerIdRef.current = playerId;
  }, [playerId]);

  const loadLobby = useCallback(async () => {
    try {
      const response = await fetch("/api/lobby");
      if (!response.ok) return;
      const payload = await response.json();
      const nextRooms = [...(payload.rooms || [])].sort(compareRooms);
      setRooms(nextRooms);
      setActiveMatches(payload.active_matches || 0);
      setWaitingRooms(payload.waiting_rooms || 0);
    } catch (_error) {
      // keep UI stable if lobby fetch fails temporarily.
    }
  }, []);

  useEffect(() => {
    loadLobby();
    const timer = window.setInterval(loadLobby, ROOM_POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [loadLobby]);

  useEffect(() => {
    if (!playerId) return;
    heartbeatTimerRef.current = window.setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "heartbeat", player_id: playerId }));
      }
    }, 5000);
    return () => {
      if (heartbeatTimerRef.current) window.clearInterval(heartbeatTimerRef.current);
    };
  }, [playerId]);

  useEffect(() => {
    return () => {
      clearReconnectTimer();
      if (heartbeatTimerRef.current) window.clearInterval(heartbeatTimerRef.current);
      manualCloseRef.current = true;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  function clearMatchState() {
    setMatchId("");
    setOpponent("");
    setRoundNumber(1);
    setTotalRounds(3);
    setTheme("-");
    setMaskedWord("");
    setCorrectLetters([]);
    setWrongLetters([]);
    setOpponentWrongLetters([]);
    setErrors(0);
    setOpponentErrors(0);
    setRemainingErrors(6);
    setOpponentRemainingErrors(6);
    setTurn("");
    setIsYourTurn(false);
    setCanGuess(false);
    setYourScore(0);
    setOpponentScore(0);
    setRoundHistory([]);
    setRevealedWord("");
    setLetterInput("");
    setWordInput("");
    setWinner("");
    setIsDraw(false);
    setGameOverReason("");
  }

  function clearReconnectTimer() {
    if (reconnectTimerRef.current) {
      window.clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptsRef.current = 0;
  }

  function scheduleReconnect() {
    if (!playerIdRef.current || reconnectTimerRef.current) return;
    const delay = Math.min(1000 * 2 ** reconnectAttemptsRef.current, 5000);
    reconnectAttemptsRef.current += 1;
    reconnectTimerRef.current = window.setTimeout(() => {
      reconnectTimerRef.current = null;
      openSocket({ type: "reconnect", player_id: playerIdRef.current });
    }, delay);
  }

  function openSocket(firstMessage) {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(firstMessage));
      return;
    }

    if (wsRef.current && wsRef.current.readyState === WebSocket.CONNECTING) {
      return;
    }

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      clearReconnectTimer();
      ws.send(JSON.stringify(firstMessage));
    };

    ws.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      handleServerEvent(payload);
    };

    ws.onclose = () => {
      setIsConnected(false);
      wsRef.current = null;
      if (manualCloseRef.current) {
        manualCloseRef.current = false;
        return;
      }
      if (playerIdRef.current) {
        setPhase("reconnecting");
        setFeedback("Conexao perdida. Tentando reconectar...");
        scheduleReconnect();
      }
    };

    ws.onerror = () => setFeedback("Erro de conexao com servidor");
  }

  function handleServerEvent(payload) {
    if (payload.type === "connected") {
      const id = payload.player_id;
      setPlayerId(id);
      playerIdRef.current = id;
      localStorage.setItem(PLAYER_STORAGE_KEY, id);
      if (nickname) localStorage.setItem(NICKNAME_STORAGE_KEY, nickname);
      setPhase("lobby");
      setFeedback("Conectado! Escolha uma sala.");
      loadLobby();
      return;
    }

    if (payload.type === "reconnected") {
      setFeedback(payload.message || "Reconectado");
      if (matchId) {
        setPhase("match");
      } else {
        setPhase("lobby");
      }
      return;
    }

    if (payload.type === "queue_update") {
      setFeedback(payload.message || "Aguardando adversario");
      return;
    }

    if (payload.type === "room_joined") {
      setCurrentRoomId(payload.room_id || "");
      setPhase("lobby");
      setFeedback(payload.message || "Voce entrou na sala");
      loadLobby();
      return;
    }

    if (payload.type === "match_found") {
      setMatchId(payload.match_id || "");
      setCurrentRoomId((prev) => payload.room_id || prev);
      setOpponent(payload.opponent || "Adversario");
      if (payload.round_number) setRoundNumber(payload.round_number);
      if (payload.total_rounds) setTotalRounds(payload.total_rounds);
      if (payload.theme) setTheme(payload.theme);
      setPhase("match");
      setFeedback(payload.message || "Partida iniciada");
      loadLobby();
      return;
    }

    if (payload.type === "game_state") {
      setMatchId(payload.match_id || "");
      setRoundNumber(payload.round_number || 1);
      setTotalRounds(payload.total_rounds || 3);
      setTheme(payload.theme || "-");
      setMaskedWord(payload.masked_word || "");
      setCorrectLetters(payload.correct_letters || []);
      setWrongLetters(payload.wrong_letters || []);
      setOpponentWrongLetters(payload.opponent_wrong_letters || []);
      setErrors(payload.errors || 0);
      setOpponentErrors(payload.opponent_errors || 0);
      setRemainingErrors(payload.remaining_errors || 0);
      setOpponentRemainingErrors(payload.opponent_remaining_errors || 0);
      setTurn(payload.turn || "");
      setIsYourTurn(Boolean(payload.is_your_turn));
      setCanGuess(Boolean(payload.can_guess));
      setYourScore(payload.your_score || 0);
      setOpponentScore(payload.opponent_score || 0);
      setRoundHistory(payload.round_history || []);
      setRevealedWord(payload.revealed_word || "");
      if (payload.opponent) setOpponent(payload.opponent);
      if (payload.status === "finished") {
        setPhase("finished");
      } else {
        setPhase("match");
      }
      return;
    }

    if (payload.type === "opponent_disconnected") {
      setFeedback(payload.message || "Adversario desconectado");
      return;
    }

    if (payload.type === "game_over") {
      setWinner(payload.winner || "");
      setIsDraw(Boolean(payload.is_draw));
      setGameOverReason(payload.reason || "");
      setYourScore(payload.your_score || 0);
      setOpponentScore(payload.opponent_score || 0);
      if (payload.round_history) setRoundHistory(payload.round_history);
      setPhase("finished");
      if (payload.is_draw) {
        setFeedback("Empate");
      } else if (payload.winner && payload.winner === playerIdRef.current) {
        setFeedback("Voce venceu");
      } else {
        setFeedback("Voce perdeu");
      }
      loadLobby();
      return;
    }

    if (payload.type === "error") {
      const message = payload.message || "Erro";
      setFeedback(message);
      if (message.toLowerCase().includes("sessao")) {
        localStorage.removeItem(PLAYER_STORAGE_KEY);
        setPlayerId("");
        playerIdRef.current = "";
        setPhase("name");
      }
    }
  }

  function resetConnectionForNewLogin(options = {}) {
    const { clearPlayerStorage = true } = options;
    clearReconnectTimer();
    if (heartbeatTimerRef.current) {
      window.clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    manualCloseRef.current = true;
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }

    if (clearPlayerStorage) {
      localStorage.removeItem(PLAYER_STORAGE_KEY);
    }
    setPlayerId("");
    playerIdRef.current = "";

    clearMatchState();
    setCurrentRoomId("");
    setIsConnected(false);
  }

  function handleEnterLobby(event) {
    event.preventDefault();
    const cleanName = nicknameInput.trim();
    if (!cleanName) {
      setFeedback("Informe um nome valido");
      return;
    }

    const storedPlayerId = localStorage.getItem(PLAYER_STORAGE_KEY) || "";
    const storedNickname = localStorage.getItem(NICKNAME_STORAGE_KEY) || "";
    const shouldTryReconnect =
      Boolean(storedPlayerId) && storedNickname.toLowerCase() === cleanName.toLowerCase();

    if (shouldTryReconnect) {
      resetConnectionForNewLogin({ clearPlayerStorage: false });
      setNickname(cleanName);
      setPlayerId(storedPlayerId);
      playerIdRef.current = storedPlayerId;
      setPhase("reconnecting");
      setFeedback("Tentando restaurar sua sessao...");
      openSocket({ type: "reconnect", player_id: storedPlayerId });
      return;
    }

    resetConnectionForNewLogin();
    setNickname(cleanName);
    localStorage.setItem(NICKNAME_STORAGE_KEY, cleanName);

    setFeedback("Conectando...");
    openSocket({ type: "register_player", nickname: cleanName });
  }

  function handleSwitchUser() {
    resetConnectionForNewLogin();
    setNickname("");
    setNicknameInput("");
    localStorage.removeItem(NICKNAME_STORAGE_KEY);
    setPhase("name");
    setFeedback("Digite seu nome para entrar no lobby");
    loadLobby();
  }

  function handleJoinRoom(roomId) {
    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN)) {
      setFeedback("Conexao indisponivel");
      return;
    }
    if (!playerId) {
      setFeedback("Jogador nao registrado");
      return;
    }
    wsRef.current.send(JSON.stringify({ type: "join_room", player_id: playerId, room_id: roomId }));
  }

  async function handleCreateRoom(event) {
    event.preventDefault();
    const name = newRoomName.trim();
    if (!name) return;

    try {
      const response = await fetch("/api/lobby/rooms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) {
        setFeedback("Falha ao criar sala");
        return;
      }

      const payload = await response.json();
      const createdRoomId = payload?.room?.room_id || "";
      setNewRoomName("");
      setFeedback("Sala criada com sucesso");
      await loadLobby();
      if (createdRoomId) {
        handleJoinRoom(createdRoomId);
      }
    } catch (_error) {
      setFeedback("Erro ao criar sala");
    }
  }

  function handleGuessLetter(event) {
    event.preventDefault();
    const letter = letterInput.trim().slice(0, 1).toUpperCase();
    if (!letter || !playerId || !matchId) return;
    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN)) return;
    wsRef.current.send(
      JSON.stringify({
        type: "guess_letter",
        player_id: playerId,
        match_id: matchId,
        letter,
      })
    );
    setLetterInput("");
  }

  function handleGuessWord(event) {
    event.preventDefault();
    const word = wordInput.trim().toUpperCase();
    if (!word || !playerId || !matchId) return;
    if (!(wsRef.current && wsRef.current.readyState === WebSocket.OPEN)) return;
    wsRef.current.send(
      JSON.stringify({
        type: "guess_word",
        player_id: playerId,
        match_id: matchId,
        word,
      })
    );
    setWordInput("");
  }

  function goToLobby() {
    clearMatchState();
    setPhase("lobby");
    setCurrentRoomId("");
    setFeedback("Escolha uma sala para jogar");
    loadLobby();
  }

  function roomButtonState(room) {
    const players = room.players || [];
    const isMyRoom = players.some((p) => p.player_id === playerId);

    if (!isConnected) return { disabled: true, label: "Sem conexao" };
    if (phase === "reconnecting") return { disabled: true, label: "Reconectando" };

    if (room.status === "in_game" && !isMyRoom) return { disabled: true, label: "Em jogo" };
    if (isMyRoom && room.status !== "in_game") return { disabled: true, label: "Aguardando" };
    if (players.length >= (room.max_players || 2) && !isMyRoom) return { disabled: true, label: "Lotada" };

    return { disabled: false, label: "Entrar" };
  }

  const youWon = winner && winner === playerId;

  return (
    <main className="app-shell">
      <section className="panel">
        <header className="topbar">
          <div className="topbar-brand">
            <h1>⚔ Forca Arena</h1>
            <p className="subtitle">Desafie seus amigos em partidas de forca por turnos</p>
          </div>
          <div className="topbar-actions">
            <div className={`status ${isConnected ? "online" : "offline"}`}>{isConnected ? "Conectado" : "Desconectado"}</div>
            {playerId && (
              <button type="button" className="ghost-button" onClick={handleSwitchUser}>
                Trocar jogador
              </button>
            )}
          </div>
        </header>

        <p className="feedback">{feedback}</p>

        {phase === "name" && (
          <section className="hero-card">
            <h2>Entrar no Lobby</h2>
            <p>Digite seu nome para acessar as salas e escolher onde jogar.</p>
            <form className="entry-form" onSubmit={handleEnterLobby}>
              <label htmlFor="nickname">Nome do jogador</label>
              <input
                id="nickname"
                value={nicknameInput}
                onChange={(event) => setNicknameInput(event.target.value)}
                placeholder="Ex: Pedro"
                maxLength={24}
                required
              />
              <button type="submit">Entrar</button>
            </form>
          </section>
        )}

        {(phase === "lobby" || phase === "reconnecting") && (
          <section className="lobby-section">
            {phase === "reconnecting" && (
              <div className="reconnect-banner">Tentando reconectar sua sessao. Aguarde alguns segundos...</div>
            )}

            <div className="lobby-header">
              <div>
                <h2>Salas</h2>
                <p className="muted">
                  Jogador: <strong>{nickname || "-"}</strong>
                </p>
              </div>
              <div className="lobby-stats">
                <span>Salas: {rooms.length}</span>
                <span>Em jogo: {activeMatches}</span>
                <span>Aguardando: {waitingRooms}</span>
              </div>
            </div>

            <form className="create-room" onSubmit={handleCreateRoom}>
              <input
                value={newRoomName}
                onChange={(event) => setNewRoomName(event.target.value)}
                placeholder="Criar sala personalizada"
                maxLength={32}
              />
              <button type="submit">Criar sala</button>
            </form>

            <div className="rooms-grid">
              {rooms.map((room) => {
                const slots = roomSlots(room);
                const action = roomButtonState(room);
                const isCurrent = room.room_id === currentRoomId;
                return (
                  <article className={`room-card ${isCurrent ? "current" : ""}`} key={room.room_id}>
                    <div className="room-head">
                      <h3>{room.name}</h3>
                      <span>{room.current_players || 0}/2</span>
                    </div>

                    <div className="room-body">
                      <Avatar player={slots[0]} isMe={slots[0]?.player_id === playerId} />
                      <span className="vs">VS</span>
                      <Avatar player={slots[1]} isMe={slots[1]?.player_id === playerId} />
                    </div>

                    <p className="room-status">{room.status === "in_game" ? "Partida em andamento" : "Aguardando jogadores"}</p>
                    <button type="button" onClick={() => handleJoinRoom(room.room_id)} disabled={action.disabled}>
                      {action.label}
                    </button>
                  </article>
                );
              })}
            </div>
          </section>
        )}

        {phase === "match" && (
          <section className="game-layout">
            <div className="board">
              <h2>Partida</h2>
              <p>
                Sala: <strong>{currentRoomId || "-"}</strong>
              </p>
              <p>
                Adversario: <strong>{opponent || "..."}</strong>
              </p>
              <p>
                Rodada:{" "}
                <strong>
                  {roundNumber}/{totalRounds}
                </strong>
              </p>
              <p>
                Tema: <strong>{theme}</strong>
              </p>

              <div className="score-row">
                <div>
                  <small>Seu placar</small>
                  <strong>{yourScore}</strong>
                </div>
                <div>
                  <small>Placar do adversario</small>
                  <strong>{opponentScore}</strong>
                </div>
              </div>

              <p className={`turn-label ${isYourTurn ? "my-turn" : ""}`}>
                {isYourTurn ? "Sua vez de jogar" : "Vez do adversario"}
              </p>

              <p className="masked-word">{maskedWord || "_ _ _ _"}</p>
              <p>Letras certas: {correctLetters.join(", ") || "-"}</p>
              <p>Suas letras erradas: {wrongLetters.join(", ") || "-"}</p>
              <p>Letras erradas do adversario: {opponentWrongLetters.join(", ") || "-"}</p>
              <p>
                Seus erros: {errors}/6 (restam {remainingErrors})
              </p>
              <p>
                Erros do adversario: {opponentErrors}/6 (restam {opponentRemainingErrors})
              </p>

              <form className="guess-form" onSubmit={handleGuessLetter}>
                <input
                  placeholder="Letra"
                  value={letterInput}
                  onChange={(event) => setLetterInput(event.target.value.toUpperCase())}
                  maxLength={1}
                  required
                  disabled={!canGuess}
                />
                <button type="submit" disabled={!canGuess}>
                  Jogar letra
                </button>
              </form>

              <form className="guess-word-form" onSubmit={handleGuessWord}>
                <input
                  placeholder="Chutar palavra"
                  value={wordInput}
                  onChange={(event) => setWordInput(event.target.value.toUpperCase())}
                  maxLength={32}
                  required
                  disabled={!canGuess}
                />
                <button type="submit" className="danger" disabled={!canGuess}>
                  Chutar palavra
                </button>
              </form>
              <p className="warning">Se errar o chute de palavra, voce perde a partida automaticamente.</p>
            </div>

            <div className="hangman-box">
              <h3>Forca</h3>
              <HangmanGraphic errors={errors} />
              <p className="hangman-label">Tema da rodada: {theme}</p>
            </div>
          </section>
        )}

        {phase === "finished" && (
          <section className="end-box">
            <h2>{isDraw ? "Empate" : youWon ? "Voce venceu" : "Voce perdeu"}</h2>
            <p>Motivo: {reasonLabel(gameOverReason)}</p>
            <p>
              Placar final: <strong>{yourScore}</strong> x <strong>{opponentScore}</strong>
            </p>
            {revealedWord && (
              <p>
                Ultima palavra: <strong>{revealedWord}</strong>
              </p>
            )}
            <div className="history">
              {roundHistory.length > 0 &&
                roundHistory.map((round) => (
                  <div className="history-item" key={`round-${round.round_number}`}>
                    <strong>Rodada {round.round_number}</strong>
                    <span>
                      Tema: {round.theme} | Palavra: {round.word}
                    </span>
                    <span>
                      Vencedor: {round.winner_nickname || "Ninguem"} | Motivo: {reasonLabel(round.reason)}
                    </span>
                  </div>
                ))}
            </div>
            <button type="button" onClick={goToLobby}>
              Voltar ao lobby
            </button>
          </section>
        )}
      </section>
    </main>
  );
}

function Avatar({ player, isMe }) {
  if (!player) {
    return <div className="avatar ghost" aria-hidden="true" />;
  }

  return (
    <div className={`avatar filled ${isMe ? "me" : ""}`} title={player.nickname}>
      <span>{initials(player.nickname)}</span>
    </div>
  );
}

function HangmanGraphic({ errors }) {
  const safeErrors = Math.max(0, Math.min(6, Number(errors) || 0));
  return (
    <svg viewBox="0 0 240 250" className="hangman-svg" role="img" aria-label={`Forca com ${safeErrors} erros`}>
      <line className="scaffold" x1="20" y1="230" x2="220" y2="230" />
      <line className="scaffold" x1="60" y1="230" x2="60" y2="24" />
      <line className="scaffold" x1="60" y1="24" x2="150" y2="24" />
      <line className="scaffold" x1="150" y1="24" x2="150" y2="48" />

      <circle className={`hangman-part ${safeErrors >= 1 ? "visible" : ""}`} cx="150" cy="68" r="20" />
      <line className={`hangman-part ${safeErrors >= 2 ? "visible" : ""}`} x1="150" y1="88" x2="150" y2="145" />
      <line className={`hangman-part ${safeErrors >= 3 ? "visible" : ""}`} x1="150" y1="105" x2="185" y2="125" />
      <line className={`hangman-part ${safeErrors >= 4 ? "visible" : ""}`} x1="150" y1="105" x2="115" y2="125" />
      <line className={`hangman-part ${safeErrors >= 5 ? "visible" : ""}`} x1="150" y1="145" x2="180" y2="188" />
      <line className={`hangman-part ${safeErrors >= 6 ? "visible" : ""}`} x1="150" y1="145" x2="120" y2="188" />
    </svg>
  );
}
