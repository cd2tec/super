# Metodologia

Como a skill chega aos números, o que ela espelha da calculadora oficial da Receita Federal, o que fica de fora e como a conta é conferida.

É resultado gerencial. A skill foi feita pela CD2 Tech com o código e as tabelas da calculadora oficial e não é um aplicativo da Receita Federal. Confira com o seu contador.

## O que a skill faz

A skill recebe uma operação de empresa do Simples Nacional (documento, regime, data, linha de alíquotas efetivas e itens) e devolve a CBS e o IBS de cada item e da operação, com a memória de cálculo. É a conta do cálculo não assistido da calculadora oficial do Simples Nacional, `POST /calculadora/observabilidade/simples-nacional/nao-assistido`, com o mesmo JSON de entrada e de saída: os mesmos nomes de campo, os mesmos estados de item (`CALCULADO`, `NAO_CALCULADO`, `NAO_IMPLEMENTADO`, `INCONSISTENCIA_ENTRADA` e `FALHA_TECNICA`), os mesmos motivos e os mesmos códigos de erro `VSN-`.

Também responde às consultas da calculadora oficial: a lista de atividades do Simples (`calc.py --atividades`), uma atividade pelo código (`calc.py --atividade`), a descrição de um código de erro (`calc.py --erro`) e as versões (`calc.py --versao`).

A conta mora em `scripts/simples_motor.py`, a leitura da base em `scripts/simples_base.py` e a política numérica em `scripts/numeros.py`. A base é `references/base-receita.db`. O `scripts/calc.py` é a linha de comando, e o `scripts/render.py` monta a entrega sem mudar nenhum número.

## O que é espelhado

Duas fontes da Receita Federal, ambas públicas e sob licença MIT:

- **O código da calculadora do Simples Nacional**, `github.com/ReceitaFederal/calculadora-rtc-simples-nacional`, portado de Java para Python, método a método.
- **As tabelas**, das migrações SQL de `github.com/ReceitaFederal/calculadora-rtc-regime-regular` (pasta `flyway/sql`), aplicadas na mesma ordem do Flyway. A base da skill guarda só as tabelas que o motor e a sugestão leem, mais a versão da base.

As versões espelhadas ficam no `meta.json` da conferência e dentro da própria base. Saem em `calc.py --versao`, no campo `versao` de toda resposta do `calc.py` e no selo de toda entrega: "Conta igual à da calculadora oficial do Simples Nacional versão X, base Y, conferida em DD/MM/AAAA."

Na entrada da skill no plugin (versão 0.6.0): calculadora do Simples na versão 1.0.2, migrações no commit `f1eebd7`, base V0073 e conferência de 22/11/2026, com 12.013 casos iguais em 12.013.

## O que fica de fora

- **O endpoint clássico** `POST /calculadora/observabilidade/simples-nacional`, que a própria Receita marca como obsoleto.
- **O cálculo assistido**, que lê a Tabela de Alíquotas Efetivas do PGDAS-D. Essa tabela não está no código nem na base públicos.
- **A alíquota efetiva do Simples.** A calculadora oficial não a calcula a partir do faturamento, e a skill também não. Ela entra na operação como a linha de alíquotas efetivas de CBS e IBS do período, por anexo, que sai do PGDAS-D e que o contador tem. A skill não calcula o DAS.
- **Geração ou validação de XML** de documento fiscal.
- **O regime regular** da CBS e do IBS. Quando a empresa do Simples opta por ele, a calculadora oficial só registra a receita (estado `NAO_CALCULADO`, motivo `REGIME_SOMENTE_RECEITA`).
- **O Imposto Seletivo.** NFC-e, NF-e e os outros documentos com CRT exigem o valor dele por item (`valorImpostoSeletivo`, "0.00" quando não há), e a calculadora oficial só o repassa. A skill também não o calcula.

## Como a conta é conferida

A conferência compara, caso a caso, a resposta da calculadora oficial rodando com a resposta da skill, para a mesma requisição.

1. **A base sai das migrações oficiais.** O `receita_base.py` do repositório do Super aplica as migrações SQL da Receita, na mesma ordem do Flyway, e grava a base reduzida da skill e o hash do conteúdo no `meta.json`. A mesma base montada pelo Flyway oficial tem que dar o mesmo conteúdo.
2. **A calculadora oficial roda de verdade.** O JAR da versão espelhada é compilado do código-fonte da Receita e sobe no perfil `offline`, contra a base do Flyway.
3. **A bateria sai da base.** `conferencia/cbs-ibs-simples/bateria.py` gera os casos de três fontes: os cenários dos testes de integração da Receita que passam por HTTP, portados uma vez (`casos-escritos.json`); uma matriz de combinações de documento, regime, atividade, classificação, situação do ISS, compra governamental e amostras de NCM e NBS, tirada das tabelas vigentes com sorteio de semente fixa; e entradas ruins, para exercitar cada código `VSN-`. Como a matriz sai da base, ela cresce sozinha quando a Receita inclui atividade ou classificação.
4. **A comparação é do JSON inteiro.** O `receita_paridade.py` do repositório do Super manda cada caso para a calculadora oficial e para a skill e compara campo a campo, com os números na forma escrita ("1.0" e "1.00" são diferentes). Só os campos da lista `ignorar` ficam de fora (veja a seção abaixo).
5. **Uma amostra fica gravada.** As respostas oficiais de 400 casos que cobrem tudo o que a bateria cobre ficam em `conferencia/cbs-ibs-simples/esperadas.json`. Os testes do dia a dia rodam a skill contra elas, só com Python, sem a calculadora oficial.

A bateria tem que cobrir todo código `VSN-` do catálogo, todo estado de item, todo motivo de não cálculo e todo regime. O que o cálculo não assistido não consegue produzir com a base de hoje fica numa lista de exceções explícita, com o motivo de cada um, e o teste de cobertura aceita só o que está nela.

