# Racing Manager — operação e integração ACC

## Requisitos

- Windows 10/11 x64, Python 3.12 e Microsoft Edge.
- Cliente Steam instalado e iniciado/autenticado manualmente pelo proprietário.
- Assetto Corsa Competizione instalado pela Steam (AppID 805550).
- Rede local privada para o celular/tablet do operador. Não exponha a porta 8000 à internet.

## Instalar e preparar

No PowerShell, na pasta do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Defina no `.env` uma chave `RACING_SECRET_KEY` nova, gerada por:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Não compartilhe nem versione `.env`. Crie um administrador com `python -m scripts.create_admin` e use esse painel para criar o operador.

## Steam e detecção do ACC

1. Inicie a Steam manualmente e autentique a conta desejada. O Racing Manager não solicita senha nem Steam Guard.
2. Instale o Assetto Corsa Competizione pela Steam e aguarde a instalação terminar.
3. A detecção lê o manifesto `appmanifest_805550.acf`, as bibliotecas em `libraryfolders.vdf` e procura `AC2-Win64-Shipping.exe` dentro da pasta registrada pelo manifesto. Não há caminho fixo de jogo.
4. Se a biblioteca não for descoberta automaticamente, configure em `.env` `RACING_STEAM_LIBRARY_PATH` com a raiz da biblioteca, isto é, a pasta que contém `steamapps`. `RACING_STEAM_PATH` pode apontar para `steam.exe` ou para a pasta do cliente. `RACING_ASSETTO_CORSA_PATH`, opcionalmente, pode apontar para a pasta de instalação do ACC.
5. O diagnóstico verifica Steam instalada/em execução, conta Steam selecionada, manifesto, executável e acesso de leitura. O lançamento usa o protocolo oficial `steam://rungameid/805550`; não inicia Steam nem automatiza login.

A seleção local da conta é inferida do `loginusers.vdf`; isso não revela nem lê senha e não prova que a Steam aceitará o entitlement. A Steam valida autenticação e licença quando o jogo abre.

## Iniciar e acessar

Inicie Steam primeiro, depois execute em PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Start-RacingManager.ps1
```

O servidor inicia oculto na porta 8000 e o Edge abre `/kiosk` em tela cheia. Os scripts separados são `Start-Backend.ps1`, `Start-Kiosk.ps1` e `Stop-TestEnvironment.ps1`. Eles não alteram Registro, Explorer, políticas, contas ou inicialização do Windows.

- Kiosk: `http://localhost:8000/kiosk`.
- Operator no computador: `http://localhost:8000/operator`.
- Operator no celular/tablet: `http://<IP-local-do-computador>:8000/operator`.
- Admin: `http://localhost:8000/admin`.
- OpenAPI: `http://localhost:8000/docs`.

Para descobrir o IP, execute `ipconfig`. Permita a porta 8000 somente na rede privada pelo Firewall; não crie encaminhamento no roteador. Use sempre o mesmo endereço durante uma sessão de login: `localhost` e o IP são hosts distintos e o navegador armazena cookies separados para cada um.

## Login e resolução de problemas

O login retorna um cookie assinado pelo backend. No modo HTTP local, os atributos são `HttpOnly`, `SameSite=Lax`, `Path=/` e `Secure` desativado; a sessão dura 8 horas. Em HTTPS, configure `RACING_COOKIE_SECURE=true`. O frontend envia credenciais same-origin, verifica `/api/auth/me` antes de redirecionar e mostra erros de autenticação/API no formulário.

Se a página voltar ao login:

1. Confirme que Operator e API estão no mesmo host/porta (não abra a página por IP e depois faça requisições por `localhost`).
2. No DevTools → Network, confira `POST /api/auth/login` (200 ou 401), o `Set-Cookie` e a resposta de `GET /api/auth/me`. Não copie nem compartilhe o valor do cookie.
3. Em DevTools → Application/Storage → Cookies, confira `racing_manager_session`, caminho `/`, `HttpOnly`, `SameSite=Lax` e a ausência de `Secure` quando acessado por HTTP.
4. Se o login for 200 mas `/auth/me` retornar 401, confirme que o navegador permaneceu no mesmo hostname e que não há outro backend com uma `RACING_SECRET_KEY` diferente respondendo na porta 8000.
5. Credenciais incorretas retornam “Usuário ou senha inválidos”. Contas Operator são criadas no Admin; não existe usuário padrão.
6. Consulte `.runtime/backend-error.log` e `.runtime/backend.log` para erros de inicialização/serviço. Não publique esses logs sem revisá-los.

Sair pelo botão encerra a sessão e limpa o cookie. Rotas administrativas continuam protegidas no backend; um Operator recebe 403 mesmo chamando diretamente a API.

## Sessão real com ACC

