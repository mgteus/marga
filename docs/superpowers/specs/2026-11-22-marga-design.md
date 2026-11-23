# Marga — design v1

- **Data:** 2026-11-22
- **Status:** proposta, aguardando aprovação
- **Stack:** Python 3.13 · Streamlit · PyMuPDF · TeX Live (latexmk + synctex) · uv

## 1. Objetivo

Marga é um webapp local para revisar documentos (principalmente projetos LaTeX) e transformar a revisão em instruções estruturadas para um agente LLM.

Você marca trechos no fonte ou direto no PDF, comenta como num review de pull request, liga trechos entre documentos (rastreio A → B) e exporta uma **rodada** em JSON, Markdown e HTML. O agente edita os arquivos; o Marga registra a versão n+1, re-ancora os comentários e mostra o diff n → n+1 para você revisar e abrir a próxima rodada.

Decisões já tomadas:

- Documento LaTeX = pasta com vários arquivos (`\input`/`\include`, `.bib`, figuras); arquivo único é caso particular.
- Seleção com o mouse no fonte e clique/arrasto no PDF (SyncTeX), via componentes leves, sem build de frontend.
- PDFs avulsos (sem fonte) também são revisáveis, a partir do texto extraído por página.
- Interface e pacote para o agente em português; código e identificadores em inglês.

## 2. Abordagens consideradas

| | Abordagem | Prós | Contras |
|---|---|---|---|
| **A (escolhida)** | Streamlit + 2 componentes HTML/JS sem build (fonte; página do PDF renderizada como imagem pelo PyMuPDF) + núcleo Python puro com armazenamento próprio em `.marga/` (snapshots por hash, estilo git) | Tudo local e em Python; JS mínimo e isolado; núcleo testável sem UI | PDF uma página por vez |
| B | Igual a A, mas PDF via pdf.js (scroll contínuo, seleção de texto nativa no PDF) | Mais fiel ao Overleaf | Bem mais JS, dependência de CDN, PDF inteiro trafega pelo componente |
| C | Versões como commits git | `git diff`/`log` de graça | Conflita com repositórios que já existam na pasta; não cobre PDF avulso |

B fica como evolução natural: o contrato de eventos do componente de PDF (clique/retângulo em pontos PDF) é o mesmo.

## 3. Conceitos

| Conceito | Definição |
|---|---|
| Workspace | Pasta aberta no Marga. Estado em `<workspace>/.marga/`. Caminhos sempre relativos a ela, que é o que o agente vê. |
| Documento | `A`, `B`, `C`… Tipos: `latex` (pasta + `.tex` principal), `text` (arquivo ou pasta de `.md`/`.txt`…), `pdf` (PDF avulso). |
| Versão | Snapshot imutável dos arquivos de texto do documento: v1, v2… Origem: `inicial`, `agente` (havia rodada exportada pendente), `manual`. |
| Âncora | Arquivo + versão + início/fim (linha 1-based, coluna 0-based em code points) + citação exata + prefixo/sufixo (até 64 caracteres) + página (PDF). |
| Anotação | Comentário `cN` (instrução ao agente) ou marca `mN` (trecho marcado, em geral alvo de link). |
| Link | `LN`: anotação → anotação, com relação e nota. Pode ligar documentos diferentes (A → B) ou trechos do mesmo documento. |
| Rodada | Exportação numerada em `.marga/rounds/NNN/` com tudo que o agente precisa, mais a resposta dele. |

- **Tipos de comentário:** corrigir, reescrever, remover, inserir, pergunta (responder sem editar), não alterar (proteger o trecho), nota (contexto), erro de compilação.
- **Sugestão (opcional):** texto substituto escrito por você; vira um diff `-`/`+` no pacote, como as suggestions do GitHub.
- **Relações de link:** `consistente` (os dois trechos devem dizer o mesmo), `propagar` (aplicar no destino a mesma mudança da origem), `origem` (o destino deriva da origem), `mover` (levar o conteúdo da origem para o destino), `contradiz`, `ver_tambem`.
- **Status da anotação:** aberto → resolvido | descartado.
- **Estado da região após nova versão:** inalterado, alterado, movido, órfão.

