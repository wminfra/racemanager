# RACING MANAGER — CONTEXTO, REGRAS E DIRETRIZES DE ARQUITETURA

> Documento consolidado com a visão de negócio, arquitetura técnica, regras operacionais e diretrizes obrigatórias para agentes e desenvolvedores que atuam no repositório **Racing Manager**.

---

## 1. Visão Geral do Projeto

O **Racing Manager** é um sistema local concebido para controlar e operar estações comerciais de simuladores de corrida físicas, integrando o jogo **Assetto Corsa Competizione (ACC)** via Steam, monitores ou headsets de Realidade Virtual (PCVR), e telas de interação com cliente e operador.

### Princípios Norteadores
- **Não Invasivo com a Steam**: O sistema nunca solicita nem manipula senhas, tokens ou credenciais da Steam. Não altera o cliente da Steam nem automatiza login. A Steam deve ser autenticada manualmente pelo proprietário.
- **Autoridade Estrita do Servidor**: O backend é o único relógio oficial e autoridade de estado da sessão. Os clientes web (Kiosk, Operator) e o Overlay nativo apenas refletem o estado calculado e emitido pelo servidor.
- **Segurança Operacional e Mecânica**: Equipamentos de alta potência (ODESC direct drive, atuadores) e o cliente no simulador têm prioridade absoluta. Processos nunca são encerrados às cegas; o simulador nunca é liberado com o jogo ainda rodando; comandos mecânicos ou de torque não são enviados sem validação física prévia.
- **Isolamento de Rede Local**: O sistema opera estritamente na rede local privada (porta 8000). Não possui integração de pagamentos ou valores financeiros integrados e não deve ser exposto à internet pública.

---

## 2. Topologia do Sistema e Componentes

```
                              +----------------------------+
                              |   Operator (Tablet/PC)     |
                              |   /operator (Autenticado)  |
                              +-------------+--------------+
                                            | HTTP REST / WebSocket
                                            v
+-----------------------+     +----------------------------+     +------------------------+
|    Kiosk (Edge)       | --> |    Racing Manager Backend  | <-- |     Admin (PC)         |
| /kiosk (Tela cheia)   |     |    FastAPI (Porta 8000)    |     | /admin (Autenticado)   |
+-----------------------+     +--------------+-------------+     +------------------------+
                                             |
                   +-------------------------+-------------------------+
                   |                         |                         |
                   v                         v                         v
+------------------------------+ +-----------------------+ +------------------------------+
| Native Overlay (PySide6)     | | Steam & ACC Process   | | VR Diagnostics (OpenVR/Meta) |
| pythonw.exe (WebSocket Kiosk)| | AC2-Win64-Shipping    | | Leitura somente-leitura      |
+------------------------------+ +-----------------------+ +------------------------------+
```

### Componentes Principais:
1. **Backend FastAPI (`app/`)**:
   - Ponto central da lógica, regras de sessão, monitoramento de processos e persistência SQLite (`data/racing_manager.db`).
   - Executa sob Uvicorn em porta 8000, protegido por cookie de sessão assinado (`itsdangerous` / Starlette `SessionMiddleware`).
2. **Kiosk Web (`frontend/kiosk/`)**:
   - Tela pública no simulador exibida via Microsoft Edge em modo Kiosk tela cheia (`--kiosk`).
   - Apresenta:
     - Tela de espera ("A próxima volta começa aqui").
     - Tela de orientações antes da corrida (15 segundos configuráveis, com lista de instruções e contagem).
     - Tela de jogo (completamente limpa durante a corrida).
     - Tela final ("Sessão encerrada") e tela de falha amigável ("Não foi possível iniciar o jogo").
3. **Operator Web (`frontend/operator/`)**:
   - Painel web responsivo para o operador (no PC ou tablet/celular na rede local).
   - Funções: monitorar prontidão da Steam/ACC/Simulador, selecionar jogo/modo de lançamento/duração, iniciar sessão, cancelar durante orientações, encerrar normalmente, interromper sessão por motivo operacional com justificativa, testar diagnósticos VR, e gerenciar tentativas de fechamento e force-close em caso de falha.
