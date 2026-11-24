# Changelog

As notas de cada versão entram na release do GitHub. Use um título `## <versão>` por versão, da mais recente para a mais antiga.

## 0.7.0

Calculadora nova: `cbs-ibs-regime-regular`, a CBS, o IBS e o Imposto Seletivo no regime regular (Lucro Real e Lucro Presumido), com a mesma conta da calculadora oficial da Receita Federal.

- **Fiscal:** `cbs-ibs-regime-regular` calcula a CBS, o IBS estadual, o IBS municipal e o Imposto Seletivo de uma venda de mercadoria em NF-e ou NFC-e, item a item, com a memória de cálculo. Lucro Real e Lucro Presumido têm a mesma conta, e a skill não pergunta qual é.
- A conta é um port para Python da calculadora oficial do regime regular, versão 1.7.2, com a base V0073 montada das migrações oficiais. Na conferência de 24/11/2026, 1.368 casos deram o mesmo JSON da calculadora oficial rodando, em 1.368, inclusive nos erros.
- De 2027 em diante a base da Receita não traz alíquota, e todo resultado é simulado. Sem alíquota informada, a skill usa um de dois cenários, mostrados antes da conta: Em 2027 (o padrão, CBS 11,3% e IBS 0,1%) e Reforma completa (CBS 8,8% e IBS 17,7%). A divisão do IBS da reforma completa entre estado e município é hipótese da CD2. O Imposto Seletivo não tem cenário: a alíquota vem da pessoa ou de um exemplo marcado.
- Duas portas: quem testa o ERP cola o JSON da calculadora oficial e recebe a resposta oficial intocada; quem toca a loja conversa em português. NFS-e, NBS, outros documentos e compra governamental são recusados com mensagem própria.
- Sugere a classificação tributária pelo NCM, como leitura da CD2, fora do selo de conferência. No regime regular a redução vale na conta.
- A skill não é um aplicativo da Receita Federal. O `NOTICE` passa a citar o motor dela.
- Tem GPT, o CD2 CBS, IBS e IS no regime regular.
- O site e o README deixam de dizer "Ainda não" para o Lucro Real e o Presumido. A `cbs-ibs-simples` passa a indicar a skill nova para o regime regular; a conta dela e as de `fim-escala-6x1` e `custo-vrva` não mudaram.

## 0.6.0

Calculadora nova: `cbs-ibs-simples`, a CBS e o IBS no Simples Nacional, com a mesma conta da calculadora oficial da Receita Federal.

- **Fiscal:** `cbs-ibs-simples` calcula a CBS e o IBS de uma operação do Simples (NFC-e, NF-e ou NFS-e) para datas a partir de 01/01/2027. Até lá, a conta é uma simulação do que passa a valer.
- A conta é um port para Python da calculadora oficial do Simples Nacional, versão 1.0.2, com a base V0073 montada das migrações oficiais. Na conferência de 22/11/2026, 12.013 casos deram o mesmo JSON da calculadora oficial rodando, em 12.013.
- Duas portas: quem testa o ERP cola o JSON da calculadora oficial e recebe a resposta oficial intocada; quem toca a loja conversa em português, escolhe a atividade numa lista e informa a linha de alíquotas efetivas do PGDAS, que vem do contador. A skill nunca estima alíquota.
- Sugere a classificação tributária pelo NCM, como leitura da CD2, fora do selo de conferência, e avisa quando o item está na lista do Imposto Seletivo.
- A skill não é um aplicativo da Receita Federal. O `NOTICE` traz o aviso de copyright e a licença MIT dos dois repositórios da Receita.
- Tem GPT, o CD2 CBS e IBS no Simples, com 19 arquivos de conhecimento.
- O card de uma skill cujo site não é da CD2 (campo `fonte_externa` do `skills.json`) leva o link sem UTM e o rótulo "Ver a calculadora oficial". As contas de `fim-escala-6x1` e `custo-vrva` não mudaram.

## 0.5.0

Segunda onda de rotinas. Oito skills novas fecham o dia e a semana, aprofundam a relação com fornecedor e olham a oferta depois de pronta.

- **Diretoria:** `resumo-da-semana`, com a pauta da reunião de resultados, e `fechamento-do-dia`, com decisões, pendências, mensagens e as três de amanhã.
- **Compras:** `cenario-de-custo`, que simula um aumento com e sem repasse em script próprio, e `ficha-do-fornecedor`, a visão completa de um fornecedor.
- **Comercial:** `resultado-da-oferta`, `revisao-de-oferta` e `radar-de-concorrencia`, que calcula o índice de preço em script próprio e usa só preço público.
- **Loja:** `visita-de-loja`, com os números da loja, o que olhar no salão e as perguntas para o gerente.
- `perfil-da-operacao` agora gera a voz da loja a partir do que ela já publicou, e `comunicacao-de-ofertas` passa a usá-la.
- O aviso de autorização em `comunicacao-de-ofertas` vale só para sorteio, vale-brinde e concurso, como na lei. Brinde comum não precisa dele.

## 0.4.0

Rotinas do dia. Sete skills novas organizam o plugin pelo dia de quem toca o supermercado, usando as consultas e as provas dos relatórios.

