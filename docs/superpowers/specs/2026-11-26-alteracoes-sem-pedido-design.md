# Alterações sem pedido · design

- **Data:** 2026-11-26
- **Status:** aprovado, a implementar
- **Base:** [design v1](2026-11-22-marga-design.md), com Word, PowerPoint e HTML da `feat/word-powerpoint-html`

## 1. Objetivo

Ao comparar versões, o Marga aponta os trechos que o agente mudou sem um comentário pedindo: alucinações, reescritas além do pedido, "melhorias" que ninguém pediu. Cada ponto aparece como um alerta, parecido com um comentário, com três decisões: **aceitar** a alteração, **pedir para refazer** (vira um comentário para a próxima rodada) ou **ignorar**.

## 2. Decisões

| Pergunta | Decisão |
|---|---|
| Até onde uma mudança perto de um comentário conta como pedida? | A **linha inteira** do comentário. No LaTeX, no Word, no PowerPoint e no HTML, uma linha costuma ser um parágrafo. |
| Quais versões são verificadas? | Só as que **respondem a uma rodada** (origem `agente`). Versões manuais não geram alertas. |
| Calcular na hora ou gravar? | **Gravar no registro da versão** (abordagem A abaixo). Versões registradas antes do recurso são verificadas na primeira vez que forem comparadas. |
| Nomes das ações | ✓ Aceitar · ↩️ Pedir para refazer · 🚫 Ignorar · ↺ Reabrir |

## 3. Abordagens consideradas

| | Abordagem | Prós | Contras |
|---|---|---|---|
| **A (escolhida)** | Verificar ao registrar a vN+1 e gravar os alertas em `.marga/docs/<doc>/unrequested.json`, com as decisões no mesmo registro | O alerta reflete o que o agente recebeu: editar ou excluir um comentário depois não muda o que foi apontado. O aviso sai já no registro | Se a regra mudar no futuro, os alertas já gravados não são refeitos |
| B | Calcular a cada comparação e gravar só as decisões | Sempre usa a regra atual | Excluir ou mudar o tipo de um comentário depois da rodada faz alertas antigos aparecerem ou sumirem; decisões presas a uma chave derivada do texto |
| C | Alertas como um novo tipo de anotação em `annotations.json` | Apareceriam na Revisão como comentários | Pacote, links, listas e rodadas teriam de aprender a deixá-los de fora, com risco de irem para o pacote do agente |

## 4. Regras de detecção

**Passo verificado.** vN → vN+1, quando `vN+1.round` existe: a vN+1 responde à rodada R, que exportou o documento na vN.

**Textos e linhas.** Os de `version_texts`: o fonte no LaTeX e no texto; o texto extraído no Word, no PowerPoint, no HTML e no PDF avulso. As linhas são as desse texto.

**Regiões da rodada.** Só contam as anotações do documento listadas em `RoundInfo.annotations` que têm âncora na vN (`anchor_at(N)`); uma âncora órfã ou desatualizada não cobre nada.

| Papel | Quem | Efeito |
|---|---|---|
| pedido | comentários `corrigir`, `reescrever`, `remover`, `inserir`, `erro_compilacao`; as duas pontas dos links da rodada (`RoundInfo.links`) com relação diferente de `ver_tambem` | as linhas inteiras da âncora na vN são **linhas pedidas** |
| protegido | comentários `nao_alterar` | o trecho exato da âncora na vN é **protegido** |
| contexto | `pergunta`, `nota` e marca, quando não são ponta de um link de edição | só entram no motivo do alerta |

Uma anotação pode ter dois papéis: um `nao_alterar` que é ponta de um link de edição cobre as linhas dele, mas o trecho exato continua protegido.

**Mudanças.**