4. **Admin Web (`frontend/admin/`)**:
   - Painel restrito a administradores.
   - Funções: resumo geral de utilização, configuração de durações permitidas (5 a 240 min), cadastro de novos operadores, visualização dos logs de auditoria (`audit_logs`) e consulta/exportação de Histórico Operacional em CSV (com BOM UTF-8).
5. **Overlay Nativo de Cronômetro (`app/modules/overlay/`)**:
   - Aplicação desktop PySide6 (Qt) independente iniciada via `pythonw.exe`.
   - Conecta ao WebSocket público do Kiosk (`ws://127.0.0.1:8000/api/ws/kiosk`).
   - Totalmente passivo: não recebe foco, não intercepta cliques (`Qt.WindowTransparentForInput`), sempre no topo (`Qt.WindowStaysOnTopHint`), sem bordas.
   - Visível apenas quando o processo validado do ACC está em andamento.
   - Comportamento visual:
     - > 2 min: verde discreto no canto superior.
     - <= 2 min: destaque visual sutil.
     - <= 30 s: pisca em vermelho (500 ms).
     - <= 10 s: pisca acelerado (180 ms).
     - Término: exibe `00:00` brevemente e some.
   - **Nota de compatibilidade**: Requer que o ACC seja configurado em modo **Windowed / Borderless** (sem bordas), pois exclusive fullscreen do DirectX pode suprimir overlays da sessão gráfica do Windows.

---

## 3. Regras de Negócio e Ciclo de Vida da Sessão

### 3.1. Máquina de Estados da Sessão (`RacingSession.status`)
As sessões transitam estritamente entre os seguintes estados:
- `waiting`: Criada pelo operador. Divide-se internamente em duas fases:
  1. `guidance`: Exibição das orientações no Kiosk (padrão: 15s). O jogo **não** é solicitado à Steam nesta fase. Pode ser cancelada pelo operador sem impactos.
  2. `launching`: O backend envia a ordem de início à Steam e aguarda a detecção real do executável do ACC (timeout padrão: 120s).
- `running`: O processo real `AC2-Win64-Shipping.exe` foi detectado no sistema operacional. O cronômetro regressivo é ativado.
- `completed`: A sessão completou o tempo contratado normalmente e o jogo foi confirmado como encerrado.
- `cancelled`: A sessão foi cancelada pelo operador durante o estado `waiting` (antes da entrada em pista). Duração efetiva: 0s.
- `interrupted`: A sessão em andamento foi interrompida manualmente pelo operador ou finalizada por saída prematura do processo do jogo.
- `technical_failure`: Ocorreu falha irrecuperável: timeout de lançamento, inicialização falha, jogo fechou com erro não tratado, ou o processo do ACC não respondeu e não pôde ser fechado normalmente.

### 3.2. Unicidade e Concorrência
- **Regra de 1 Sessão Ativa**: Cada simulador só pode ter no máximo uma sessão em `waiting` ou `running` simultaneamente. Essa regra é aplicada tanto por trava de software (`app.state.session_transition_lock = threading.RLock()`) quanto por restrição de índice único parcial no banco de dados (`uq_one_running_session_per_simulator`).

### 3.3. Contabilização da Duração Efetiva
- A contagem de `effective_seconds` inicia **exclusivamente** em `started_at` (quando o processo do ACC é detectado pela primeira vez).
- O tempo gasto em `guidance` (orientações) e `launching` (carregamento pela Steam/jogo) **nunca** é descontado do tempo contratado pelo cliente.
- Caso ocorra interrupção ou falha antes do início do jogo (`started_at is None`), a duração efetiva é zero.

### 3.4. Interrupção Operacional Manual
- O operador pode interromper uma sessão em andamento pelo botão vermelho **INTERROMPER SESSÃO**.
- **Motivo Obrigatório**: Deve pertencer à lista predefinida:
  - `Problema no volante`, `Problema nos pedais`, `Problema no câmbio`, `Problema no computador`, `Problema no jogo`, `Solicitação do cliente`, `Necessidade operacional`, `Outro`.
- **Regra do campo Observação**:
  - Se motivo for `Outro`: o texto de observação é **obrigatório**.
  - Se motivo for qualquer outro: o campo observação **não pode** ser preenchido.
