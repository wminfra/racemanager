# Encerramento seguro do Assetto Corsa Competizione

## Auditoria anterior

O mecanismo anterior enviava `WM_CLOSE`, esperava somente 8 segundos e, em seguida, chamava automaticamente `psutil.Process.terminate()` e `Process.kill()`. Nao havia `taskkill /F`, `TerminateProcess` ou `CloseMainWindow`. O processo era selecionado pelo nome `AC2-Win64-Shipping.exe` e pelo caminho exato da instalacao; o launcher `acc.exe` e a Steam nao eram alvos. Os chamadores eram encerramento manual, timer, monitor de nao resposta, cancelamento de inicializacao e recuperacao apos reinicio. Rotas e supervisor compartilhavam lock em producao, mas faltava estado de encerramento idempotente.

Essa politica poderia contribuir para fechamento abrupto, mas nao comprova a causa do estado Windows ?Nao respondendo?. O log local mais recente do ACC terminou com `LogD3D11RHI: Shutdown` e `LogExit` as 22:38:13. A consulta do log Application nao encontrou `AppHang` do ACC nos dois dias anteriores. Foi localizado um evento WER `RADAR_PRE_LEAK_64` para o executavel do ACC as 13:51; ele indica um diagnostico de recurso/memoria e nao e evidencia de que o fechamento forcado causou o travamento. Nao ha evidencia suficiente para excluir problema de jogo, renderizacao, driver, memoria ou conflito de runtime.

## Fluxo atual

1. Um servico unico, `GameShutdownService`, obt?m processos pelo executavel configurado e revalida PID, nome e caminho antes de enviar qualquer mensagem.
2. No Windows, uma unica solicitacao `WM_CLOSE` e enviada as janelas visiveis de nivel superior associadas ao PID validado. Isso equivale a solicitar o fechamento pela janela; nao usa taskkill.
3. O servico aguarda `RACING_GAME_CLOSE_FIRST_WAIT_SECONDS` (padrao 15 s) e depois `RACING_GAME_CLOSE_ADDITIONAL_WAIT_SECONDS` (padrao ate 30 s). A mensagem nao e reenviada durante essas esperas.
4. Se o processo continuar ativo, o estado torna-se `CLOSE_FAILED`. A sessao permanece pendente e o simulador indisponivel. O timer nao inicia uma nova tentativa em cada tick.
5. O Operator pode pedir uma segunda tentativa normal. O servico e idempotente: pedidos simultaneos nao duplicam o fechamento.
6. A acao forcada exige uma caixa de confirmacao do operador/admin com aviso de possivel perda de dados. So entao o servico tenta `terminate`, espera `RACING_GAME_FORCE_TERMINATE_WAIT_SECONDS` e, se necessario, `kill`, revalidando o caminho do processo. Se a identidade do PID nao puder ser comprovada, nenhum sinal e enviado e o simulador continua bloqueado.
7. A sessao so e finalizada/liberada depois que a ausencia do processo for confirmada. Timer, operador, interrupcao, cancelamento e recuperacao no startup usam o mesmo servico.

## Configuracao

Os valores podem ser definidos no `.env` (nao ha tela Admin para eles nesta etapa):

- `RACING_GAME_CLOSE_FIRST_WAIT_SECONDS=15`
- `RACING_GAME_CLOSE_ADDITIONAL_WAIT_SECONDS=30`
- `RACING_GAME_FORCE_TERMINATE_WAIT_SECONDS=3`

Alteracoes exigem reinicio do backend. Os limites de validacao ficam em `app/core/config.py`.

## Falha e recuperacao pelo Operator

Quando o estado for `CLOSE_FAILED`, o painel mostra a falha e habilita:

- **Tentar fechamento normal novamente**: envia uma nova e unica solicitacao `WM_CLOSE` e executa os dois periodos de espera.
- **Forcar encerramento**: abre uma confirmacao explicita sobre perda de configuracoes/dados ainda nao salvos. Cancelar nao altera o processo.

Enquanto o ACC continuar ativo, o backend nao aceita uma nova sessao. Se um PID nao puder ser verificado ou continuar ativo mesmo apos a acao forcada, mantenha o computador em manutencao; nao encerre Steam/VR nem processos pelo nome. Registros estruturados `[ACC-SHUTDOWN]` incluem UTC, PID, executavel, origem, metodo, resultado e duracao.

## Testes

Su?te automatizada: processos simulados cobrem fechamento imediato/demorado, ignorar `WM_CLOSE`, repeticao normal, confirmacao de forca, concorrencia, identidade nao verificavel, timer e Steam sem encerramento. Esses testes nao encerram ACC real.

Teste real controlado: use `tests/real/test_acc_live.py` somente com Steam autenticada manualmente, sem outra sessao em curso. O teste abre ACC pela Steam e deixa a sessao de 2 minutos pedir o fechamento normal; nao usa forca automatica. Se o jogo nao fechar dentro de 45 segundos, o teste falha deixando o processo ativo para recuperacao manual pelo Operator. Nao marque a integracao real como aprovada se o processo nao sumir e a Steam permanecer aberta.
