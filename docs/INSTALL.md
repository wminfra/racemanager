# Racing Manager — instalação inicial

## Requisitos

- Windows 10/11 ou Linux; Python 3.12 recomendado.
- Rede local privada para o tablet/celular. Não exponha a porta à internet.

## Preparar

No PowerShell, dentro da pasta do projeto:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Edite `.env` e defina `RACING_SECRET_KEY` com uma string aleatória. Gere uma usando `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Não compartilhe nem versione esse arquivo.

## Criar administrador e iniciar

```powershell
python -m scripts.create_admin
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`0.0.0.0` disponibiliza o painel na rede local; use somente em rede confiável e configure o Firewall do Windows para permitir acesso apenas à rede privada. Para uso no próprio PC, prefira `--host 127.0.0.1`. Em produção, habilite HTTPS e `RACING_COOKIE_SECURE=true` atrás de um proxy local configurado.

## Interfaces

- Kiosk: `http://localhost:8000/kiosk`
- Administrador: `http://localhost:8000/admin`
- Operador: `http://localhost:8000/operator`
- OpenAPI: `http://localhost:8000/docs`

O administrador pode cadastrar operadores no painel. Preços iniciais de demonstração (15/30 min) são inseridos na primeira inicialização; ainda não há editor de preços nesta entrega. O status do simulador fica “não conectado” até existir integração de processo/jogo. As sessões são encerradas pelo serviço de backend ao atingir a duração contratada; a tela apenas exibe o tempo restante reportado pelo servidor.

## Testes

```powershell
pytest
```

## Arquitetura e limites desta etapa

`app/api` trata HTTP, autorização e WebSocket autenticado para atualizações de sessão; `services` concentra operações reutilizáveis; `models` e `database` isolam persistência. `frontend/{kiosk,admin,operator}` são páginas leves servidas pelo FastAPI. `app/modules` reserva integrações futuras de Steam, jogos, dispositivos e VR. SQLite é apropriado para a instalação inicial de um equipamento; implantações multiestabelecimento devem migrar a um banco cliente-servidor. Não há integração Steam/Assetto Corsa, controle de processos, relatórios por período, gestão de preços ou MFA nesta fase.
