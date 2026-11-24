# Metodologia

Como a skill chega aos números, o que ela espelha da calculadora oficial do regime regular da Receita Federal, o que fica de fora, de onde vêm as alíquotas dos cenários e como a conta é conferida.

É resultado gerencial. A skill foi feita pela CD2 Tech com o código e as tabelas da calculadora oficial e não é um aplicativo da Receita Federal. Confira com o seu contador.

## O que a skill faz

A skill recebe uma venda de mercadoria de empresa do regime regular (Lucro Real ou Lucro Presumido) em NF-e ou NFC-e, com UF, município, data e itens, e devolve a CBS, o IBS estadual, o IBS municipal e o Imposto Seletivo de cada item e da operação, com a memória de cálculo. É a conta do `POST /calculadora/observabilidade/regime-geral` da calculadora oficial, com o mesmo JSON de entrada e de saída: os mesmos nomes de campo, os mesmos estados de item (`CALCULADO`, `CALCULO_SIMULADO`, `NAO_IMPLEMENTADO`, `INCONSISTENCIA_ENTRADA` e `FALHA_TECNICA`), os mesmos códigos de erro (`VAL-`, `REG-` e `CAL-`), com erro por item e total só dos itens calculados. O `POST /calculadora/regime-geral`, que para no primeiro erro, também é portado e entra na conferência.

A calculadora oficial não distingue Lucro Real de Lucro Presumido: não há menção a eles no código do cálculo. A conta é a mesma, e a skill não pergunta.

A conta mora nos módulos `rr_*.py` de `scripts/` (o motor em `scripts/rr_motor.py`, a leitura da base em `scripts/rr_base.py`, a entrada e os erros em `scripts/rr_entrada.py` e `scripts/rr_erros.py`, o processamento do item em `scripts/rr_item.py`, as expressões do tratamento tributário em `scripts/rr_expressao.py`, a base de cálculo em `scripts/rr_base_calculo.py`, a CBS e o IBS em `scripts/rr_calculo.py`, o Imposto Seletivo em `scripts/rr_calculo_is.py`, a memória e a saída em `scripts/rr_memoria.py` e `scripts/rr_saida.py`) e a política numérica em `scripts/numeros.py`. A base é `references/base-receita.db`. O `scripts/calc.py` é a linha de comando, o `scripts/sugestao.py` sugere a classificação e o `scripts/render.py` monta a entrega sem mudar nenhum número.

## O que é espelhado

Uma fonte da Receita Federal, pública e sob licença MIT: `github.com/ReceitaFederal/calculadora-rtc-regime-regular`. Dela vêm:

- **o código da calculadora**, na tag da release, portado de Java para Python, método a método, inclusive nos erros;
- **as tabelas**, das migrações SQL da pasta `flyway/sql`, no último commit da `main`, aplicadas na mesma ordem do Flyway. A base da skill guarda só as tabelas que o motor e a sugestão leem, mais a versão da base.

As versões espelhadas ficam no `meta.json` da conferência e dentro da própria base. Saem em `calc.py --versao`, no campo `versao` de toda resposta do `calc.py` e no selo de toda entrega.

Na entrada da skill no plugin (versão 0.7.0): calculadora do regime regular na versão 1.7.2 (commit `32df719`), migrações no commit `f1eebd7`, base V0073 e conferência de 24/11/2026, com 1.368 casos iguais em 1.368.

## Alíquotas: cenários e simulação

Até 31/12/2026, a calculadora oficial resolve as alíquotas de referência pela base da Receita, e informar alíquota é erro (REG-036). De 01/01/2027 em diante, a base não traz alíquota: as três alíquotas nominais (CBS, IBS estadual e IBS municipal) são obrigatórias (sem elas, REG-037), e o item calculado sai `CALCULO_SIMULADO`. Por isso todo resultado de 2027 em diante é simulado, e toda entrega diz isso.

Quando a pessoa não informa as alíquotas, a skill usa um de dois cenários, os mesmos dois botões da calculadora gratuita da CD2 para o regime regular, copiados para `references/aliquotas.json` com versão, data de conferência (22/11/2026) e fontes:

| Cenário | CBS | IBS estadual | IBS municipal | De onde vem |
|---|---|---|---|---|
| Em 2027 (`em-2027`, o padrão) | 11,3% | 0,05% | 0,05% | As hipóteses da calculadora gratuita da CD2, com as fontes de `references/aliquotas.json`; a divisão do IBS é a do exemplo oficial da Receita (`docs/exemplos.md`) |
| Reforma completa (`reforma-completa`) | 8,8% | 8,85% | 8,85% | CBS de 8,8% e IBS de 17,7%, as hipóteses da calculadora gratuita da CD2, com as mesmas fontes |

A divisão do IBS da reforma completa em 8,85% estadual e 8,85% municipal é hipótese da CD2, sem fonte oficial. O total do IBS do item não muda com ela; muda só a parte de cada ente. A entrega que usa esse cenário diz isso.

