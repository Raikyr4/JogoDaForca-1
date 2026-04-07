# Teste manual: reconexão automática e failover entre backends

Este guia valida o comportamento distribuído com dois backends (`game-server-1` e `game-server-2`) atrás do Nginx.

## 1) Pré-requisito

Com o Docker ativo:

```bash
docker compose down -v
docker compose up --build -d
```

## 2) URLs usadas no teste

- Aplicação (ponto único): `http://localhost`
- Backend 1 (direto): `http://localhost:8001/health`
- Backend 2 (direto): `http://localhost:8002/health`
- Health via balanceador Nginx: `http://localhost/health/backend`

### Opcional: forçar roteamento por jogador (teste cruzado entre containers)

Para fixar uma sessão no backend desejado, adicione `?server=...` na URL:

- Jogador A no backend 1: `http://localhost/?server=game-server-1`
- Jogador B no backend 2: `http://localhost/?server=game-server-2`

Esse parâmetro é aplicado ao WebSocket (`/ws`) e também às chamadas HTTP do frontend (`/api/...`).
Se o backend fixado cair durante a partida, o cliente tenta reconectar nele primeiro e, após falha, remove o fixo e volta para o balanceador automaticamente.

## 3) Cenário base (dois jogadores)

1. Abra duas sessões isoladas do navegador (anônima A e anônima B).
2. Em ambas, acesse `http://localhost`.
3. Entre com nicknames diferentes.
4. Entrem na mesma sala.
5. Confirme início da partida (`match_found` + `game_state`).

## 4) Simular queda de um backend

Pare uma instância (exemplo: servidor 2):

```bash
docker compose stop game-server-2
```

Valide:
- `http://localhost:8002/health` indisponível;
- `http://localhost:8001/health` disponível;
- `http://localhost/health/backend` disponível.

## 5) Resultado esperado da reconexão automática

1. O socket do jogador que caiu junto com a instância fecha.
2. O frontend entra em modo de reconexão.
3. O frontend tenta novamente conexão em `/ws` automaticamente.
4. O Nginx encaminha a nova conexão para a instância saudável.
5. O backend aceita `reconnect` com `player_id` e envia `reconnected`.
6. A partida continua se ocorrer dentro de 30 segundos.

## 6) Teste de expiração (abandonment)

1. Derrube o backend de uma sessão.
2. Não deixe o jogador reconectar por mais de 30 segundos.
3. O adversário deve vencer por `abandonment`.

## 7) Reativar instância derrubada

```bash
docker compose start game-server-2
```

Confira novamente:
- `http://localhost:8002/health`.

## 8) Logs úteis

Para acompanhar o teste:

```bash
docker compose logs -f nginx game-server-1 game-server-2
```