## 4. Arquitetura

```
marga/
  core/                 # sem Streamlit; testável isoladamente
    models.py           # dataclasses + (de)serialização JSON
    workspace.py        # abre/cria .marga, registro de documentos, IDs, JSON atômico
    versions.py         # blobs sha256, snapshots, mudanças no disco, registrar versão
    textmap.py          # offset ↔ (linha, coluna); texto LaTeX normalizado; busca de frase exata/aproximada
    anchors.py          # criar âncora; re-ancorar n → n+1; estado da região
    diffing.py          # linhas de diff para as views, diff unificado, estatísticas
    latex.py            # .tex principal, motor, latexmk, parse do log, compilação paralela
    synctex.py          # busca direta (linha → PDF) e inversa (PDF → linha) via CLI synctex
    pdf.py              # render de página, palavras numa região, extração de texto de PDF avulso
    export.py           # pacote da rodada (JSON/MD/HTML) e importação da resposta do agente
  ui/
    components.py       # wrappers Python dos componentes
    frontend/source_view/index.html   # fonte: seleção por mouse, diff, comentários inline
    frontend/pdf_view/index.html      # página do PDF: clique/arrasto, realces
    sidebar.py  pane.py  tabs.py      # layout Streamlit
  app.py                # entrada Streamlit
  cli.py                # `marga <workspace>`
tests/                  # pytest: núcleo, integração LaTeX/SyncTeX, smoke do app
examples/demo/          # workspace de exemplo: artigo/ (multi-arquivo), suplemento/, parecer.pdf
```

Os componentes usam o protocolo de custom components v1 do Streamlit (`declare_component(path=...)` + `postMessage`), escritos em JS puro, sem npm.

Armazenamento:

```
.marga/
  marga.json                 # documentos, contadores de ID, configurações
  objects/ab/abcdef…         # conteúdo dos arquivos, endereçado por sha256
  docs/A/versions.json       # [{n, criado_em, origem, nota, arquivos: {caminho: sha}}]
  annotations.json
  links.json
  AGENT.md                   # protocolo fixo para o agente
  rounds/003/bundle.json  bundle.md  bundle.html  response.json (escrito pelo agente)
  rounds/latest.json  latest.md
  build/A/work/  build/A/v3/ # saídas do latexmk (cópia de trabalho; snapshot materializado)
```

Dependências Python: `streamlit`, `pymupdf`; `pytest` para desenvolvimento. Ferramentas externas: `latexmk`, `pdflatex`/`xelatex`/`lualatex` e `synctex` (TeX Live, já instalados nesta máquina).

## 5. Interface

Layout largo com **duas colunas**. Cada coluna escolhe **documento · arquivo · versão · modo (Fonte | PDF)**. Combinações úteis: A-fonte + A-PDF (estilo Overleaf), A-fonte + B-fonte (para linkar), A-PDF v3 × A-PDF v4 (comparar versões).

- **Barra lateral:** workspace; documentos com aviso "alterado no disco → Registrar vN+1"; adicionar documento (pasta ou arquivo detectado no workspace, ou upload); ▶ Compilar visíveis.
- **Fonte (componente):** números de linha; quebra de linha (parágrafos LaTeX costumam ser uma linha só); diff contra a versão anterior (verde = novo/alterado, vermelho = removido); trechos anotados realçados; caixas de comentário entre as linhas, estilo GitHub.
  - Arrastar o mouse seleciona com precisão de caractere.
  - Clique no número da linha seleciona a linha; shift+clique estende.
  - Clique numa caixa foca o comentário na lista.
  - 🔎 Buscar frase: acha um trecho (exato ou aproximado, ignorando comandos LaTeX) e o seleciona.