1. Inicie Steam manualmente e confirme no painel Operator que Steam/ACC aparecem disponíveis.
2. Execute `Start-RacingManager.ps1 -ShortSessionTestMode` para habilitar opções transitórias de 1 e 2 minutos. O modo vale somente para o processo iniciado; ele não grava essas durações nos pacotes/configurações salvas. Para operação normal, inicie sem essa opção.
3. No Operator, selecione 2 minutos e pressione **Iniciar Assetto Corsa Competizione**. O backend cria `Aguardando`, abre `steam://rungameid/805550`, e muda para `Em andamento` quando detecta o processo exato da instalação.
4. Se a Steam mostrar uma janela de seleção de opção de inicialização, selecione a opção normal/de desktop do ACC. O backend espera o processo até o limite configurado; enquanto a Steam aguarda uma escolha, o estado permanece `Aguardando`.
5. O Kiosk e o Operator recebem o cronômetro calculado pelo servidor. No fim, o backend manda `WM_CLOSE`, aguarda o período de tolerância e só então pode encerrar o PID validado pelo caminho exato do `AC2-Win64-Shipping.exe`. Não encerra a Steam.
6. Confirme o estado `Concluída`, processo do jogo ausente e Kiosk de volta à tela de espera. Falhas de início, saída inesperada, travamento e encerramento aparecem no histórico e log.

Para executar o teste automatizado **real** em banco temporário, separado dos testes simulados, com a Steam aberta e ACC instalado, use uma janela PowerShell de manutenção:

```powershell
$env:RACING_RUN_REAL_ACC_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests/real/test_acc_live.py -m real_integration -q
Remove-Item Env:RACING_RUN_REAL_ACC_TESTS
```

Esse teste abre o ACC real, roda uma sessão de 2 minutos e solicita o encerramento; não rode enquanto outro ACC estiver aberto. Se a Steam pedir uma opção de inicialização, selecione a opção normal/de desktop durante o teste. A suíte normal `pytest` não inicia o jogo e não exige instalação. O teste real verifica backend/Kiosk API e processo; a apresentação física do Edge e o uso do tablet ainda exigem validação visual no equipamento.

O backend pode ser encerrado com `Stop-TestEnvironment.ps1` somente sem sessão ativa. Para sair do Edge durante manutenção, use `Alt+F4`. Edge Kiosk oculta o navegador, mas não desativa barra de tarefas ou shell do Windows. Nenhum script inicia o jogo ao ligar o Windows.

## Hardware

ODESC 4.2, Arduino Leonardo, volante artesanal, pedais, botões e Meta Quest 3S ainda não são acessados. Veja [HARDWARE.md](HARDWARE.md). Nenhum comando de torque/movimento é enviado.


## Orientacoes antes da corrida e Kiosk

Ao criar uma sessao, o Kiosk mostra as orientacoes fixas por 15 segundos. O
backend e a autoridade do contador; o jogo nao e solicitado antes do fim desse
periodo. Para ajustar a duracao em uma implantacao, configure
`RACING_GUIDANCE_DURATION_SECONDS` no `.env` e reinicie o backend. As mensagens
atuais ficam em `app/services/sessions.py`; a estrutura permite substitui-las
por configuracao administrativa numa etapa futura.

Durante o jogo, a tela Kiosk fica sem texto e sem avisos. Quando a sessao
termina, ela mostra a tela de encerramento por alguns segundos. Falhas tecnicas
mostram uma mensagem generica ao cliente; o detalhe fica no Operator/Admin e no
log. Cancelar durante as orientacoes impede a chamada da Steam.

## Overlay nativo do cronometro

A instalacao `pip install -e ".[dev]"` inclui PySide6. O script
`Start-RacingManager.ps1` inicia o overlay independente antes do Edge e evita
duplicar a instancia registrada. Para inicia-lo manualmente:

```powershell
.\.venv\Scripts\pythonw.exe -m app.modules.overlay
```

Ele consome somente o WebSocket publico e somente de leitura do Kiosk em
`127.0.0.1`; o backend continua calculando o tempo. O overlay nao tem botoes,
nao recebe foco e nao aceita entrada. Ele permanece oculto sem sessao ativa.
Acima de 2 minutos usa apresentacao normal; aos 2 minutos destaca discretamente;
aos 30 segundos pisca em vermelho e acelera aos 10 segundos. Ao concluir,
apresenta `00:00` brevemente. O `Stop-TestEnvironment.ps1` encerra apenas o
`pythonw.exe` cujo PID, executavel e linha de comando correspondam ao overlay
registrado por este projeto.

Para testar sobre o ACC real, inicie o ambiente pelo script geral, crie uma
sessao no Operator e aguarde as orientacoes e a deteccao do jogo. O overlay so
aparece depois que o backend detecta o processo validado do ACC. O teste visual
precisa ser feito no computador do simulador; a suite automatizada nao abre o
jogo nem valida composicao grafica real.

A janela usa flags Qt de topo, sem foco e transparente para entrada. Isso
permite desenhar sobre aplicativos em modo janela/borderless. Alguns drivers e
a configuracao de tela cheia exclusiva do jogo podem impedir composicao de
janelas externas; se isso ocorrer, configure o ACC para **Windowed/Borderless**
(sem borda) nas opcoes de video e reinicie o jogo. A tela cheia exclusiva nao e
considerada validada neste projeto; teste os dois modos no equipamento antes da
operacao comercial. O overlay tambem depende da sessao grafica interativa do
Windows e nao e um servico de sistema.