## Campos ignorados na conferência

A conferência compara cada resposta da calculadora oficial com a da skill. A lista `ignorar` de `conferencia/cbs-ibs-simples/config.json` tem só o que muda sozinho entre duas execuções da mesma pergunta.

- `timestamp` (raiz do corpo): a hora em que a calculadora oficial montou a resposta de erro. Aparece nas 37 respostas de erro de validação (status 400 e 422 no formato `application/problem+json`, 34 do grupo `global` e 3 de consulta sem a data). Duas gravações com 81 segundos de intervalo diferiram nesse campo e em mais nenhum, nos 195 casos. Não é regra de cálculo.

Nada além disso entra na lista. Campo que muda por outro motivo é erro da skill, não ruído.

## Exceções de cobertura

Nove itens não aparecem pelo cálculo não assistido com a base de hoje. Cada um está em `excecoes_cobertura` de `conferencia/cbs-ibs-simples/config.json`, com o motivo completo:

- **`ANEXO_SEM_ALIQUOTA_CBS`, `VSN-029`:** só existem no cálculo assistido, em que a linha de alíquotas vem da base. No não assistido ela vem na requisição, e a falta vira outro erro.
- **`VSN-028`:** valida o NI do contribuinte, campo que o contrato do não assistido não tem. Nenhum fluxo da versão espelhada emite o código.
- **`FALHA_TECNICA`, `VSN-999`, `VSN-000`:** precisam de uma exceção que ninguém prevê. O caminho é conferido só em Python, com falhas injetadas.
- **`VSN-025`, `VSN-026`:** precisam de um buraco na parametrização (compra governamental ou tipo de receita bruta sem linha vigente), e as tabelas não têm nenhum a partir de 01/01/2027. O caminho é portado e conferido só em Python, com uma base de teste com o buraco. Se a Receita passar a ter um caso real, a matriz pega e o item sai da lista.
- **`VSN-046`:** só é emitido no modo rígido de validação de nomenclatura, e o cálculo não assistido valida sempre no modo permissivo.

## Política numérica

A mesma da calculadora oficial (`ArredondamentoUtils`), em `scripts/numeros.py`:

- aritmética decimal exata, com `decimal.Decimal`; nenhum número de ponto flutuante na conta;
- divisão com 34 dígitos significativos;
- arredondamento `ROUND_HALF_EVEN` (ABNT NBR 5891);
- valor fiscal com 2 casas, arredondado uma vez por item; os totais são a soma dos itens;
- alíquota publicada com 4 casas; na conta, a alíquota entra com todas as casas informadas.

Valores de entrada seguem o formato da calculadora oficial: texto com ponto decimal e até 2 casas nos valores ("1234.50"), e alíquotas em pontos percentuais ("1.1612" é 1,1612%).

## Números digitados por gente

O motor não deduz, não corrige e não completa a entrada: faz o que a calculadora oficial faz, inclusive nos erros. Quem digita "1.234,50" recebe da calculadora oficial um erro de formato.

Por isso a conversa em português passa a operação pelo `calc.py --humano` antes do motor. Ele aceita vírgula decimal, separador de milhar, número sem aspas, NCM com pontos e data DD/MM/AAAA, e devolve em `normalizacoes` cada troca que fez. Quando um número com ponto é ambíguo ("1.000" pode ser mil ou um), ele lê como decimal e avisa; a skill pergunta à pessoa antes de entregar. A porta técnica, de quem cola o JSON, roda sem `--humano`: a resposta é a que a calculadora oficial daria.

## Classificação sugerida

O motor exige a classificação tributária (`cClassTrib`) de cada item. Quem não sabe recebe uma sugestão do `scripts/sugestao.py`, a partir do NCM.

A sugestão é a leitura de varejo da CD2, não código da Receita. Ela fica fora do motor e fora do selo de conferência: o selo vale para a conta. Os percentuais de redução e a lista do Imposto Seletivo vêm da base da Receita; a escolha das classificações prováveis para cada NCM é nossa. Quando um NCM está em mais de uma lista, a sugestão com a menor redução vem primeiro, para o imposto mostrado nunca ficar abaixo do real; a regra geral, 000001, vem por último. Todo resultado que passou por ela diz: "Classificação sugerida pela CD2, confirme com o contador."

A redução da sugestão é a da tabela de classificação. No Simples, o efeito da classificação sai da conta oficial, pelo tipo de receita bruta (`tpRBSN`) que ela resolve e mostra em cada item.

## Limites

- **Datas a partir de 01/01/2027.** Todas as tabelas de parâmetro do Simples começam a valer nessa data. Antes dela a calculadora oficial recusa a operação, e até lá a skill é um simulador do que vem.
- **Linha de alíquotas do contador.** A conta usa a linha de alíquotas efetivas informada. Sem ela, a skill só roda o exemplo ilustrativo da documentação oficial (anexo I: CBS 1,1612% e IBS 0,0129%; anexo III: CBS 2,3874% e IBS 0,0263%, e sem redistribuição do ISS, CBS 2,3591% e IBS 0,0260%), sempre marcado como exemplo e nunca como a conta da empresa.
- **Classificação sugerida fora do selo.** Confirme com o contador.
- **Janela de atualização.** Quando a Receita muda a calculadora, a skill acompanha depois que a mudança é portada e conferida. A skill avisa, onde houver internet, se a Receita já publicou versão mais nova que a espelhada (`calc.py --checar-versao`), e quem instalou recebe a base nova ao atualizar o plugin.
- **Sem execução de código, sem conta.** A conta depende da base e do motor. Onde a plataforma não roda Python, a skill diz que não calcula e por quê.