1. Em cada arquivo presente nas duas versões, os blocos alterados por linha (`line_opcodes`). Blocos separados por até 2 linhas iguais viram um bloco só, para um parágrafo reformatado não se partir.
2. Em cada bloco, os tokens sem espaço (`\w+` ou um caractere de pontuação) da vN e da vN+1 são comparados com `SequenceMatcher(autojunk=False)`. Cada opcode diferente de `equal` é uma mudança, com um trecho na vN e outro na vN+1 (vazio num ponto de inserção ou de remoção). Espaços e quebras de linha nunca contam como mudança.
3. Um bloco com mais de 3000 tokens num dos lados vira uma mudança só, sem a comparação por palavra.

**Cobertura.**

- Troca ou remoção (trecho não vazio na vN): é pedida se **todas** as linhas da vN que o trecho toca são pedidas.
- Inserção (trecho vazio na vN): é pedida se o intervalo entre o token anterior e o seguinte da vN toca uma linha pedida. Esse intervalo atravessa as linhas em branco, então um parágrafo inserido depois do trecho de um *inserir* é pedido.
- Protegido: uma mudança cujo trecho na vN cruza um trecho protegido (uma inserção, estritamente dentro dele) vira alerta `protegido`, mesmo numa linha pedida.

**Agrupamento.** Mudanças não pedidas seguidas, do mesmo tipo de alerta, sem mudança pedida entre elas e em linhas da vN iguais ou vizinhas viram um alerta. O alerta vai do início da primeira ao fim da última, nos dois lados. Os alertas são numerados (1, 2…) na ordem dos arquivos e das posições na vN.

**Arquivos.**

- Um arquivo que só existe na vN+1 vira um alerta `arquivo_novo`.
- Um arquivo que só existe na vN vira um alerta `arquivo_removido`, a não ser que todas as suas linhas não vazias sejam pedidas (um *remover* que cobre o arquivo todo).

**Remoção pura.** O trecho na vN+1 fica vazio. Para o comentário ter onde se ancorar, o alerta estende os dois lados até um token vizinho igual: o anterior, se estiver na mesma linha da vN; senão, o seguinte. Assim, `new.quote` não fica vazio e `old.quote` continua sendo o texto que, no lugar de `new.quote`, desfaz a remoção.

**Motivo.** Texto gravado no alerta; vários se juntam com "; ".

| Situação | Motivo |
|---|---|
| trecho protegido | "trecho protegido por c4 (não alterar)" |
| a mudança toca linhas pedidas e não pedidas | "vai além das linhas de c3" |
| na região de uma pergunta | "c5 era uma pergunta: responder sem editar" |
| na região de uma nota ou marca | "c6 é só contexto (nota)", "m2 é só uma marca" |
| nenhuma anotação por perto | "fora dos comentários da rodada R" |
| arquivo novo ou removido | "arquivo criado na rodada R sem comentário que o peça" (ou "apagado") |

## 5. Dados

Em `core/models.py`:

```python
@dataclass
class UnrequestedChange:
    n: int                      # número do alerta na versão verificada (1, 2…)
    kind: str                   # sem_pedido | protegido | arquivo_novo | arquivo_removido
    file: str
    old: Anchor | None          # trecho na vN (citação vazia: inserção; None: arquivo novo)
    new: Anchor | None          # trecho na vN+1 (None: arquivo removido)
    reason: str
    related: list[str] = field(default_factory=list)  # anotações da rodada que tocam o trecho
    status: str = "pendente"    # pendente | aceita | comentada | ignorada
    comment: str | None = None  # o comentário criado por "Pedir para refazer"
    decided_at: str | None = None


@dataclass
class UnrequestedCheck:
    version: int                # vN+1
    previous: int               # vN
    round: int
    checked_at: str
    items: list[UnrequestedChange] = field(default_factory=list)
```

`.marga/docs/<doc>/unrequested.json` guarda as verificações por versão: `{"3": {"version": 3, "previous": 2, "round": 2, "checked_at": "…", "items": […]}}`.

- Escrita com `write_json`, com ler-modificar-gravar dentro de um lock. JSON corrompido levanta `MargaError` e nunca é sobrescrito.
- Uma verificação sem alertas também é gravada (`"items": []`), para não ser refeita.
- O arquivo sai junto com o documento (`delete_doc` apaga `.marga/docs/<doc>/`).
- É estado interno do workspace, como `annotations.json`: não é contrato com o agente.

