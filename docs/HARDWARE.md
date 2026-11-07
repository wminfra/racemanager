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
