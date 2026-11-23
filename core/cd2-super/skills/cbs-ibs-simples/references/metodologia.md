# Metodologia

(Rascunho da Tarefa 4. O texto completo entra na tarefa de documentação.)

## Campos ignorados na conferência

A conferência compara cada resposta da calculadora oficial com a da skill. A lista `ignorar` de `conferencia/cbs-ibs-simples/config.json` tem só o que muda sozinho entre duas execuções da mesma pergunta.

- `timestamp` (raiz do corpo): a hora em que a calculadora oficial montou a resposta de erro. Aparece nas 37 respostas de erro de validação (status 400 e 422 no formato `application/problem+json`, 34 do grupo `global` e 3 de consulta sem a data). Duas gravações com 81 segundos de intervalo diferiram nesse campo e em mais nenhum, nos 195 casos. Não é regra de cálculo.

Nada além disso entra na lista. Campo que muda por outro motivo é erro da skill, não ruído.