A data padrão é 01/01/2027 nos dois cenários. O cenário não depende da data: as tabelas da base (reduções, vínculos, Imposto Seletivo) seguem a vigência delas. Para 2028 a 2032, os anos da transição, não há cenário: quem simula um desses anos informa as três alíquotas. Alíquota informada vence o cenário, e alíquota informada só em parte vai como veio (a calculadora oficial responde REG-037).

O Imposto Seletivo não tem cenário. A alíquota dele ainda não foi definida em lei, e de 2027 em diante a calculadora oficial pede que ela seja informada (sem ela, REG-035). A pessoa informa a alíquota ad valorem e, quando o produto tiver, a ad rem; sem ela, a skill usa o exemplo do documento oficial da Receita (10% ad valorem sobre a cerveja), sempre marcado como exemplo em toda a entrega.

## O que fica de fora

O `calc.py` recusa antes da conta, com mensagem própria, o que a skill não calcula:

- **NFS-e e serviços (NBS).** O motor oficial calcula, mas a skill é para venda de mercadoria.
- **Outros documentos** (`tpDoc` que não seja 55 nem 65: CT-e, NFCom, BP-e, NF3e e os demais). Operação sem `tpDoc` segue para a conta, como na calculadora oficial.
- **Compra governamental** (`gCompraGov`).

Também ficam de fora a base de cálculo unificada da NF-e e da NFS-e, a geração e a validação de XML, o pedágio e as consultas de dados abertos da calculadora oficial. A skill lê a base direto quando precisa (na sugestão de classificação, por exemplo).

## Como a conta é conferida

A conferência compara, caso a caso, a resposta da calculadora oficial rodando com a resposta da skill, para a mesma requisição.

1. **A base sai das migrações oficiais.** O `receita_base.py` do repositório do Super aplica as migrações SQL da Receita, na mesma ordem do Flyway, e grava a base reduzida da skill e o hash do conteúdo no `meta.json`. A mesma base montada pelo Flyway oficial tem que dar o mesmo conteúdo.
2. **A calculadora oficial roda de verdade.** O JAR da versão espelhada é compilado do código-fonte da Receita e sobe no perfil `offline`, contra a base do Flyway.
3. **A bateria sai dos testes da Receita e da base.** `conferencia/cbs-ibs-regime-regular/bateria.py` gera os casos de duas fontes: os cenários dos testes da Receita que passam por HTTP no regime geral, portados uma vez (242 casos em `casos-escritos.json`), e uma matriz tirada das tabelas vigentes, com sorteio de semente fixa, em dez grupos: entrada (Bean Validation, data, UF e município, tipo de documento), nomenclatura (NCM, CST, classificação, vínculos, anexos, alíquotas nominais), CBS e IBS sem redução, redução, diferimento, tributação regular, Imposto Seletivo (ad valorem, ad rem, sem o grupo e sem alíquota), memória de cálculo, totais com itens válidos e inválidos, e o `/regime-geral` com vários itens. As datas da matriz são 31/12/2026 (alíquotas da base) e 01/01/2027 (alíquotas informadas).
4. **A comparação é do JSON inteiro.** O `receita_paridade.py` do repositório do Super manda cada caso para a calculadora oficial e para a skill e compara campo a campo, com os números na forma escrita ("1.0" e "1.00" são diferentes), e o status. Só os campos da lista `ignorar` ficam de fora.
5. **Uma amostra fica gravada.** As respostas oficiais de 550 casos (todos os escritos e pelo menos um gerado de cada tipo) ficam em `conferencia/cbs-ibs-regime-regular/esperadas.json`. Os testes do dia a dia rodam a skill contra elas, só com Python, sem a calculadora oficial.

O robô de conferência do repositório roda essa conferência todo dia, com as tabelas novas da Receita, e só publica com 100%.

## Campos ignorados na conferência

A lista `ignorar` de `conferencia/cbs-ibs-regime-regular/config.json` tem só o que muda sozinho entre duas execuções da mesma pergunta.

- `timestamp` (raiz do corpo): a hora em que a calculadora oficial montou a resposta de erro (status 400 e 422, no formato `application/problem+json`). Não é regra de cálculo.

Nada além disso entra na lista. Campo que muda por outro motivo é erro da skill, não ruído.

## Testes da Receita fora da bateria

Os testes da Receita que cobrem só o que está fora do escopo ficam fora da bateria. Cada um está em `excluidos_da_bateria` do `config.json` da conferência, com o motivo. Em resumo:

- **Compra governamental** (`gCompraGov`): os testes de compras governamentais e o caso T5 dos casos fixos de arredondamento. Do lote de arredondamento, só os perfis de compra governamental saem; os outros são gerados pela bateria com o mesmo gerador portado.
- **NFS-e, NBS e outros documentos:** os testes de NFS-e, de NFCom e de `tpDoc` fora de 55 e 65, e os de validação de NBS. Quando um teste mistura um item de mercadoria com um de serviço, a bateria usa o mesmo cenário com um NCM no lugar da NBS.
- **Endpoints fora do escopo:** XML, base de cálculo unificada, pedágio, dados abertos e o catálogo de erros (que a skill descreve pelo `calc.py --erro`).
- **Consultas diretas aos repositórios**, sem cálculo: a mesma regra entra pelos cenários de NCM com e sem exceção.
- **Um item com várias violações de Bean Validation de uma vez** (o item 1 do teste de observabilidade): a calculadora oficial não devolve esses erros sempre na mesma ordem. A bateria usa a mesma entrada sem esse item, e cada violação aparece sozinha no grupo de entrada.

