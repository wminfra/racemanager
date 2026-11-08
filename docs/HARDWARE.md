# Hardware e integrações futuras

## Equipamento conhecido

- ODESC 4.2 como controlador do volante artesanal baseado em motor de hoverboard.
- Arduino Leonardo USB HID para pedais e botões de troca de marcha.
- Futuro headset Meta Quest 3S.

O Racing Manager não conversa com a ODESC nem com o Arduino nesta etapa e não envia comandos de torque, movimento ou calibração. Não se presume que os dispositivos aparecem como HID utilizável: isso deve ser verificado no Gerenciador de Dispositivos e por uma ferramenta de inspeção antes de selecionar uma biblioteca ou associar eixos/botões.

## Caminho de integração

As integrações de dispositivo devem ser adapters isolados em `app/modules/`, sem acoplar eventos HID à API de sessões. Uma futura prova de conceito deve:

1. Registrar VID/PID, nome, interface, eixos e botões observados no hardware real.
2. Detectar conexão/desconexão e expor estado somente de leitura.
3. Validar leituras sem movimentar atuadores e com o volante mecanicamente seguro.
4. Manter controles de parada e procedimentos próprios do controlador, fora do painel público.
5. Só estudar efeitos de força depois de revisar documentação, limites elétricos/mecânicos e recuperação.

VR permanece desacoplado do estado de sessão e poderá ser acrescentado por um adapter/runtime separado quando o Meta Quest 3S estiver disponível para ensaio.


## Diagnostico VR (etapa inicial)

O painel Operator possui selecao Monitor / VR Oculus / VR SteamVR e o botao **TESTAR VR**. Nesta etapa, Oculus e SteamVR sao opcoes de diagnostico; nao alteram a inicializacao de sessoes. O botao nao cria sessao, nao inicia SteamVR nem abre o ACC.

- Processos do Meta Link e o servico `OVRService` indicam disponibilidade de software/runtime, nao conexao do Quest. Headset conectado e colocado permanecem `DESCONHECIDO` ate uma API suportada fornecer confirmacao.
- No modo SteamVR, o Racing Manager consulta OpenVR somente quando `vrserver.exe` ja esta ativo. A consulta verifica o HMD, sua classe e a validade/resultado do tracking. Falhas de acesso retornam `DESCONHECIDO`; o diagnostico nunca usa o ultimo headset conhecido como prova atual.
- A biblioteca OpenVR usa um registro temporario de caminhos e grava seus logs em pasta temporaria do usuario. Nao modifica o Registro do Windows, OpenXR nem a configuracao global do SteamVR.
- OpenVR nao fornece confirmacao portatil de que o headset esta colocado. Esse campo fica `DESCONHECIDO`. A API documentada do runtime Meta oferece `HmdMounted`/`HmdPresent` dentro de uma sessao OVR; o Racing Manager ainda nao cria essa sessao.

### Sondagem local de 7 de novembro de 2026

`vrserver.exe`, `vrmonitor.exe`, `vrcompositor.exe` e o servico Meta `OVRService` foram encontrados ativos. A consulta OpenVR do Racing Manager retornou `Init_NoServerForBackgroundApp`, portanto conexao do HMD e tracking nao foram confirmados. O log SteamVR contem evento `DismissableWarning_RequestDisconnectReconnect` as 20:06:55. Esse evento e um aviso de solicitar desconexao/reconexao, nao prova por si so o estado atual do HMD. A sondagem nao iniciou o ACC.

O operador relatou anteriormente que o modo Oculus abriu o ACC apos o aviso de incompatibilidade e que o SteamVR mostrou o headset rastreado a 72 Hz. Esses sao relatos de teste anteriores, nao foram reproduzidos nesta execucao. Estereo, resposta do tracking dentro do ACC e comparacao de desempenho continuam pendentes.

### Procedimento para teste no local

1. Conecte o Quest pelo Meta Quest Link e confirme o ambiente PCVR manualmente.
2. Selecione **VR SteamVR** e use **TESTAR VR**. Considere conectado/rastreado somente quando a consulta OpenVR confirmar HMD conectado e tracking valido.
3. Retire e recoloque o headset; desconecte e reconecte Link. Repita o diagnostico e observe a interface Meta/SteamVR. O estado de headset colocado continua desconhecido nesta versao.
4. Testes Oculus VR e SteamVR dentro do ACC sao separados e exigem observacao visual no headset. O Racing Manager ainda nao inicia esses modos.

Recomendacao provisoria: usar **SteamVR na proxima validacao controlada**, pois o operador relatou que o SteamVR reconheceu tracking. Isso ainda nao e recomendacao de operacao comercial; primeiro e preciso repetir o ensaio apos reconexao e confirmar o ACC em VR. Oculus VR permanece experimental devido ao aviso de incompatibilidade relatado.
