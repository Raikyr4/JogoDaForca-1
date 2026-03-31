# Revisão detalhada dos requisitos (Backend + integração cliente)

Este documento descreve como o projeto trata cada requisito solicitado no enunciado, com foco no **backend** e nos pontos de integração com cliente/infra.

---

## 1) Preocupação com latência

### Como está tratado

- O backend usa comunicação em **WebSocket** para eventos de jogo em tempo real (`/ws`), evitando polling para jogadas e estados de partida.
- A maior parte do estado compartilhado está em **Redis** (fila, partidas, jogadores, deadlines), reduzindo custo de sincronização entre instâncias.
- Operações críticas de concorrência usam lock com TTL curto em Redis (`acquire_lock/release_lock`) para evitar corrupção de estado sem bloquear por longos períodos.
- Há controle de tentativas para locks em ações de jogada (`_wait_lock`), retornando erro amigável quando a partida está ocupada em vez de travar o fluxo.

### Riscos e mitigação já implementada

- Evita race condition entre servidores no matchmaking com `lock:matchmaking`.
- Evita race em jogadas da mesma partida com `lock:match:<match_id>`.
- Nginx com balanceamento `least_conn` entre duas instâncias de jogo.

---

## 2) Monitoramento dos serviços

### Como está tratado

- Backend expõe métricas Prometheus (ex.: conexões WS, fila de espera, partidas ativas/finalizadas, reconexões, erros).
- Stack de observabilidade inclui **Prometheus + Grafana + Loki + Promtail** para métricas e logs.
- Logging estruturado no backend para eventos importantes (`player_connected`, `match_created`, `guess_processed`, `match_finished`, etc.).

### O que medir para validação da apresentação

- Latência percebida por evento de jogada.
- Taxa de reconexão e abandonos.
- Tamanho médio da fila e tempo de espera.
- Erros por tipo (`hangman_errors_total`).

---

## 3) Sincronização entre servidores e com o cliente

### Entre servidores

- Estado compartilhado é persistido em Redis (`player:*`, `match:*`, fila, deadlines).
- Entrega de eventos para jogador conectado em outra instância ocorre via **Redis Pub/Sub** (`server:<server_id>`).
- `EventDispatcher` identifica o `connected_server` do jogador e faz envio local ou publica no canal correto.

### Com o cliente

- Cliente recebe eventos de domínio: `connected`, `room_joined`, `queue_update`, `match_found`, `game_state`, `opponent_disconnected`, `reconnected`, `game_over`, `error`.
- Heartbeat periódico mantém sessão viva e atualiza `last_seen`.

---

## 4) Apresentação de letras acertadas e erradas

### Como está tratado

- O backend mantém:
  - `correct_letters` (letras corretas globais da palavra da rodada)
  - `wrong_letters_by_player` (erros por jogador)
  - `errors_by_player` (contador de erros por jogador)
- O payload de `game_state` retorna letras corretas/erradas para renderização no cliente.
- Repetição de letra já usada é bloqueada com erro amigável.

---

## 5) Apresentação da forca e boneco (cliente)

### Como está tratado

- Backend fornece `errors`/`remaining_errors` no `game_state`.
- Cliente renderiza a forca em SVG e exibe partes do boneco conforme número de erros (1..6).
- Regra de desenho está alinhada com o limite `MAX_ERRORS=6`.

---

## 6) Reconexão em até 30 segundos; após isso adversário vence

### Como está tratado

- Ao desconectar jogador em partida ativa:
  - backend marca `disconnect_deadlines[player_id] = now + 30s`
  - grava deadline em ZSET (`reconnect:deadlines`)
  - notifica adversário com `opponent_disconnected`.
- Se jogador reconecta dentro do prazo:
  - deadline é removida e a partida continua.
- Se prazo expira:
  - rotina de timeout finaliza partida por `abandonment`, declarando vitória do adversário.

### Ponto importante de UX corrigido

- Login com **mesmo nickname** durante janela de reconexão agora tenta restaurar a sessão anterior (quando aplicável), em vez de criar novo jogador desconectado da partida.

---

## 7) UX para entrada/espera de par (fila/sala/avisos)

### Como está tratado

- Fluxo de lobby com salas e criação de sala.
- Para matchmaking por fila:
  - `broadcast_queue_updates` envia `queue_update` com posição para quem está aguardando.
- Para salas:
  - `room_joined` sinaliza entrada e estado de espera.
  - ao formar par, `match_found` + `game_state` iniciam rodada.

---

## 8) Verificação de ganhador com 6 erros

### Como está tratado

- Cada erro de letra aumenta `errors_by_player[player_id]`.
- Ao atingir `MAX_ERRORS` (default 6), jogador perde a rodada/partida conforme regra implementada.
- Chute de palavra incorreto encerra partida com derrota imediata (`wrong_word_guess`).
- Finalização envia `game_over` com vencedor, motivo e histórico de rodadas.

### Correspondência com partes do boneco

Os 6 erros representam:
1. cabeça
2. tronco
3. braço direito
4. braço esquerdo
5. perna direita
6. perna esquerda

No cliente, o SVG exibe progressivamente essas 6 partes.

---

## 9) Mapa rápido de serviços do backend

- `GameService`: regras de sessão, reconexão, jogadas e término de partida.
- `MatchmakingService`: fila, formação de pares, criação de partida.
- `LobbyService`: gerenciamento de salas.
- `EventDispatcher`: roteamento de eventos local/remoto.
- `ConnectionManager`: vínculo `player_id <-> websocket` no servidor atual.
- `TimeoutService`: expiração de deadline de reconexão.
- `RedisRepository`: persistência e locks distribuídos.

---

## 10) Checklist objetivo para apresentação (06/04/2026)

1. Subir dois backends + redis + nginx + observabilidade.
2. Mostrar 4+ abas conectando simultaneamente sem queda indevida.
3. Derrubar 1 aba durante partida e reconectar em <30s com mesmo nickname.
4. Repetir cenário e aguardar >30s para provar vitória por abandono.
5. Mostrar letras certas/erradas, progressão da forca e fim por 6 erros.
6. Mostrar dashboards (conexões, fila, partidas ativas/finalizadas, erros).

