# Racing Manager — operação local

## Requisitos

- Windows 10/11 x64 e Python 3.12.
- Microsoft Edge.
- Steam instalada, conta dedicada autenticada manualmente e Assetto Corsa AppID 244210 instalado.
- Rede local privada para o tablet do operador. O sistema não deve ser publicado na internet.

## Instalar

No PowerShell, na pasta do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

No `.env`, configure `RACING_SECRET_KEY` com uma chave nova. Gere-a com:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Não compartilhe nem versione `.env`. Se a chave já tiver sido copiada para algum exemplo ou repositório, gere outra. `RACING_STEAM_PATH` e `RACING_ASSETTO_CORSA_PATH` são opcionais quando a detecção automática da Steam funciona.

Crie o administrador uma vez:

```powershell
python -m scripts.create_admin
```

Não há usuário ou senha padrão.

## Iniciar os serviços e interfaces

Em uma janela PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\Start-RacingManager.ps1
```

O script principal chama separadamente o backend e o Edge. Eles iniciam o backend oculto na rede local e abrem o Edge no endereço `/kiosk` em modo `--kiosk` de tela cheia, usando um perfil isolado em `.runtime/edge-profile`. O Kiosk fica atrás do Assetto Corsa e reaparece quando o jogo fecha. Os scripts não criam tarefas de inicialização, não alteram Explorer, Registro, políticas de grupo, contas ou configurações do Windows.

Interfaces:

- Kiosk no monitor do simulador: `http://localhost:8000/kiosk`.
- Admin: `http://localhost:8000/admin`.
- Operador no mesmo computador: `http://localhost:8000/operator`.
- Operador no tablet/celular: `http://<IP-local-do-computador>:8000/operator`.
- Documentação da API: `http://localhost:8000/docs`.

Para descobrir o IP local, execute `ipconfig`. O backend escuta na porta 8000 em todas as interfaces. Permita essa porta somente na rede privada pelo Firewall do Windows; não configure redirecionamento de porta no roteador. Para executar manualmente sem o script, use `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

## Modo Kiosk e manutenção

O Edge recebe `--kiosk` e `--edge-kiosk-type=fullscreen`: a interface não exibe barra de endereço, abas ou controles normais do navegador e o cliente não recebe um botão de saída. Para manutenção com teclado conectado ao simulador, pressione `Alt+F4` para sair do Edge e volte a iniciar `Start-Kiosk.ps1` depois.

O modo do Edge não desativa a barra de tarefas ou o shell do Windows. Esta etapa não aplica Assigned Access nem esconde a barra de tarefas, conforme a restrição de não alterar o Windows. Não deixe teclado ou acesso ao desktop disponíveis ao público. A configuração para iniciar automaticamente após ligar o PC fica para uma etapa posterior, revisada com procedimento de recuperação.

Antes de encerrar o ambiente, termine a sessão pelo painel do operador. Então execute:

```powershell
.\scripts\Stop-TestEnvironment.ps1
```

O script recusa encerrar o backend se houver uma sessão ativa e valida os PIDs guardados pelo próprio Racing Manager antes de fechar processos do Edge/backend. O banco e os logs são preservados em `data/` e `.runtime/`.

## Steam e Assetto Corsa

Abra a Steam no computador, entre manualmente com a conta dedicada ao equipamento e instale o Assetto Corsa. O sistema nunca lê nem armazena a senha Steam; inicia o jogo pelo cliente com `steam.exe -applaunch 244210`, verifica o AppID/manifesto e monitora `AssettoCorsa.exe`/`acs.exe` dentro da pasta de instalação.

O diagnóstico local verifica o cliente ativo e uma conta marcada como recente em loginusers.vdf; isso não é uma API oficial de autenticação nem prova que a Steam aceitará a sessão. Se faltar instalação ou conta selecionada, o painel bloqueia o início e informa o motivo. Se o jogo não aparecer em 120 segundos após a solicitação, a sessão fica registrada como falha técnica.

Como o equipamento é comercial e acessível ao público, **não presuma que uma licença Steam pessoal de varejo permite esse uso**. Consulte o [programa oficial Steam PC Café](https://partner.steamgames.com/doc/sitelicense), os [requisitos e cadastro](https://partner.steamgames.com/doc/sitelicense/licensees/signup) e o [modelo de servidor PC Café e pool de licenças](https://partner.steamgames.com/doc/sitelicense/licensees/gettingstarted/licensemodelpc). A documentação Steam informa que o servidor local participa da gestão de licenças comerciais e que só títulos incluídos pelo publicador estão disponíveis. Este adaptador ainda **não integra o Steam PC Café Server nem confirma licença comercial do AppID 244210**. Portanto, não use o sistema para sessões pagas até confirmar a elegibilidade/licença do Assetto Corsa e configurar o modo de acesso aprovado pela Steam para essa estação. Não use a conta pessoal do proprietário nem compartilhe credenciais.

## Sessões, falhas e recuperação

- As durações são configuradas no Admin e validadas novamente pela API.
- O operador só inicia quando o adaptador informa Steam/jogo prontos; a interface não decide duração nem estado.
- O estado inicial é `Aguardando`; só passa para `Em andamento` quando o processo do Assetto Corsa é encontrado.
- Um supervisor no backend calcula o tempo, envia avisos em 2 minutos e 30 segundos e fecha o jogo no prazo. O navegador só mostra o tempo reportado pelo servidor.
- No prazo, o integrador pede fechamento normal às janelas do jogo e, após o período de tolerância, encerra somente os PIDs cujos caminhos executáveis correspondem à instalação conhecida do Assetto Corsa.
- Se o jogo sumir antes do prazo, a sessão fica `Interrompida`; falha de início, travamento ou encerramento controlado malsucedido ficam `Falha técnica`.
- Ao reiniciar o computador/serviço, sessões que estavam abertas são fechadas como interrompidas/falha técnica. O jogo não é relançado.
- Uma restrição única no banco impede duas sessões aguardando/em andamento simultâneas no mesmo simulador. O identificador UUID e a transição terminal tornam os ticks repetidos idempotentes.
- Em bancos da versão anterior, a inicialização copia metadados de `racing_sessions` para a nova tabela operacional `sessions` de forma idempotente. Valores monetários não são lidos nem copiados; as tabelas antigas permanecem preservadas para não destruir dados existentes.

O limite de tentativas de login é mantido em memória do processo e reinicia com ele. Use apenas uma instância local do backend nesta entrega.

## Testes

```powershell
pytest
```

Os testes usam um adaptador Steam simulado e não precisam de Steam nem Assetto Corsa instalados.

## Hardware e VR (preparação)

Veja [HARDWARE.md](HARDWARE.md). ODESC 4.2, Arduino Leonardo, volante artesanal, pedais, botões e Meta Quest 3S ainda não são acessados pelo software. Nenhum comando de torque ou movimento é enviado. A tela Kiosk usa viewport fluido e resolução 1920×1080 como alvo visual, mas ainda não implementa runtime ou apresentação VR.