## 6. Operações

Em `core/unrequested.py` (novo, sem Streamlit):

- `detect(old_texts, new_texts, regions, previous, version, round_n)`: a detecção pura, sobre textos e regiões. É o que os testes unitários exercitam.
- `round_regions(ws, doc, info, n)`: as regiões da rodada no documento, com papel, tipo e âncora na vN.
- `check_version(ws, doc, version)`: lê a verificação gravada ou verifica e grava. Devolve `None` numa versão sem rodada.
- `load_checks(ws, doc_id)` e a gravação de uma decisão.
- `draft_comment(check, item)`: o rascunho do "Pedir para refazer".

| Alerta | Tipo | Instrução | Sugestão |
|---|---|---|---|
| troca | corrigir | "Alteração sem pedido na rodada R (vN → vN+1): volte ao texto da vN." | `old.quote` |
| inserção | remover | "Trecho inserido sem pedido na rodada R (vN → vN+1): remova-o." | nenhuma |
| remoção | corrigir | "Trecho removido sem pedido na rodada R (vN → vN+1): restaure-o." | `old.quote` (estendida) |
| protegido | corrigir (ou remover, se for inserção) | começa com "Este trecho estava protegido por c4 (não alterar)." e pede a volta ao texto da vN | como acima |
| arquivo novo | remover | "O arquivo X foi criado sem pedido na rodada R: apague-o e desfaça as referências a ele." Âncora na primeira linha não vazia | nenhuma |
| arquivo removido | (sem rascunho: não há onde ancorar na vN+1) | | |

Em `core/actions.py`:

- `register_version`: depois da re-ancoragem e da importação da resposta, chama `check_version` na nova versão. `RegisterReport.unrequested` guarda quantos alertas saíram, e o resumo ganha "⚠️ N alteração(ões) sem pedido".
- `decide_unrequested(ws, doc_id, version_n, n, status)`: `aceita`, `ignorada` ou `pendente` (reabrir). O status `comentada` só vem de `comment_unrequested`.
- `comment_unrequested(ws, doc_id, version_n, n, type_, body, suggestion)`:
  1. leva `item.new` até a versão atual, re-ancorando passo a passo; se algum passo não terminar como `inalterado` ou `movido`, levanta `MargaError` ("o trecho mudou de novo na vK: comente direto na versão atual");
  2. no PDF avulso e no PowerPoint, define a página ou o slide;
  3. cria o comentário com `create_annotation`, que já exige a versão atual e o disco sem mudanças;
  4. marca o alerta como `comentada`, com o id do comentário.

## 7. Interface

**Comparar versões** (de a para b):

- Os passos verificados são as versões em (a, b] que têm rodada. Cada versão manual do intervalo ganha uma linha: "v3 foi registrada manualmente: não há verificação de alterações sem pedido."
- Uma métrica nova, **Sem pedido**, com os alertas pendentes.
- A seção **⚠️ Alterações sem pedido** vem antes de "Comentários: antes × depois":
  - sem alertas: "✅ Nenhuma alteração fora das linhas comentadas na rodada R.";
  - com dois ou mais pendentes: "✓ Aceitar os N pendentes";
  - um cartão por alerta pendente: ícone (⚠️ sem pedido, 🛡️ protegido, 📄 arquivo), "vN+1 · alerta n", `arquivo:linhas` e o motivo; duas colunas (vN e vN+1) com as linhas do trecho e as palavras alteradas destacadas; os botões ✓ Aceitar, ↩️ Pedir para refazer, 🚫 Ignorar e 📍 Ir;
  - **Pedir para refazer** abre um popover com tipo, instrução e sugestão já preenchidos pelo rascunho, e o botão 💬 Criar comentário;
  - **📍 Ir** abre a Revisão no trecho, na vN+1;
  - os alertas decididos ficam num expander recolhido, "N decidido(s)", com o status ("aceita", "ignorada", "virou c9") e ↺ Reabrir.
- No **Diff por arquivo**, quando a → b é um passo só, as linhas dos alertas pendentes ganham uma borda âmbar.