- **PDF (componente):** uma página por vez (◀ ▶, zoom) com as anotações realçadas (posição via SyncTeX).
  - Clique leva à linha do fonte; arrastar um retângulo seleciona o trecho: o SyncTeX dá arquivo e linhas, e as palavras dentro do retângulo refinam a citação exata. Se a outra coluna mostra o fonte do mesmo documento, ela rola até a seleção.
  - Sem SyncTeX, ou em PDF avulso, o mapeamento usa a busca aproximada de frase.
- **Seleção → ação:** sob cada coluna, a seleção atual (ex.: `artigo/sections/intro.tex:42:10–43:5 "coletados em 2019"`) com [💬 Comentar] [🏷 Marcar]. O formulário tem tipo, texto e sugestão (pré-preenchida com a citação, com prévia do diff).
- **Linkar:** abaixo das duas colunas, [↔ Linkar] escolhe origem e destino entre as seleções atuais e as anotações existentes (uma seleção escolhida vira marca automaticamente), mais relação e nota.
- **Lista de anotações** sob cada coluna: filtros; resolver, reabrir, descartar, editar, excluir; 📍 ir para o trecho; histórico com as notas do agente por rodada.
- **Abas:** Revisão · Links (tabela + grafo dos rastreios) · Comparar versões · Rodadas (exportar, baixar, importar resposta).

## 6. Compilação paralela

- Comando: `latexmk -pdf|-pdfxe|-pdflua -f -interaction=nonstopmode -file-line-error -synctex=1 -outdir=<.marga/build/doc/alvo>`, com cwd na pasta do documento.
- Motor pelo magic comment `% !TEX program = …` (padrão pdflatex), ajustável por documento.
- Todos os documentos/versões visíveis compilam ao mesmo tempo (`ThreadPoolExecutor`), cada um no seu outdir: sem colisão de `.aux`, mesmo com A e B na mesma pasta.
- Versão antiga compila num snapshot materializado (cópia da pasta + arquivos daquela versão) e fica em cache, já que versões são imutáveis.
- Erros `arquivo:linha: mensagem` aparecem clicáveis (vão até a linha) e podem virar comentário "erro de compilação" para o agente.
- Timeout de 180 s por documento; sem `-shell-escape`.

## 7. Ciclo n → n+1

1. **Registro inicial:** ao adicionar um documento, o Marga registra v1. Só se comenta na versão mais recente e somente se o disco bate com ela; senão, primeiro "Registrar vN+1". Assim, as linhas do pacote são as linhas do disco.
2. **Exportar rodada:** grava `rounds/NNN/` e `latest.*` e mostra um prompt curto para o agente (ex.: Claude Code nesta pasta): "Leia `.marga/rounds/003/bundle.md`, aplique os comentários editando os arquivos no lugar e escreva `.marga/rounds/003/response.json`".
3. **Agente:** edita os arquivos e, opcionalmente, escreve `response.json`: `{"comments": {"c3": {"status": "feito|parcial|nao_feito|resposta", "nota": "…", "substituicao": "…"}}, "resumo": "…"}`.
4. **Registrar v(n+1):** o Marga detecta a mudança no disco; ao registrar, re-ancora as anotações abertas:
   - mapeia as linhas pelo diff e procura a citação perto da posição mapeada (prefixo/sufixo desempatam);
   - citação encontrada → inalterado; citação sumiu → alterado (âncora no bloco que a substituiu);
   - arquivo removido → busca a citação nos outros arquivos (movido) ou marca como órfão; anotações nunca são apagadas;
   - importa as notas do agente para o histórico de cada comentário.
5. **Comparar versões:** resumo (arquivos alterados, +/−, comentários alterados/inalterados/órfãos); diff por arquivo com os comentários no lugar; por comentário, antes × depois + nota do agente → ✓ resolver ou ↺ reabrir com nova instrução. Atalho para abrir os PDFs vn × vn+1 lado a lado. Novos comentários entram na rodada seguinte.