## Historico operacional no Admin

No painel Admin, a secao **Historico operacional** lista solicitacao,
orientacoes, pedido de abertura, inicio (primeira deteccao do processo), pedido
de encerramento e encerramento confirmado. A duracao efetiva comeca em
`started_at`, quando o processo real do ACC e detectado; o tempo de orientacao
e de inicializacao nao e contabilizado. Falhas antes dessa deteccao tem zero
segundos efetivos. O fechamento por tempo usa o prazo oficial do backend; o
horario de encerramento registrado e quando o fechamento foi confirmado.

Use filtros de data, operador, jogo e status. Os indicadores sao calculados
sobre o resultado filtrado. **Exportar CSV** aplica os mesmos filtros, inclui
BOM UTF-8 para Excel e nao contem valores financeiros. A API e exclusiva de
administradores: `GET /api/admin/history` e `GET /api/admin/history.csv`.

A primeira inicializacao aplica uma migracao aditiva idempotente que acrescenta
`guidance_started_at`, `launch_requested_at` e `end_requested_at` como colunas
nulas. Ela nao recria tabelas nem remove registros existentes. Registros
anteriores permanecem no historico; dados de operador/jogo/simulador ausentes
sao identificados como removidos ou desconhecidos.


## Interrupcao manual de sessao

Quando o ACC estiver em andamento, o Operator apresenta o botao vermelho **INTERROMPER SESSAO**, separado do encerramento normal. A confirmacao exige um motivo; ao selecionar **Outro**, a observacao tambem e obrigatoria. Fechar/cancelar o dialogo nao altera a sessao.

O endpoint autenticado aceita somente usuarios `operator` e `admin`. A API serializa a transicao com o supervisor e com o encerramento normal. A hora efetiva e a hora da solicitacao de interrupcao; o horario de encerramento registra a confirmacao de que o processo ACC sumiu. O tempo nao utilizado e a duracao contratada menos a duracao efetiva. O periodo de orientacoes nao entra no calculo. Uma repeticao apos sucesso e segura e nao solicita outro fechamento.

O fechamento usa a identificacao existente do executavel ACC e primeiro solicita fechamento normal; depois a integracao espera o intervalo e, se necessario, encerra somente processos que validou pelo nome e caminho da instalacao do ACC. A Steam permanece aberta. O intervalo padrao e 8 segundos e pode ser alterado em `.env` com `RACING_INTERRUPTION_CLOSE_GRACE_SECONDS` (0 a 60 segundos). Se ainda houver processo ou nao for possivel verificar o encerramento, o historico registra falha tecnica e o Operator recebe uma mensagem. Nesse caso, o equipamento permanece indisponivel enquanto o processo ACC continuar presente; feche-o e atualize o Operator.

Para validar com o ACC real, inicie a Steam manualmente, execute `Start-RacingManager.ps1`, inicie uma sessao pelo Operator e aguarde o jogo entrar em andamento. Clique em **INTERROMPER SESSAO**, escolha o motivo e confirme. Confira no Gerenciador de Tarefas que `AC2-Win64-Shipping.exe` encerrou e que a Steam continua aberta; no Operator o equipamento deve voltar a disponivel, o Kiosk deve voltar a espera e o overlay deve desaparecer. No Admin, confira operador, motivo, observacao, hora, duracao efetiva e tempo nao utilizado; exporte o CSV com o filtro de motivo. Esses passos sao um teste manual; os testes automatizados usam apenas um adaptador simulado e nao validam o ACC instalado.

Na proxima inicializacao, a migracao aditiva acrescenta horario da interrupcao, operador responsavel, motivo e observacao como campos opcionais, preservando as linhas atuais do SQLite. Registros antigos continuam consultaveis; os campos novos ficam vazios nos registros anteriores.

## Diagnóstico VR

No Operator, selecione VR Oculus ou VR SteamVR e pressione TESTAR VR. Essa sondagem é somente leitura: não cria sessão, não inicia SteamVR ou ACC e não encerra aplicações. UNKNOWN indica que o estado não pôde ser comprovado. O campo headset colocado permanece desconhecido porque esta versão não abre uma sessão no runtime Meta. Consulte [HARDWARE.md](HARDWARE.md) para limitações e procedimento de teste no Quest 3S.


## Diagnostico VR

No Operator, selecione VR Oculus ou VR SteamVR e pressione TESTAR VR. A sondagem e somente leitura: nao cria sessao, nao inicia SteamVR ou ACC e nao encerra aplicacoes. `DESCONHECIDO` indica que o estado nao pode ser comprovado. O campo headset colocado permanece desconhecido porque esta versao nao abre sessao no runtime Meta. Consulte [HARDWARE.md](HARDWARE.md) para limitacoes e procedimento de teste no Quest 3S.