**Barra lateral:** o aviso do registro (botão Registrar vN+1 e `.zip` como nova versão) mostra o resumo com a contagem.

## 8. Pacote do agente e documentação

- `RULES`: a regra "Mude só o necessário…" ganha "O Marga aponta ao usuário toda alteração fora das linhas dos comentários e dos trechos ligados por links." O `AGENT_GUIDE` ganha uma frase equivalente. Os formatos `marga.bundle/v1` e `marga.response/v1` não mudam.
- README: o recurso, na parte de comparar versões.
- AGENTS.md: o mapa (`core/unrequested.py`), a tabela de testes, a contagem esperada de testes e o passo 9 do tutorial.

## 9. Erros e casos-limite

- Uma anotação ou link da rodada excluído antes de uma verificação tardia deixa de cobrir a região, e a mudança pode virar alerta. No caminho normal, a verificação é gravada no registro, com tudo presente.
- Pedir para refazer com o disco alterado, ou com o trecho mudado de novo numa versão posterior: `MargaError` com a explicação, mostrado com `st.error`.
- Arquivo removido: Pedir para refazer fica desabilitado, com uma dica para comentar em outro trecho.
- Se o comentário criado for excluído depois, o alerta continua `comentada`; ↺ Reabrir o devolve a pendente.
- Um bloco enorme (mais de 3000 tokens num lado) vira um alerta só, sem destacar palavras.

## 10. Testes

`tests/test_unrequested.py`:

- **Detecção pura:**
  - mudança numa linha pedida não gera alerta; numa linha sem comentário, gera, com o motivo "fora dos comentários";
  - um parágrafo só reformatado não gera alerta;
  - um bloco com uma linha pedida e outra não aponta só a outra;
  - uma inserção encostada num *inserir* (com linha em branco no meio) é pedida, e uma inserção longe vira alerta;
  - um trecho *não alterar* dentro de uma linha pedida vira alerta `protegido`;
  - os motivos de pergunta, nota e marca;
  - mudanças vizinhas viram um alerta, e mudanças distantes, dois;
  - arquivo novo; arquivo removido coberto e não coberto;
  - a extensão da remoção pura e os rascunhos de cada tipo.
- **Fluxo no workspace:**
  - rodada exportada → agente edita uma linha pedida e outra não → registro com `report.unrequested == 1` e o resumo com a contagem;
  - a verificação gravada é lida de novo sem recalcular, e é refeita quando o arquivo some (versões antigas);
  - versão manual: nenhum alerta, `check_version` devolve `None`;
  - links: a ponta de um *propagar* é pedida, a de um *ver também* não;
  - aceitar, ignorar e reabrir; status inválido;
  - pedir para refazer: o comentário nasce na versão atual com a sugestão, e o alerta fica `comentada`. Com uma versão nova no meio, o trecho é re-ancorado; se mudou de novo, ou se o disco está alterado, `MargaError`.

`tests/test_app.py`: a aba Comparar versões mostra a seção e os cartões, e ✓ Aceitar muda o status.

Navegador, numa cópia do demo: o fluxo do Passo 4 do AGENTS.md com o agente simulado fazendo uma mudança pedida e outra não; o aviso do registro; o cartão; Pedir para refazer criando o comentário; Aceitar, Ignorar e Reabrir; a borda no diff.

## 11. Fora do escopo

- Alertas dentro do texto, na aba Revisão (caixas no componente de fonte).
- Um campo opcional no `response.json` para o agente justificar mudanças extras.
- Verificar versões manuais.
- Recalcular verificações já gravadas.

## 12. Ordem de implementação

1. Modelos e `core/unrequested.py` (detecção pura), com os testes unitários.
2. Gravação, `check_version`, `register_version`, decisões e `comment_unrequested`, com os testes de fluxo.
3. Interface: cartões, popover, métrica e diff marcado, com o smoke test.
4. `RULES`, `AGENT_GUIDE`, README e AGENTS.md.
5. Verificação no navegador numa cópia do demo.