## Limites conhecidos da conferência

O que a bateria não consegue provar com a calculadora oficial 1.7.2 e a base V0073, e como cada caso foi tratado:

- **A ordem de vários erros de Bean Validation numa requisição só não é estável na calculadora oficial.** Duas execuções da mesma requisição podem listar os erros em ordens diferentes. A bateria usa um erro por caso, e a skill devolve os erros numa ordem fixa.
- **Datas com o ano 0000.** A calculadora oficial aceita a data; a skill responde como JSON ilegível. Nenhuma operação real tem essa data, e o caso fica fora da bateria.
- **Monofasia.** Na base V0073, as classificações de tributação monofásica (620xxx) não têm tratamento tributário, e a calculadora oficial responde CAL-007. O caminho da conta monofásica foi portado método a método, mas não dá para prová-lo ao vivo. Quando a Receita publicar o tratamento, a matriz da bateria pega a classificação e a conferência passa a cobri-lo.
- **Quantidade em notação científica na memória de cálculo.** O Java escreve algumas quantidades em notação científica (`BigDecimal.toString()`), e a skill faz o mesmo. Na 1.7.2, nenhuma resposta oficial passa por esse caminho (as classificações que usam a quantidade dão CAL-007), então ele é conferido por um teste em Python com valores tirados do JDK, sem caso na bateria.
- **`/regime-geral` com vários itens inválidos.** A calculadora oficial processa os itens em paralelo (`parallelStream`) e devolve o erro de um item qualquer. A skill devolve o erro do primeiro item na ordem da operação, e a bateria usa no máximo um item inválido por caso nesse endpoint. A conversa da skill usa a observabilidade, que devolve o erro de cada item.

## Política numérica

A mesma da calculadora oficial (`ArredondamentoUtils`, nas duas cópias do código da Receita, conferidas iguais à do Simples), em `scripts/numeros.py`, cópia byte a byte da skill do Simples:

- aritmética decimal exata, com `decimal.Decimal`; nenhum número de ponto flutuante na conta;
- divisão com 34 dígitos significativos;
- arredondamento `ROUND_HALF_EVEN` (ABNT NBR 5891);
- valor fiscal com 2 casas, arredondado por item; os totais são a soma dos itens calculados.

## Imposto Seletivo

De 2027 em diante, item cujo NCM tem incidência do Imposto Seletivo na data precisa do grupo do IS (a classificação e o CST do IS, a base, a quantidade e a unidade); sem ele, a calculadora oficial recusa o item (REG-013). Com o grupo e sem alíquota, REG-035. O IS só é admitido em NF-e e NFC-e. Nos itens com IS, a base da CBS e do IBS é a base do item mais o IS, como no motor oficial.

## Classificação sugerida

O motor exige a classificação tributária (`cClassTrib`) e o CST de cada item. Quem não sabe recebe uma sugestão do `scripts/sugestao.py`, a partir do NCM.

A sugestão é a leitura de varejo da CD2, não código da Receita. Ela fica fora do motor e fora do selo de conferência: o selo vale para a conta. Uma sugestão só sai se a base da Receita aceitar o NCM com aquela classificação, no tipo de documento e na data, pelas mesmas consultas que o motor usa. Os percentuais de redução e a incidência do Imposto Seletivo vêm da base; a escolha das classificações prováveis para cada NCM é nossa, do perfil de varejo da calculadora gratuita da CD2. A regra geral, 000001, vem por último. No regime regular a redução vale: a classificação escolhida muda o valor da CBS e do IBS. Todo resultado que passou por ela diz: "Classificação sugerida pela CD2, confirme com o contador."

O `sugestao.py` não corrige o NCM da operação. Com pontos ou com 7 dígitos, ele avisa como fica o NCM só com os dígitos ou com o zero à esquerda e se esse código existe na base; a conversa mostra isso à pessoa e só usa o NCM que ela confirmar.

## Limites

- **De 2027 em diante, simulação.** As alíquotas são de cenário ou informadas, não as definitivas.
- **A divisão do IBS na reforma completa é hipótese da CD2.**
- **Imposto Seletivo sem alíquota em lei.** Vem da pessoa ou de um exemplo marcado.
- **Classificação sugerida fora do selo.** Confirme com o contador.
- **Janela de atualização.** Quando a Receita muda a calculadora, a skill acompanha depois que a mudança é portada e conferida. A skill avisa, onde houver internet, se a Receita já publicou versão mais nova que a espelhada e se publicou tabelas novas depois da última conferência (`calc.py --checar-versao`), e quem instalou recebe a base nova ao atualizar o plugin.
- **Sem execução de código, sem conta.** A conta depende da base e do motor. Onde a plataforma não roda Python, a skill diz que não calcula e por quê.
