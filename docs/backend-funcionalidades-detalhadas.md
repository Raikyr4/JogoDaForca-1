# Backend Python — documentação detalhada por funcionalidade

## Objetivo deste documento

Este documento descreve **como o backend em Python foi implementado** para atender os requisitos funcionais e não funcionais do projeto, com rastreabilidade direta para os módulos da aplicação.

Escopo principal:
- Camada de aplicação (`FastAPI` + `WebSocket`)
- Serviços de domínio (partida, lobby, matchmaking, timeout)
- Persistência e coordenação distribuída com Redis
- Monitoramento/observabilidade
- Pontos de integração com o cliente quando influenciam o contrato do backend

---

## 1) Visão geral da arquitetura de backend

### 1.1 Composição de serviços

No startup, o backend cria um container de dependências com:
- `RedisRepository` (persistência e primitivas distribuídas)
- `ConnectionManager` (mapeamento `player_id <-> websocket` local)
- `EventDispatcher` + `ServerChannelSubscriber` (roteamento local/remoto via Pub/Sub)
- `MatchmakingService` (fila e criação de partidas)
- `LobbyService` (salas)
- `GameService` (regras de jogo, turno, rodada, reconexão, encerramento)
- `TimeoutService` (expiração de reconexão)
- loops em background para Pub/Sub e atualização periódica de métricas. 

### 1.2 Interface exposta

- `GET /health`: status da instância.
- `GET /metrics`: endpoint Prometheus.
- `GET /lobby`: snapshot das salas e contadores.
- `POST /lobby/rooms`: criação de sala.
- `WS /ws`: canal em tempo real para protocolo de jogo.

### 1.3 Protocolo WebSocket (entrada)

Eventos processados no backend:
- `register_player` / `join_queue`
- `join_room`
- `reconnect`
- `guess_letter`
- `guess_word`
- `heartbeat`

A camada de handler valida payload mínimo, vínculo do socket com `player_id` e delega as regras ao `GameService`.

---

## 2) Preocupação com latência

### 2.1 Estratégia adotada

1. **Transporte em tempo real**: jogadas e sincronização de estado ocorrem via WebSocket, evitando polling para eventos críticos.
2. **Estado centralizado em Redis**: jogadores, partidas, fila, salas e deadlines ficam em memória distribuída de baixa latência.
3. **Locks curtos com TTL**:
   - Matchmaking: `lock:matchmaking` (3s).
   - Jogadas da partida: `lock:match:<match_id>` (5s) com espera curta (`retries=20`, `delay=50ms`).
   - Sala: `lock:room:<room_id>` (4s com retentativa).
4. **Fail-fast para contenção**: quando lock de partida não é adquirido, o backend retorna erro amigável (“Partida ocupada, tente novamente”), sem bloquear indefinidamente.
5. **Encaminhamento de eventos local/remoto otimizado**: envio direto local quando possível; Pub/Sub apenas quando o jogador está conectado em outra instância.

### 2.2 Impacto prático

- Reduz disputa simultânea de jogadas e evita corrida entre duas instâncias processando o mesmo match.
- Mantém tempo de resposta previsível mesmo com concorrência.
- Evita inconsistência de turno, placar e letras ao serializar atualizações por lock.

---

## 3) Monitoramento dos serviços

### 3.1 Métricas implementadas (Prometheus)

O módulo `Metrics` publica:
- `hangman_ws_active_connections` (Gauge)
- `hangman_waiting_players` (Gauge)
- `hangman_active_matches` (Gauge)
- `hangman_matches_finished_total` (Counter por `reason`)
- `hangman_queue_wait_seconds` (Histogram)
- `hangman_reconnections_total` (Counter)
- `hangman_disconnections_total` (Counter)
- `hangman_errors_total` (Counter por tipo)
- `hangman_backend_up` (Gauge)

### 3.2 Como os valores são atualizados

- Conexões WS: atualizadas no `ConnectionManager.bind_player/unbind_websocket`.
- Fila e partidas ativas: refresh periódico no background (`run_metrics_refresh_loop`), lendo snapshot do lobby e contagem de partidas ativas.
- Espera na fila: observada ao criar partida (`observe_queue_wait`) usando `queue_entered_at`.
- Reconexão/desconexão: incrementadas em `GameService.reconnect` e `GameService.disconnect`.
- Erros de protocolo/jogada: incrementados por tipo (`invalid_turn`, `repeated_letter`, `busy_match`, etc.) no `GameService`.

### 3.3 Logging operacional