- **Seu dia:** `perfil-da-operacao`, `briefing-do-dia` e `sinais-da-operacao`. O briefing compara a venda do mês com a meta proporcional aos dias corridos e a venda de ontem com o mesmo dia da semana anterior.
- **Fornecedores:** `preparo-de-negociacao` e `ata-de-negociacao`. Na ata, todo acordo traz o trecho de origem.
- **Ofertas:** `plano-de-ofertas` e `comunicacao-de-ofertas`. As regras de oferta foram conferidas em 09/11/2026 e são apoio para conferência, não parecer jurídico.
- As rotinas nunca escrevem no ERP. E-mail sai como rascunho, WhatsApp como texto pronto, e o que nasce de conteúdo externo é mostrado antes de sair.
- No pacote do Claude, o Archeo MCP Creator da CD2 já vem configurado: é só fazer login. Sem conta, tudo segue com arquivo.
- O relatório aceita os blocos `agenda`, `acordos` e `pendencias` e uma linha de origem em cada bloco. A conta das calculadoras e dos relatórios não mudou.

## 0.3.0

Troca de nome. O plugin `cd2-varejo` agora se chama `cd2-super`, e o repositório e o marketplace agora se chamam `super`. As skills e as contas não mudaram.

- Instalação nova no Claude Code: `/plugin marketplace add cd2tec/super` e `/plugin install cd2-super@super`. No Codex: `codex plugin marketplace add cd2tec/super` e `codex plugin add cd2-super@super`.
- Para quem já instalou: remova o plugin `cd2-varejo` e o marketplace `cd2-skills`, e instale de novo com os nomes acima.
- Os ZIPs da release passam a se chamar `cd2-super-<pacote>.zip`, e o site agora fica em https://cd2tec.github.io/super/.

## 0.2.1

Correção de instalação. A descrição do plugin passou de 500 caracteres, e o envio do ZIP no Claude recusava o pacote. A descrição ficou mais curta, e o `--check` agora confere esse limite. As skills não mudaram.

## 0.2.0

Dez skills novas, todas com nome em português, e teste livre e sem prazo.

- **Oito relatórios gerenciais**: `relatorio-vendas`, `relatorio-estoque`, `relatorio-compras`, `relatorio-precos`, `relatorio-perdas`, `relatorio-financeiro`, `relatorio-fiscal` e `relatorio-contabil`. Relatório com linhas, classes e a prova da conta: a soma das partes tem de bater com o total, e, se não bater, a página abre em vermelho. O fiscal e o contábil são conferência, não documento oficial.
- **`painel-varejo`**: painel visual da operação, com totalizadores, linha do tempo, mapa de calor e leitura. Sem conexão, entrega a foto do período.
- **`ficha-archeo-mcp`**: ficha de ferramenta para cadastrar no Archeo MCP Creator da CD2, com o SQL e os parâmetros. Sem conexão, entrega um rascunho marcado como não validado.
- **Funcionam sem conexão.** Com o banco do ERP conectado (pelo Archeo MCP Creator da CD2 ou por outro MCP de dados), a prova fecha no banco. Sem conexão, a skill segue com um arquivo exportado (CSV ou Excel), dado colado ou digitado, e o selo é "Conferido só contra o arquivo", nunca "Prova fechada". Cada relatório traz a linha "Para exportar", com o que pedir ao ERP.
- **Teste livre e sem prazo.** Sai a regra dos 90 dias, nas calculadoras também: testar na sua operação é livre, sem suporte, garantia nem responsabilidade da CD2, e o uso contínuo no dia a dia segue sendo para clientes CD2 com contrato ativo (`COMMERCIAL.md`).
- A conta das duas calculadoras não mudou. A saída delas mudou só na frase da licença.
- README, site e pacotes mostram as doze skills. Os GPTs seguem só para as duas calculadoras.
- Para quem já instalou o plugin `cd2-varejo`: as skills novas entram com nomes novos, em português, e as duas calculadoras mantêm os nomes `fim-escala-6x1` e `custo-vrva`. Se você usava as análises de ERP de outro pacote, peça pelos nomes novos.

## 0.1.0

Publicada em 2026-11-07. Primeira versão do plugin `cd2-varejo`, da CD2 Tech.

- **`fim-escala-6x1`**: o impacto do fim da escala 6x1 (Emenda Constitucional 141/2026) na loja ou na rede. Pessoas a contratar, custo por mês e por ano, setores no semáforo e o que fazer primeiro, sem e com acordo coletivo. Parâmetros conferidos em 02/11/2026.
- **`custo-vrva`**: quanto o VR e o VA custam de verdade frente ao Decreto 12.712/2025. Taxa acima do teto de 3,6%, dinheiro parado além dos 15 dias, bandeiras no semáforo e o que fazer primeiro. Parâmetros conferidos em 06/11/2026.
- Mesma conta das calculadoras gratuitas da CD2, em Python só com biblioteca padrão, com os valores de conferência das metodologias como testes.
- Conversa guiada em sete passos, entregas no design system da CD2 Tech (página visual, planilha, WhatsApp ou texto) e assinatura com logo e aviso de isenção.
- Pacotes para Claude Code e Claude Cowork, Codex e ChatGPT Work, DeepSeek Harness (developer preview) e dois GPTs para a GPT Store: CD2 Escala 6x1 e CD2 VR/VA: quanto custa.
- Licença CC BY-NC-ND 4.0, com avaliação gratuita na empresa por até 90 dias e uso contínuo para clientes CD2 (`COMMERCIAL.md`).
- README e site no GitHub Pages.