- A duração efetiva é calculada com base no instante exato da solicitação de interrupção (`interrupted_at - started_at`). O tempo não utilizado (`unused_seconds`) é registrado para fins de auditoria e atendimento ao cliente.
- O jogo é imediatamente solicitado a fechar; o simulador só é liberado para nova sessão quando a ausência do processo for confirmada.

### 3.5. Recuperação Pós-Reinício (`recover_open_sessions`)
- Se o backend ou o computador reiniciar abruptamente enquanto existia uma sessão em `waiting` ou `running`:
  - O sistema **nunca retoma** uma sessão aberta.
  - Verifica se há processos do jogo rodando e tenta encerrá-los de forma graciosa.
  - Marca a sessão no banco como `interrupted` (se estava rodando) ou `technical_failure` (se estava iniciando), registrando o motivo no log de auditoria.

---

## 4. Integração Steam e Política de Encerramento Seguro (Shutdown)

### 4.1. Detecção do Jogo e da Steam
- A Steam é detectada pelo Registro do Windows (`HKCU\Software\Valve\Steam`), variáveis de ambiente (`ProgramFiles`) ou caminho configurado em `RACING_STEAM_PATH`.
- O Assetto Corsa Competizione (AppID `805550`) é localizado dinamicamente lendo os manifestos da Steam (`appmanifest_805550.acf`) em todas as bibliotecas listadas em `libraryfolders.vdf`. Não há caminhos fixos obrigatórios de instalação no código.
- A validação de execução busca o executável exato `AC2-Win64-Shipping.exe`. Caso o PID detectado tenha nome correto mas caminho de executável inacessível ou inconsistente, o sistema bloqueia ações forçadas para evitar encerrar processos errôneos.

### 4.2. Fluxo Gradual de Encerramento (`GameShutdownService`)
O encerramento é centralizado e idempotente:
1. **Solicitação Graciosa (`WM_CLOSE`)**:
   - Envia uma mensagem `PostMessageW(hwnd, 0x0010, 0, 0)` exclusivamente às janelas visíveis de topo pertencentes ao PID validado do ACC. Equivale a clicar no "X" da janela ou solicitar encerramento limpo pelo jogo.
   - **Primeira Espera**: Aguarda `RACING_GAME_CLOSE_FIRST_WAIT_SECONDS` (padrão: 15s).
   - **Espera Adicional**: Caso continue rodando, aguarda `RACING_GAME_CLOSE_ADDITIONAL_WAIT_SECONDS` (padrão: até 30s adicionais).
   - O comando `WM_CLOSE` **não é reenviado em loop** durante as esperas.
2. **Falha no Fechamento Gracioso (`CLOSE_FAILED`)**:
   - Se após 45 segundos o processo continuar ativo, o serviço entra em estado `CLOSE_FAILED`.
   - A sessão não é finalizada e o simulador permanece **bloqueado e indisponível**. O timer do backend não dispara novos pedidos automáticos.
3. **Ações Disponíveis ao Operador em `CLOSE_FAILED`**:
   - **Tentar fechamento normal novamente**: Envia uma nova mensagem `WM_CLOSE` isolada e reinicia os períodos de espera.
   - **Forçar encerramento**: Requer confirmação explícita em caixa de diálogo com aviso de risco de perda de telemetria/dados. Somente após a confirmação, o serviço executa `terminate()` (aguarda 3s) e, se necessário, `kill()`, revalidando rigorosamente o caminho do executável do PID antes de disparar o sinal.
4. **Isolamento de Processos**: A Steam, o SteamVR, o Meta Link e outros softwares do Windows **nunca** recebem sinais de encerramento pelo Racing Manager.

### 4.3. Detecção de Travamento (`is_game_unresponsive`)
- No Windows, o backend avalia via `user32.IsHungAppWindow(hwnd)`.
- Se o jogo parar de responder por tempo superior à carência (`unresponsive_grace_seconds = 60s`), o supervisor sinaliza `game_unresponsive`, tenta fechar graciosamente e notifica o painel.

---

## 5. Realidade Virtual (VR) e Diagnósticos