O backend emite logs estruturados em eventos-chave:
- conexão/desconexão (`player_connected`, `player_disconnected`)
- matchmaking (`player_joined_queue`, `match_created`)
- jogo (`guess_processed`, `word_guess_processed`, `round_started`, `match_finished`)
- deadline de reconexão (`disconnect_deadline_set`).

---

## 4) Sincronização entre servidores e com o cliente

### 4.1 Sincronização entre servidores

A arquitetura assume múltiplas instâncias de backend atrás do balanceador. A consistência entre instâncias é tratada por dois mecanismos:

1. **Fonte de verdade no Redis**
   - Jogador: `player:<id>`
   - Partida: `match:<id>`
   - Fila: lista Redis
   - Salas: `lobby:room:<id>` + set de rooms
   - Deadlines: ZSET `reconnect:deadlines`

2. **Entrega de evento cross-server via Pub/Sub**
   - Cada servidor assina `server:<server_id>`.
   - `EventDispatcher` consulta `connected_server` do jogador.
   - Se destino é local: envia direto pelo `ConnectionManager`.
   - Se remoto: publica envelope `{player_id, payload}` no canal da instância alvo.

### 4.2 Sincronização com cliente

- O backend envia eventos de domínio (`connected`, `room_joined`, `queue_update`, `match_found`, `game_state`, `opponent_disconnected`, `reconnected`, `game_over`, `error`).
- A cada jogada válida, ambos os jogadores recebem novo `game_state` com dados derivados para renderização.
- O cliente envia `heartbeat` periódico; o servidor atualiza `last_seen`, heartbeat e renova claim do nickname.

### 4.3 Garantia de sessão por socket

No handler WS, após vínculo inicial, eventos subsequentes rejeitam `player_id` diferente daquele vinculado ao socket, evitando impersonação intra-conexão.

---

## 5) Apresentação de letras acertadas e erradas

### 5.1 Modelo de dados no match

No estado da partida:
- `correct_letters`: lista global de letras corretas da palavra da rodada.
- `wrong_letters_by_player`: mapa de erros por jogador.
- `errors_by_player`: contador numérico por jogador.

### 5.2 Regras de atualização

Ao receber `guess_letter`:
1. Normaliza para maiúsculo (`normalize_letter`).
2. Valida formato (1 caractere alfabético).
3. Rejeita letra repetida (já em `correct_letters` ou nos erros do jogador).
4. Se acerto: adiciona em `correct_letters`.
5. Se erro: adiciona em `wrong_letters_by_player[player_id]` e incrementa `errors_by_player[player_id]`.

### 5.3 Payload entregue ao cliente

`build_game_state_payload` retorna:
- `masked_word` (com `_` para letras não reveladas)
- `correct_letters`
- `wrong_letters` (do próprio jogador)
- `opponent_wrong_letters`
- `errors` / `remaining_errors`

Isso permite ao cliente exibir claramente letras certas e erradas sem reconstrução local ambígua.

---

## 6) Apresentação da forca e boneco (cliente) com contrato backend

### 6.1 O que o backend entrega

O backend não envia SVG/arte da forca; ele fornece os dados de estado:
- `errors` (erros do jogador)
- `remaining_errors`
- limite global configurável `max_errors` (padrão 6)

### 6.2 Como o cliente renderiza

No frontend (`HangmanGraphic`), o valor `errors` é limitado ao intervalo [0..6] e cada incremento ativa uma parte do boneco:
1. cabeça
2. tronco
3. braço direito
4. braço esquerdo
5. perna direita
6. perna esquerda

### 6.3 Coerência de regra

A coerência backend/cliente é garantida porque:
- backend encerra rodada ao atingir `max_errors`;
- frontend usa exatamente 6 estágios visuais.

---

## 7) Reconexão em até 30 segundos (senão adversário vence)

### 7.1 Fluxo na desconexão

Quando um socket cai:
1. `websocket_handler` executa `unbind_websocket`.
2. `GameService.disconnect` marca jogador como desconectado.
3. Se jogador estava em partida ativa:
   - define `disconnect_deadlines[player_id] = now + reconnect_timeout_seconds` (default 30);
   - grava token `<match_id>:<player_id>` no ZSET de deadlines;
   - notifica adversário com `opponent_disconnected`.

### 7.2 Fluxo de reconexão bem-sucedida

