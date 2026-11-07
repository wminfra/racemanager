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
