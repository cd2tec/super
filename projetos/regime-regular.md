# Projeto 2: a CBS e o IBS no regime regular

Guia de continuação para o agente que vai tocar este projeto. Escrito em 23/11/2026, logo depois de o projeto 1 (a skill `cbs-ibs-simples`) entrar em produção. Os documentos internos do projeto 1 ficam fora do git; o que deles importa para cá está resumido aqui.

Antes de qualquer código, leia o `AGENTS.md` inteiro, principalmente [Skills que espelham a calculadora da Receita](../AGENTS.md#skills-que-espelham-a-calculadora-da-receita). Depois converse com quem pediu o projeto para fechar as decisões da seção 5. Elas mudam o que vai ser construído, e nenhuma foi tomada ainda.

## 1. O que é

Uma skill nova do plugin `cd2-super` que faz a conta da CBS, do IBS (e, se entrar no escopo, do Imposto Seletivo) para empresas do regime regular, Lucro Real e Lucro Presumido, com a mesma conta da calculadora oficial da Receita Federal: https://github.com/ReceitaFederal/calculadora-rtc-regime-regular (MIT).

É a metade que falta. Hoje o site (`docs/index.html`, seção `#reforma`) diz "Lucro Real / Presumido: Ainda não" e manda para https://calc-imposto.cd2.com.br/. Quando a skill sair, essa linha muda.

Os princípios do projeto 1 continuam valendo, nas palavras de quem pediu: o plugin é gratuito, então a skill tem que funcionar perfeitamente para qualquer pessoa que instale, sem servidor da CD2 no meio e sem restrição de funcionamento. E quando a Receita mudar a calculadora, a skill acompanha sem depender de alguém lembrar.

"Sempre a versão mais nova" quer dizer a mesma conta, conferida todo dia contra o código oficial rodando. Não quer dizer rodar o Java da Receita na máquina de quem usa.

## 2. Onde o projeto 1 parou

- Release `cd2-super-v0.6.0` no ar, com a skill `cbs-ibs-simples` nos pacotes Claude, Codex, DSH e GPT.
- O motor em Python (`core/cd2-super/skills/cbs-ibs-simples/scripts/`) bate 12.013 de 12.013 casos com a calculadora oficial do Simples 1.0.2, base V0073.
- O robô `.github/workflows/conferencia-receita.yml` roda todo dia às 9h17 UTC. A primeira execução verde de ponta a ponta foi a https://github.com/cd2tec/super/actions/runs/45241913201 (nada mudou na Receita, ele terminou sem mexer em nada).
- O caminho "a Receita publicou tabela nova e o robô publicou sozinho" ainda não rodou de verdade. Foi ensaiado só localmente.

## 3. Fatos do repositório da Receita (conferidos em 23/11/2026)

- Última release: 1.7.2, de 19/11/2026. A anterior é a 1.6.1. Licença MIT. Branch padrão `main`.
- Java 25, Spring Boot 4.1.2, Maven Wrapper. 481 arquivos Java em `src/main`, cerca de 33 mil linhas (o Simples tem uns 9 mil no motor). Pacote raiz `br.gov.serpro.rtc`.
- O mesmo repositório traz o código **e** as migrações (`flyway/sql`, com `criacao` e `manutencao`). No projeto 1 eram dois repositórios; aqui é um só. Isso muda o robô (seção 6).
- Base: o mesmo `calculadora-pro.db` que o Simples usa, montado por `./mvnw flyway:migrate -Dflyway.configFiles=./flyway/flyway-pro.conf`. O motor do regime regular lê bem mais tabelas que as 32 do Simples.
- Perfil `offline` em `src/main/resources/application-offline.yml`. Sobe em `http://localhost:8080/api`. JAR: `target/api-regime-geral.jar`.
- Endpoints principais (todos sob `/api/calculadora`): `POST /regime-geral` (falha no primeiro erro), `POST /observabilidade/regime-geral` (erro por item), `GET /observabilidade/erros?codigo=...`, base de cálculo unificada da NF-e e da NFS-e, geração e validação de XML e os dados abertos (CST, cClassTrib, NCM, NBS, alíquotas de referência da União, UF e município). Exemplos em `docs/exemplos.md` do repositório da Receita.
- Existem **duas** cópias de `ArredondamentoUtils` aqui: `core/util` e `shared/util`. No projeto 1 partimos de que a política numérica é idêntica byte a byte entre os dois projetos. Confira as duas cópias contra a do Simples antes de reaproveitar o `numeros.py`.
- Diferente do Simples, existe instância hospedada: `piloto-cbs.tributos.gov.br/servico/calculadora-consumo/api` roda o regime regular.

## 4. O que a CD2 já tem do regime regular

- O site https://calc-imposto.cd2.com.br/ é a calculadora gratuita da CD2 para o regime regular. O código está no repositório privado `cd2tec/cd2-calc-imposto` (TypeScript). Ele já faz, à mão, o que o robô faz para o Simples: `npm run sync` traz a base da Receita e `npm run paridade` compara com a calculadora oficial. Se a conta deste novo agente não tiver acesso a esse repositório, peça; é a melhor referência de port que existe.
- O `retail-profile.json` desse miniapp (da CD2, MIT) é a fonte do perfil de varejo que a `sugestao.py` do Simples usa para sugerir `cClassTrib`. A sugestão do regime regular nasce do mesmo arquivo.
- Atenção à diferença que o projeto 1 descobriu: no Simples, o motor oficial não aplica o percentual de redução do regime regular (todo `tpRBSN` do perfil é 1, e a conta sai igual byte a byte com ou sem redução). No regime regular a redução vale de verdade. Não copie para cá a frase do Simples sobre redução sem efeito.

## 5. Decisões em aberto

Para fechar na conversa com quem pediu, antes do desenho. Ao lado de cada uma, a recomendação de quem escreveu este guia, que não é decisão tomada.

1. **Uma skill nova ou a `cbs-ibs-simples` crescendo?** Recomendação: skill nova (nome a combinar, por exemplo `cbs-ibs-regime-regular`). As conversas, as entradas e as entregas são diferentes, e o robô já trata cada pasta de `conferencia/` como uma skill.
2. **Fonte de quem usa: o GitHub da Receita ou o site da CD2?** O regime regular tem a calc-imposto. Se a skill for "a conta da calculadora da CD2", ela segue [Somar uma calculadora nova](../AGENTS.md#somar-uma-calculadora-nova) com `site_url` da calc-imposto e UTM; se for "a conta da Receita", segue o caminho do Simples, com `fonte_externa: true`. Recomendação: a conta é da Receita (é o que o robô confere), e o fechamento convida para a calc-imposto.
3. **Port completo ou chamar a API hospedada da Receita?** A API do piloto existe, mas é servidor de terceiro, pode sair do ar ou mudar e exige internet; o princípio do projeto é não depender de servidor. Recomendação: port em Python, como no Simples, e usar a API do piloto no máximo como segunda referência no robô.
4. **Escopo do port.** O repositório tem muito além da conta (XML, base de cálculo unificada da NF-e/NFS-e, dados abertos). Recomendação: só `POST /observabilidade/regime-geral` e o que ele chama, mais a leitura de NCM e cClassTrib para a sugestão. XML fica fora, como no projeto 1. Decidir se o Imposto Seletivo entra.
5. **Qual versão o robô segue.** No Simples, a calculadora segue a tag da release e as migrações seguem o último commit da `main` do outro repositório. Aqui é um repositório só: seguir a tag para o código e a `main` para `flyway/sql`, ou a tag para os dois? Seguir a `main` nas tabelas traz tabela nova antes, mas a base pode andar à frente do JAR da release. Recomendação: código pela tag, tabelas pela `main`, como no Simples, desde que a conferência de 100% com o JAR da tag continue sendo a trava.
6. **Público e perguntas da conversa.** No Simples foram dois perfis (o lojista e o contador). Confirmar se valem os mesmos e o que a pessoa precisa informar (regime, UF, município, NCM, cClassTrib, valor, data).
7. **GPT.** Calculadora tem `gpt: true`. O limite de 8.000 caracteres nas instruções já apertou no Simples (7.997). Ver se cabe e o que fica no conhecimento.

## 6. O que reaproveitar e o que precisa generalizar

O projeto 1 deixou o montador, o comparador e o robô parametrizados pela pasta `conferencia/<skill>/config.json`, mas sobraram pontos com o Simples escrito no código. Antes de somar a segunda skill:

**Já serve como está**

- `scripts/receita_base.py`: monta a base a partir de `flyway/sql`, reduzida às `tabelas` do `config.json`, e grava a `CD2_META`.
- `scripts/receita_paridade.py`: roda a bateria contra a oficial e grava as esperadas.
- A estrutura de `conferencia/<skill>/` (`config.json`, `meta.json`, `bateria.py`, `casos-escritos.json`, `esperadas.json`) e o job `listar`, que já roda uma vez por pasta.
- `tests/versao_da_base.py` (`@ancorado_na`) e o padrão de tirar as amostras dos testes da própria base.
- O motor de decisão do robô (`receita_robo.py decidir`) e a tabela do `AGENTS.md`.

**Precisa generalizar**

- `.github/workflows/conferencia-receita.yml`
  - Clona dois repositórios fixos, em `$RECEITA/sn` e `$RECEITA/rr` (linhas 110 a 124), e o build faz o Flyway em `/rr` e o `mvn package` em `/sn` (linhas 137 a 155). Com um repositório só, o build e o Flyway rodam no mesmo clone.
  - O nome do JAR está escrito à mão (`api-simples-nacional.jar`, linhas 145, 167 e 217) e a porta é `PORTA: "8082"` no `env` do job (linha 68). O `config.json` já tem `oficial.jar` e `oficial.porta`; o workflow deve ler de lá. O regime regular sobe na 8080 por padrão.
  - Com duas skills, os dois `conferir` podem rodar em paralelo na mesma máquina? Hoje cada job é uma máquina própria do matrix, então a porta não colide, mas confira.
- `scripts/receita_robo.py`
  - Os textos de CHANGELOG, PR e issue dizem "calculadora oficial do Simples" (linhas 234, 670 a 674, 716 a 721) e a lista do PR cita `simples_motor.py` e `simples_base.py` (linha 746). Leve o nome humano da calculadora e os arquivos do motor para o `config.json`.
  - A detecção supõe dois repositórios (calculadora e migrações) com commits separados no `meta.json`. Com um só, `calculadora.repo` e `migracoes.repo` ficam iguais; confira que a classificação (`src/main` contra `flyway/sql`) continua certa quando as duas mudanças vêm do mesmo diff.
- `numeros.py` (a política numérica): hoje mora em `scripts/` da `cbs-ibs-simples`. O desenho do projeto 1 previa um módulo que as duas skills copiam. Os kits do `harnesses.json` são por `tipo`, e as duas skills são `calculadora`, então um kit novo iria também para `fim-escala-6x1` e `custo-vrva`. Opções: copiar o arquivo e ter um teste que confere que as duas cópias são iguais, ou ensinar o sync a somar um kit por skill. Recomendação: a cópia com teste, que é o menor passo.
- `tests/test_workflows.py` e `tests/test_receita_robo.py` têm casos com o Simples escrito; somar os do regime regular.

## 7. Roteiro sugerido

1. Conversar e fechar a seção 5. Escrever o desenho em `interno/` (fora do git, como manda o `AGENTS.md`) e o plano.
2. Generalizar o robô (seção 6) num PR próprio, sem a skill nova, com o Simples continuando verde. Dispare o robô à mão depois do merge: o projeto 1 só pegou os dois defeitos do workflow numa execução de verdade.
3. Montar a base do regime regular com o `receita_base.py` e subir a calculadora oficial 1.7.2 no perfil `offline`, à mão, como em [Rodar a conferência local](../AGENTS.md#skills-que-espelham-a-calculadora-da-receita).
4. Escrever a bateria (`conferencia/<skill>/bateria.py`) a partir da base e dos testes da Receita (175 arquivos em `src/test`), e o `casos-escritos.json`.
5. Portar o motor método a método, até a conferência fechar 100%. Só vale 100%.
6. Escrever a conversa, o `render.py`, a sugestão, o GPT, os testes, o `skills.json` e o resto da lista de [Somar uma calculadora nova](../AGENTS.md#somar-uma-calculadora-nova).
7. Atualizar a seção `#reforma` do `docs/index.html` e o README (a linha "Ainda não"), subir a versão (minor) e escrever o `CHANGELOG.md`.

## 8. Armadilhas que o projeto 1 já pagou

- No GitHub Actions, o contexto `runner` não vale no `env:` do job. Grave os caminhos no `$GITHUB_ENV` num primeiro passo (é o passo "Pastas de trabalho" de hoje).
- Saída de passo pulado é nula, e `null == '0'` dá verdadeiro nas expressões do GitHub. Toda comparação de saída com número leva também `steps.X.conclusion == 'success'`; o `tests/test_workflows.py` confere isso.
- Valide o workflow antes do PR: `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7 -shellcheck=`.
- A release só sai quando o `plugin.core.json` muda num push na `main`. O link `releases/latest/download/...` aponta para a release mais nova, não para a `main`.
- A "latest" do GitHub segue marca e data, não a ordem das versões. O robô compara por número.
- Fora da `main`, o robô só confere. Teste de verdade é disparo manual na `main` depois do merge.
- O `--checar-versao` da skill compara a data do último commit de `flyway/sql` com `meta.conferencia.data`, não com `base.data`.

## 9. Pendências soltas do projeto 1

- Republicar o GPT `cbs-ibs-simples` e conferir se o editor aceita o `base-receita.db` no conhecimento.
- Somar ao GPT a linha "onde achar o NCM", cortando outra frase para caber nos 8.000 caracteres.
- O GitHub avisa que `actions/checkout@v4`, `actions/setup-python@v5` e as actions de artefato usam Node 20, que está saindo. Hoje rodam no Node 24 e funcionam; atualizar as versões é ajuste pequeno, de preferência junto do passo 2 do roteiro.