Com evento `reconnect` (ou restauração por login no mesmo nickname):
1. backend valida sessão;
2. re-vincula socket ao jogador;
3. marca conectado, atualiza `last_seen`, heartbeat e claim do nickname;
4. remove deadline de reconexão da partida e do ZSET;
5. notifica adversário (`reconnected`);
6. reenvia estado atual da partida.

### 7.3 Expiração do prazo

`TimeoutService` roda periodicamente (default 1s):
- lê tokens expirados no ZSET (`read_expired_deadlines(now)`);
- chama `resolve_expired_deadline(match_id, player_id)`;
- `GameService` finaliza a partida por `abandonment`, definindo vencedor como adversário.

### 7.4 Resultado funcional

- Reconectou em até 30s: partida continua.
- Não reconectou até o deadline: derrota automática por abandono.

---

## 8) UX para usuário entrando e usuário aguardando par

### 8.1 Entrada amigável no sistema

- Registro com nickname exclusivo (claim no Redis com TTL).
- Mensagens de erro claras para nickname em uso ou payload inválido.
- Restauração de sessão por nickname quando possível (evita frustração em reconexão).

### 8.2 Espera por adversário (fila)

No fluxo de matchmaking por fila:
- jogador entra com status `waiting` e `queue_entered_at`;
- backend envia `queue_update` para cada jogador aguardando, com `position` calculada dinamicamente.

### 8.3 Espera por adversário (salas)

No fluxo de lobby por salas:
- ao entrar sozinho na sala: `room_joined` com mensagem de espera;
- ao completar 2 jogadores: backend cria partida, associa `match_id` à sala e envia `match_found` + `game_state` inicial.

### 8.4 Informações de lobby para UX

`GET /lobby` retorna snapshot com:
- total de salas
- partidas ativas
- salas aguardando
- jogadores aguardando
- lista detalhada de salas e jogadores (incluindo `connected/status`).

Isso suporta avisos como “aguardando”, “em jogo”, ocupação e estado geral do sistema.

---

## 9) Verificação de ganhador e regra de 6 erros

### 9.1 Regra de erro máximo

A configuração `MAX_ERRORS` vem de ambiente e default é 6.

No `guess_letter`, quando erro incrementa `errors_by_player[player_id]`:
- se atingir/exceder `max_errors`, a rodada é concluída com vitória do oponente (`reason="max_errors"`).

### 9.2 Correspondência com partes da forca

Os 6 erros correspondem aos 6 elementos visuais do boneco:
1. cabeça
2. tronco
3. membro superior direito
4. membro superior esquerdo
5. membro inferior direito
6. membro inferior esquerdo

### 9.3 Outras condições de término

- `word_solved`: completou a palavra por letras.
- `full_word_hit`: acertou chute de palavra.
- `wrong_word_guess`: errou chute de palavra e perde a partida imediatamente.
- `abandonment`: não reconectou no prazo.
- `best_of_three` / `best_of_three_draw`: conclusão por placar após 3 rodadas.

### 9.4 Notificação final

No encerramento, ambos recebem:
- `game_state` final
- `game_over` com `winner`, `reason`, `is_draw`, placares e histórico de rodadas.

---

## 10) Tabela de rastreabilidade (requisito -> implementação)

| Requisito | Implementação principal |
|---|---|
| Latência | WebSocket + Redis em memória + locks com TTL + fail-fast em contenção |
| Monitoramento | `/metrics` + classe `Metrics` + refresh periódico + logs estruturados |
| Sincronização servidores | Redis como fonte de verdade + Pub/Sub por `server_id` |
| Sincronização com cliente | Eventos de domínio WS + `game_state` após mutações |
| Letras certas/erradas | `correct_letters`, `wrong_letters_by_player`, `errors_by_player`, payload dedicado |
| Forca/boneco | Backend envia `errors`; frontend mapeia 1..6 partes |
| Reconexão 30s | `disconnect_deadlines` + ZSET + `TimeoutService` + finalização por abandono |
| UX entrada/espera | Lobby snapshot, fila com posição, mensagens `room_joined/queue_update/match_found` |
| Ganhador com 6 erros | `max_errors` configurável (default 6) + derrota por `max_errors` |

---

## 11) Observações finais de engenharia

1. **Consistência distribuída** é alcançada por serialização com locks e estado compartilhado em Redis.
2. **Resiliência de sessão** combina heartbeat, claim de nickname com TTL e janela de reconexão.
3. **Escalabilidade horizontal** é suportada por desacoplamento entre conexão local e estado global + Pub/Sub entre instâncias.
4. **Operabilidade** é reforçada por métricas de negócio/técnicas e logs estruturados para diagnóstico.