### 5.1. Modos de Lançamento
- `monitor`: Inicia com argumento `-nohmd`.
- `oculus`: Inicia com argumentos `-vr hmd=OculusHMD`.
- `steamvr`: Inicia com argumentos `-vr hmd=SteamVR`.

### 5.2. Diagnóstico VR Somente-Leitura (`VRDiagnostics`)
- O botão **TESTAR VR** no painel do operador executa uma sondagem passiva:
  - **Não** inicia a Steam, o SteamVR ou o ACC.
  - **Não** altera o Registro do Windows, configurações globais do SteamVR nem arquivos de configuração do OpenXR.
  - No modo **SteamVR**: Consulta `openvr` apenas se `vrserver.exe` já estiver em execução. Utiliza um arquivo temporário de registro (`openvrpaths.vrpath`) em diretório temporário para isolar logs e leituras.
  - No modo **Oculus**: Consulta a biblioteca `LibOVRRT64_1.dll` via ctypes abrindo e destruindo uma sessão efêmera de SDK para inspecionar `HmdPresent`, `HmdMounted` e pose de rastreamento.
- **Regra de Honestidade de Estado**: O sistema reporta `DESCONHECIDO` sempre que uma condição não puder ser atestada com certeza. Processos abertos por si só não provam que o headset está conectado ou funcional.
- **Bloqueio de Início**: Se o operador tentar iniciar uma sessão em modo VR (`oculus` ou `steamvr`), o backend consulta previamente o diagnóstico; se o headset não estiver com tracking e conexão comprovados (`connected`), a inicialização é recusada com erro HTTP 409.

---

## 6. Hardware e Integrações Futuras

Conforme detalhado em `docs/HARDWARE.md`:
- **Dispositivos do Cockpit**:
  - Volante artesanal: Placa controladora **ODESC 4.2** com motor de hoverboard direct-drive.
  - Pedais e Paddle Shifters: **Arduino Leonardo** operando como dispositivo USB HID.
  - Controles de Volante / Navegação: **Arduino Pro Micro / Leonardo** operando como Joystick USB HID.
  - Headset VR: **Meta Quest 3S** via cabo USB Link / PCVR.
- **Diretriz de Segurança de Hardware**:
  - O código **não se comunica diretamente** com a ODESC e não envia comandos de torque, calibração ou movimentação mecânica.

### 6.1. Navigation Button Manager (`app/modules/navigation/`)
- **Propósito**: Mapeia botões físicos de joystick USB (Arduino Pro Micro) para teclas virtuais do Windows (`UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER`, `ESC`, `SPACE`), permitindo ao cliente navegar pelos menus do jogo sem acesso a teclado físico.
- **Arquitetura em Camadas**:
  `DEVICE MANAGER` (`winmm.dll`) → `INPUT LISTENER` (Thread ~50Hz) → `BUTTON DETECTION` (Captura física) → `INPUT MAPPING` (Validação/Unicidade) → `ACTION DISPATCHER` (Debounce/Repetição) → `KEYBOARD OUTPUT` (`SendInput` via Win32).
- **Leitura Não Exclusiva**: A leitura via WinMM (`joyGetPosEx`) é compartilhada; não bloqueia o dispositivo e não interfere no funcionamento normal do joystick e demais periféricos no simulador.
- **Botões Dedicados e Sem Alternância de Modo**: Os botões associados são reservados à navegação sem alternância entre modo menu ou corrida. Botões não mapeados continuam operando nativamente no jogo.
- **Controle de Repetição e Debounce**:
  - Debounce configurável (padrão 200 ms).
  - Repetição contínua permitida apenas para setas direcionais (`UP`, `DOWN`, `LEFT`, `RIGHT`) após atraso (`repeat_delay_ms`, padrão 400 ms) a cada intervalo (`repeat_interval_ms`, padrão 150 ms).
  - Bloqueio estrito de repetição para `ENTER`, `ESC` e `SPACE` enquanto mantidos pressionados, evitando confirmações/cancelamentos múltiplos involuntários.
- **Tolerância a Falhas**: Detecção imediata de desconexão e recuperação transparente após reconexão sem reinício do servidor.
- **Persistência e Migrações**: Salvo na tabela `app_settings` (`key="navigation_controls_config"`) com migração idempotente `init_navigation_config`. Auditado via `AuditLog`.