**PDF avulso:** a versão é o PDF + o texto extraído; nova versão = substituir o arquivo no disco e registrar. Como não há fonte, o agente devolve o texto corrigido em `substituicao`.

O Marga nunca altera seus arquivos. O upload só cria arquivos novos em `<workspace>/uploads/`.

## 8. Formato de saída

- **`bundle.json`**, a fonte da verdade (schema `marga.bundle/v1`): rodada; instruções gerais e protocolo; documentos (id, tipo, raiz, principal, versão atual e anterior, status da compilação); anotações (âncora completa, linhas com contexto, tipo, texto, sugestão, status, estado da região, histórico); links (origem, destino, relação, nota); mudanças desde a versão anterior (estatísticas + diff unificado); comentários resolvidos na rodada anterior (contexto).
- **`bundle.md`**, o prompt para o agente: regras; comentários agrupados por documento/arquivo com trecho numerado e citação exata; sugestões como diff; links dentro de cada comentário e uma seção "Rastreio entre arquivos" (tabela + diagrama mermaid); formato da resposta.
- **`bundle.html`**: relatório legível e autocontido com o mesmo conteúdo.

Trecho de exemplo do `bundle.md`:

    ### c3 · corrigir · artigo/sections/intro.tex:42–43 (A v3)
        42 | Os dados foram coletados em 2019
        43 | na região metropolitana de ...
    Trecho exato: "coletados em 2019"
    Pedido: o ano correto é 2021.
    Sugestão:
        - coletados em 2019
        + coletados em 2021
    Links: L1 → m5 (B · suplemento/main.tex:13) · consistente · "mesmo ano nos dois"

## 9. Erros e casos-limite

- `latexmk`/`synctex` ausentes: compilação desabilitada com aviso; a seleção pelo PDF cai para a busca aproximada.
- Falha de compilação: lista os erros e mantém o último PDF com selo "desatualizado".
- Exportar com alterações não registradas: bloqueado, com aviso apontando o documento.
- Codificação: UTF-8, com fallback para latin-1 registrado por arquivo.
- JSON corrompido em `.marga/`: não sobrescreve; mostra o erro. Escritas atômicas (arquivo temporário + rename).
- `response.json` inválido: aviso, e é ignorado.
- Arquivos grandes/binários: só extensões de texto são versionadas (`.tex .bib .sty .cls .md .txt`).

## 10. Testes

- **Unitários (pytest):** snapshots/blobs, detecção de mudanças, re-ancoragem (inalterado, deslocado, alterado, movido, órfão), busca de frase com marcação LaTeX, conversões linha/coluna, export (chaves do JSON; MD com ids, citações e links), importação da resposta.
- **Integração (puladas sem TeX):** compilar o exemplo multi-arquivo; compilar dois documentos em paralelo; SyncTeX ida e volta; extração de texto de PDF.
- **Smoke do app:** `streamlit.testing.v1.AppTest` abrindo o workspace de exemplo.
- **Verificação manual no navegador:** selecionar no fonte, clicar/arrastar no PDF, comentar, linkar A → B, exportar, simular a resposta do agente, registrar v2 e comparar.

## 11. Fora do escopo (v1)

Editor de texto no app · chamar a API do LLM direto (o pacote já fica pronto para isso) · multiusuário · PDF com scroll contínuo (pdf.js) · diff visual de PDF (a comparação é pelo texto, com os PDFs lado a lado) · integração com git.

## 12. Ordem de implementação

1. Núcleo: models, workspace, versions, textmap, anchors, diffing + testes.
2. LaTeX: compilação paralela, log, synctex, pdf + testes de integração.
3. Export e importação da rodada + testes.
4. Componentes de fonte e de PDF.
5. App Streamlit: barra lateral, colunas, seleção, comentários, links, abas.
6. Exemplo demo, smoke test e verificação no navegador.