---

## 7. Scripts Operacionais e Ambiente Windows

Localizados em `scripts/`:
- `Start-RacingManager.ps1 [-ShortSessionTestMode]`:
  - Orquestrador principal. Inicia o backend (`Start-Backend.ps1`), o overlay nativo (`Start-Overlay.ps1`) e o navegador em modo quiosque (`Start-Kiosk.ps1`).
  - O parâmetro `-ShortSessionTestMode` habilita temporariamente opções de sessão de 1 e 2 minutos (útil para homologação rápida).
- `Stop-TestEnvironment.ps1`:
  - Encerra com segurança o ambiente.
  - **Trava de Segurança**: Recusa a execução caso exista uma sessão em andamento (`waiting` ou `running`). A sessão deve ser encerrada primeiro pelo operador.
  - Encerra de forma cirúrgica apenas os PIDs registrados em `.runtime/` (`backend.json`, `overlay.json`, `kiosk.json`), validando nome de processo, caminho do executável e linha de comando para evitar fechar instâncias não relacionadas do Edge ou Python.
- `create_admin.py`:
  - Cria o primeiro usuário administrador no banco via linha de comando (exige senha forte de no mínimo 12 caracteres).

---

## 8. Segurança, Autenticação e Banco de Dados

### 8.1. Autenticação e Autorização
- **Hash de Senha**: `pwdlib[argon2]`.
- **Sessão**: Gerenciada por cookies assinados (`racing_manager_session`), duração de 8 horas, `HttpOnly`, `SameSite=Lax`. Em ambiente HTTP local, `Secure=false`; em produção HTTPS, `RACING_COOKIE_SECURE=true`.
- **Proteção contra Brute-Force**: Limite de 5 tentativas de login por IP/host a cada 5 minutos.
- **RBAC**: Dois papéis:
  - `operator`: Pode operar sessões, visualizar status e histórico recente. Não acessa rotas `/api/admin/*`.
  - `admin`: Controle total, configuração de durações, criação de operadores e acesso ao histórico geral com exportação CSV.

### 8.2. Banco de Dados e Migrações
- SQLite em `data/racing_manager.db`.
- O sistema utiliza migrações aditivas e idempotentes (`app/database/migrations.py`):
  - `migrate_session_operational_columns`: Adiciona colunas nullable via `ALTER TABLE` sem recriar tabelas ou destruir o histórico existente.
  - `migrate_legacy_sessions`: Migra dados de versões legadas de forma idempotente.

---

## 9. Diretrizes Obrigatórias para Agentes e Desenvolvedores

Ao realizar manutenções, melhorias ou alterações no código deste projeto:

1. **Nunca force o encerramento do jogo sem confirmação explícita**:
   - Mantenha a política de duas etapas de espera do `WM_CLOSE` e transição para `CLOSE_FAILED`.
   - Não reintroduza `taskkill /F` indiscriminado ou `process.kill()` automático em timers.
2. **Preserve a autoridade de tempo no backend**:
   - Telas web e overlays nunca determinam se a sessão acabou. Eles apenas escutam os eventos ou cronômetros vindos do servidor.
3. **Mantenha o diagnóstico VR passivo e desacoplado**:
   - A checagem de VR nunca deve disparar executáveis do SteamVR ou do jogo por debaixo dos panos. Se o estado for inconclusivo, retorne `unknown`.
4. **Proteja as rotinas de shutdown e hardware**:
   - Não adicione chamadas para envio de pacotes a portas seriais ou controladores de motores sem isolamento em módulos específicos e aprovação expressa de segurança.
5. **Atenção aos Testes com Pytest no Windows**:
   - A suíte padrão roda com mocks e simuladores (`FakeSteam`).
   - Ao executar pytest no Windows com múltiplas unidades (ex: projeto no `D:` e temporários no `C:`), use sempre `--basetemp` apontando para uma pasta no mesmo drive ou pasta temporária absoluta válida (ex: `--basetemp="C:\Users\<user>\AppData\Local\Temp\pytest_rm"`).
   - O teste real com jogo instalado (`tests/real/test_acc_live.py`) só deve ser executado com autorização explícita e com a Steam previamente logada.
