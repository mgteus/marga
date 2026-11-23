# AGENTS.md · guia para agentes de IA no repositório do Marga

Este arquivo é para agentes (Claude Code, Codex, Cursor e afins) que vão **desenvolver o Marga**: instalar, rodar, testar e mudar o código. Leia antes de editar qualquer coisa.

> [!IMPORTANT]
> Não confunda com o `.marga/AGENT.md`. Esse outro arquivo é gerado pelo app dentro de cada workspace revisado e orienta o agente que **aplica uma rodada de revisão** nos documentos de alguém. Se foi isso que pediram, pule para [Aplicando uma rodada de revisão](#aplicando-uma-rodada-de-revisão).

## Em 30 segundos

O Marga é um webapp local em Streamlit para revisar projetos LaTeX, arquivos de texto e PDFs: o usuário comenta trechos, exporta uma *rodada* (`bundle.md/json/html`) para um agente, o agente edita os arquivos e o Marga registra a versão n+1, re-ancora os comentários e mostra o diff.

- **Stack:** Python ≥ 3.11 (o ambiente local usa 3.13) · Streamlit 1.67 com componentes v2 · PyMuPDF · TeX Live (`latexmk` + `synctex`), opcional · [uv](https://docs.astral.sh/uv/).
- **Idioma:** interface, pacote para o agente, mensagens de erro, docstrings, comentários e commits em português; identificadores em inglês.
- **Design aprovado:** [`docs/superpowers/specs/2026-11-22-marga-design.md`](docs/superpowers/specs/2026-11-22-marga-design.md). O mapa abaixo reflete o código atual, que difere da spec em alguns nomes de arquivo.

| Tarefa | Comando |
|---|---|
| Instalar ou atualizar dependências | `uv sync` |
| Rodar os testes | `uv run pytest -q` |
| Rodar um teste só | `uv run pytest tests/test_export.py -k response -q` |
| Subir o servidor | `uv run marga <pasta> --server.port 8501 --server.headless true --browser.gatherUsageStats false` |
| Ver se o servidor está de pé | `curl -fsS http://localhost:8501/_stcore/health` (responde `ok`) |
| Adicionar uma dependência | `uv add <pacote>` (ou `uv add --dev <pacote>`) |

## Mapa do repositório

```text
marga/
├── cli.py              `marga [pasta] [opções do streamlit]` → `streamlit run marga/app.py -- --workspace <pasta>`
├── app.py              entrada do Streamlit: usa --workspace, senão $MARGA_WORKSPACE, senão a pasta atual
├── core/               lógica pura, sem Streamlit
│   ├── models.py       dataclasses + JSON; tipos de comentário, relações de link, status
│   ├── workspace.py    .marga/, IDs (A, c1, m1, L1), JSON atômico, MargaError
│   ├── versions.py     blobs sha256, snapshots, mudanças no disco (TEXT_EXTS, SKIP_DIRS)
│   ├── textmap.py      offset ↔ (linha, coluna), texto LaTeX normalizado, busca de frase
│   ├── anchors.py      âncoras a partir de seleções; re-ancoragem n → n+1
│   ├── diffing.py      diff para as views, diff unificado, estatísticas
│   ├── latex.py        .tex principal, motor, latexmk em paralelo, leitura do log
│   ├── synctex.py      CLI synctex: PDF → fonte e fonte → PDF
│   ├── pdf.py          render de página, palavras numa região, texto de PDF avulso
│   ├── outline.py      seções: fonte LaTeX + SyncTeX, marcadores do PDF ou títulos pela tipografia
│   ├── zipimport.py    extração segura de .zip (pacotes do Overleaf)
│   ├── actions.py      operações de alto nível (o que a UI chama e os testes exercitam)
│   └── export.py       rodada (bundle JSON/MD/HTML), RULES, AGENT_GUIDE, importação da resposta
└── ui/
    ├── main.py         layout: barra lateral + abas Revisão, Links, Comparar versões, Rodadas
    ├── sidebar.py      workspace, documentos (fragmento que se atualiza a cada 4 s), adicionar, ajustes
    ├── review.py       aba Revisão: duas colunas, seleção, formulário, lista de anotações, links
    ├── pages.py        abas Links, Comparar versões e Rodadas
    ├── render.py       utilitários de apresentação (texto curto, diffs em HTML, grafo)
    ├── state.py        session_state: seleções, foco, toasts, mudanças adiadas de widgets
    ├── components.py   registra os componentes v2 (lê o JS/CSS na importação)
    └── frontend/       source_view.{js,css} e pdf_view.{js,css}: JS puro, sem npm nem build
tests/                  pytest; as fixtures copiam o demo para tmp_path
examples/demo/          workspace de exemplo: artigo/, suplemento/, parecer.pdf, notas.md
docs/superpowers/specs/ design da v0.1
.claude/launch.json     configuração "marga" do preview do Claude Code (porta 8601, examples/demo)
```

## Instalação

### 1. Pré-requisitos

```bash
uv --version
```

```bash
command -v latexmk synctex pdflatex
```

- **uv é obrigatório.** Se faltar, instale com `curl -LsSf https://astral.sh/uv/install.sh | sh` ou `brew install uv`. O uv baixa um Python ≥ 3.11 se a máquina não tiver.
- **TeX Live é opcional.** Sem ele, o app funciona, mas não compila (a barra lateral mostra `❌ latexmk` e o botão **▶ Compilar visíveis** fica desabilitado), a seleção no PDF cai para a busca aproximada de texto e 4 testes são pulados. Para instalar: `brew install texlive` no macOS; no Debian/Ubuntu, `sudo apt install latexmk texlive-latex-extra texlive-lang-portuguese` (o demo usa `babel` com `brazilian`).
- **Não instale ferramentas de sistema (uv, TeX Live) sem pedir ao usuário.** Dentro do projeto, use só `uv`.

### 2. Dependências

```bash
uv sync
```

Cria `.venv/` e instala exatamente o que está no `uv.lock` (Streamlit 1.67.0, PyMuPDF 2.1 e, no grupo `dev`, pytest 9.2), além do próprio pacote, que expõe o comando `marga`.

- Não use `pip` e não edite o `uv.lock` à mão. Para mudar dependências, use `uv add`/`uv remove` e inclua `pyproject.toml` e `uv.lock` no mesmo commit.
- O `pyproject.toml` aceita `streamlit>=1.40`, mas o código usa `streamlit.components.v2` (a spec cita o 1.67). Rode sempre pelo ambiente do lock, com `uv run`.

### 3. Verificação

```bash
uv run pytest -q
```

Esperado na v0.1: `22 passed` em cerca de 7 s, com TeX Live. Os `DeprecationWarning` sobre `SwigPyPacked`/`swigvarlink` vêm do PyMuPDF e podem ser ignorados.

## Tutorial: rodando o servidor pela primeira vez

Passo a passo validado em 2026-11-23 (macOS, Streamlit 1.67.0, TeX Live do Homebrew), do repositório recém-instalado até a primeira rodada completa.

### Passo 0 · Escolha um workspace descartável

- `examples/demo/` é versionado, mas `examples/demo/.marga/` e `examples/demo/uploads/` são **estado local do usuário** (ignorados pelo git) e podem guardar documentos e revisões reais. Não apague, não "limpe" e não versione essas pastas.
- Para uma primeira execução limpa, copie o demo para uma pasta temporária:

```bash
DEMO="$(mktemp -d)/demo" && rsync -a --exclude .marga --exclude uploads examples/demo/ "$DEMO/" && echo "$DEMO"
```

- Não rode `uv run marga` sem pasta na raiz do repositório: a pasta atual vira workspace e ganha um `.marga/`.

### Passo 1 · Suba o servidor

```bash
uv run marga "$DEMO" --server.port 8501 --server.headless true --browser.gatherUsageStats false > "$(dirname "$DEMO")/marga.log" 2>&1 &
```

- O primeiro argumento que não começa com `-` é o workspace; todo o resto vai para o `streamlit run`.
- `--server.headless true` não abre o navegador e, mais importante, pula o prompt interativo de e-mail que o Streamlit mostra na primeira execução de uma máquina. Sem essa opção, o processo pode ficar parado esperando o stdin.
- `--browser.gatherUsageStats false` desliga a telemetria do Streamlit.
- O servidor não termina sozinho: rode-o em segundo plano (o `&` acima, ou o modo de background da sua ferramenta) e guarde o log.
- **Claude Code no app desktop:** prefira a ferramenta de preview com a configuração `marga` do `.claude/launch.json` (porta 8601). Ela abre `examples/demo`, que pode ter estado do usuário; para testes que criam comentários e rodadas, aponte uma configuração temporária para a cópia do Passo 0 e desfaça a mudança no `launch.json` ao terminar.

### Passo 2 · Espere ficar pronto

```bash
for i in $(seq 60); do curl -fsS http://localhost:8501/_stcore/health && break; sleep 1; done
```

Quando imprimir `ok`, o servidor está pronto (leva poucos segundos). O log mostra `You can now view your Streamlit app in your browser.` e a URL local. A dica `For better performance, install the Watchdog module` é inofensiva: não adicione o watchdog às dependências por causa dela. Se o `ok` nunca vier, leia o log e veja [Problemas comuns](#problemas-comuns).

### Passo 3 · Confira o que foi criado

```bash
cat "$DEMO/.marga/marga.json"
```

A pasta passada na linha de comando vira workspace automaticamente: o `marga.json` nasce com `"docs": []`. Se o usuário digitar outra pasta na barra lateral, o app pergunta antes (**Criar workspace aqui**).

### Passo 4 · A primeira rodada, no navegador

Abra <http://localhost:8501>. Cada linha abaixo foi conferida no app:

| # | Ação | O que deve aparecer |
|---|---|---|
| 1 | Abrir a página | Barra lateral com **Workspace (pasta)**, `✅ latexmk · ✅ synctex` e "Nenhum documento ainda."; no centro, "Adicione um documento na barra lateral…" |
| 2 | **➕ Adicionar documento** → **Pasta** | A lista *Encontrados no workspace* traz `artigo/ · LaTeX (main.tex)`, `suplemento/ · LaTeX (main.tex)`, `parecer.pdf · PDF avulso` e `notas.md · texto` (as outras origens são **Enviar** e **Disco**) |
| 3 | Escolher `artigo/` e clicar em **Adicionar artigo** | Toast "A · artigo adicionado · LaTeX · v1". Com um documento só, as colunas mostram A-Fonte e A-PDF, e o PDF compila sozinho em 1 a 2 s: "✅ PDF da v1 · pdflatex · …" |
| 4 | Clicar no número da linha 15 do fonte | Caixa "Seleção em A · artigo/main.tex:15 · v1" com **Tipo**, **Instrução para o agente** e **💬 Salvar comentário** |
| 5 | Escrever um pedido e salvar | Toast "c1 criado em artigo/main.tex:15"; o comentário aparece em *Anotações de A* |
| 6 | Aba **Rodadas** → **📦 Exportar rodada 1** | "Rodada 1 · … · ⏳ aguardando o agente", o prompt para o agente e os arquivos em `.marga/rounds/001/` |
| 7 | Simular o agente (comando abaixo) | Em até 4 s, a barra lateral mostra "⚠️ 1 arquivo(s) mudaram no disco desde a v1" e o botão **Registrar v2** |
| 8 | **Registrar v2** | Toast "A v2 registrada · resposta da rodada 1 · 1 alterado · 1 nota(s) do agente importada(s)"; em *Rodadas*, "✅ resposta importada" |
| 9 | Aba **Comparar versões** (A, v1 → v2) | c1 antes × depois, a nota do agente e o diff de `artigo/main.tex` |

Para simular o agente no passo 7:

```bash
python3 - "$DEMO" <<'EOF'
import json, pathlib, sys
ws = pathlib.Path(sys.argv[1])
tex = ws / "artigo/main.tex"
tex.write_text(tex.read_text(encoding="utf-8").replace("entre 2019 e 2021.", "entre março e junho de 2021."), encoding="utf-8")
(ws / ".marga/rounds/001/response.json").write_text(json.dumps({
    "schema": "marga.response/v1", "round": 1, "resumo": "Ajustei o período no resumo.",
    "comments": {"c1": {"status": "feito", "nota": "troquei 'entre 2019 e 2021' por 'entre março e junho de 2021'"}},
}, ensure_ascii=False, indent=2), encoding="utf-8")
EOF
```

> [!TIP]
> Ao automatizar o navegador: a seleção por número de linha é um clique simples em `.mg-row[data-line="N"] .mg-no` (o componente não usa iframe). Depois de adicionar um documento, a caixa *Encontrados no workspace* pode continuar mostrando o rótulo do documento recém-adicionado, embora as opções já tenham sido atualizadas: abra a lista e escolha o item explicitamente.

### Passo 5 · O mesmo fluxo sem navegador

Para conferir uma mudança no núcleo, use a API direto (é o que os testes fazem):

```python
from marga.core import actions, export
from marga.core.anchors import lines_anchor
from marga.core.versions import latest_version, version_text
from marga.core.workspace import Workspace

ws = Workspace.open("/caminho/da/copia/demo")        # cria .marga/ se ainda não existir
doc = actions.add_document(ws, "artigo")             # vira o documento A (LaTeX) e ganha a v1
v1 = latest_version(ws, doc.id)
texto = version_text(ws, doc, v1, "artigo/main.tex")
ancora = lines_anchor(texto, "artigo/main.tex", v1.n, 15, 15)   # a linha 15 inteira
actions.create_annotation(ws, doc.id, ancora, "corrigir", "O período correto é de março a junho de 2021.")
info = export.export_round(ws, "Seja conciso.")
print(export.round_dir(ws, info.n) / "bundle.md")
```

Rode com `uv run python script.py`. Para a interface sem navegador, o smoke test renderiza todas as abas com o `AppTest` do Streamlit: `uv run pytest tests/test_app.py -q`.

### Passo 6 · Desligue e limpe

```bash
kill $(lsof -ti tcp:8501 -sTCP:LISTEN)
```

```bash
rm -rf "$(dirname "$DEMO")"
```

O `-sTCP:LISTEN` garante que só o servidor seja encerrado, e não um navegador conectado à porta. Em primeiro plano, basta Ctrl+C.

## Arquitetura e regras do código

- **O núcleo não importa Streamlit.** Nada em `marga/core/` importa `streamlit`; é isso que o deixa testável. A UI importa o núcleo, nunca o contrário.
- **Regra de negócio fica no núcleo.** Operação nova vai para `core/actions.py` (ou para o módulo certo do núcleo), com teste em pytest; a UI só chama e mostra.
- **Escrita em `.marga/` só pelos helpers.** Use `write_json`/`write_text`/`write_bytes` de `workspace.py` (arquivo temporário + rename) e faça ler-modificar-gravar dentro de `_LOCK`. JSON corrompido levanta `MargaError` e nunca é sobrescrito; mantenha assim.
- **Erros esperados são `MargaError`**, com mensagem em português pronta para o usuário. A UI captura e mostra com `st.error`.
- **O Marga nunca edita os documentos do usuário.** Ele só escreve em `.marga/` e cria arquivos novos em `uploads/` (envios, cópias do disco e `.zip` extraídos em `uploads/<nome>/`; nada sobrescreve: nomes repetidos ganham `-2`, `-3`…). Qualquer recurso que altere os arquivos do usuário é mudança de design: pergunte antes.
- **Coordenadas:** caminhos relativos ao workspace, em formato POSIX; linhas começam em 1; colunas começam em 0 e contam code points (não bytes, nem UTF-16).
- **Formatos são contratos.** `marga.bundle/v1`, `marga.response/v1` e `marga.project/v1` são lidos por outros agentes e por workspaces já existentes. Acrescentar chaves opcionais é seguro; renomear, remover ou mudar o sentido de uma chave exige subir a versão do schema e continuar lendo o formato antigo. `import_response` aceita apelidos (`comentarios`, `note`, `replacement`, `summary`): preserve-os. Ao mudar o pacote, atualize `RULES` e `AGENT_GUIDE` em `export.py`, os testes e o README.
- **LaTeX:** `latexmk` com `-interaction=nonstopmode -file-line-error -synctex=1`, timeout de 600 s (projetos do Overleaf com muitas figuras levam minutos) e um outdir por documento e versão (`.marga/build/<doc>/v<n>/out`). Nunca acrescente `-shell-escape`: os documentos são entrada não confiável.
- **Versionamento:** só extensões de `TEXT_EXTS` (em `versions.py`), até 5 MB por arquivo; as pastas de `SKIP_DIRS` são ignoradas.
- **Streamlit:**
  - Para mudar o valor de um widget por código, use `state.request(key, value)`: ele é aplicado no início da próxima execução por `apply_pending`. Atribuir a `st.session_state[key]` depois que o widget foi desenhado dá erro.
  - Chaves de widgets que precisam sobreviver à troca de aba devem casar com a regex `_KEEP` de `ui/state.py`, porque o Streamlit descarta o estado de widgets que não foram desenhados.
  - Eventos dos componentes trazem um `nonce`; use `state.fresh(nonce)` para tratar cada um uma vez só.
  - A lista de documentos da barra lateral é um `@st.fragment(run_every=4)`: é assim que mudanças no disco aparecem sem recarregar a página.
- **Frontend:** JS e CSS puros em `marga/ui/frontend/`, com a API de componentes v2 (`st.components.v2.component`, sem iframe, eventos via `setTriggerValue`). Sem npm, bundler ou CDN. Os arquivos são lidos na importação: depois de editá-los, reinicie o servidor.
- **Estilo:** `from __future__ import annotations`, type hints e dataclasses; linhas de até uns 120 caracteres. Não há linter configurado: siga o estilo do arquivo que estiver editando.

## Testes

Rode `uv run pytest -q` antes de dar qualquer mudança por concluída.

| Arquivo | Cobre |
|---|---|
| `tests/test_textmap.py` | offsets ↔ linha/coluna com Unicode, normalização de LaTeX, busca de frase exata e aproximada, linhas de diff |
| `tests/test_versions_anchors.py` | versões, arquivos latin-1, re-ancoragem (inalterado, deslocado, alterado, movido, órfão), links |
| `tests/test_export.py` | pacote da rodada, exportação bloqueada com disco alterado, importação da resposta, resposta inválida |
| `tests/test_latex_pdf.py` | compilação paralela, erro apontando a linha do subarquivo, versão antiga via snapshot, ida e volta do SyncTeX, texto de PDF avulso |
| `tests/test_zip_import.py` | `.zip` no formato do Overleaf (subpastas, figura, `.bib`, lixo do macOS), caminhos inseguros, nome digitado no envio, caminho colado do terminal, compilação com figuras e seções, figuras por hard link em versões antigas |
| `tests/test_outline.py` | títulos pela tipografia (folha de rosto, sumário e notas ficam de fora), marcadores, seções do fonte (`\include` comentado, `\section*`, apêndice) e páginas de cada seção |
| `tests/test_app.py` | smoke test: o `AppTest` renderiza todas as abas sem exceção (usa `MARGA_WORKSPACE`); lista de seções no PDF, filtro do texto do PDF e "ir para seção" no fonte |

- As fixtures `demo_ws` e `text_ws` (em `tests/conftest.py`) criam workspaces em `tmp_path`. Testes novos nunca devem escrever em `examples/demo/`.
- Sem `latexmk`/`synctex`, os testes de compilação e SyncTeX são pulados.
- Testes opcionais com arquivos reais: `MARGA_ZIP=/caminho/projeto.zip uv run pytest tests/test_zip_import.py -k real -s` e `MARGA_PDF=/caminho/doc.pdf uv run pytest tests/test_outline.py -k real -s`.
- Mudou a interface? Além do smoke test, suba o servidor (tutorial acima) e repita o fluxo do Passo 4 numa cópia do demo.

## Antes de entregar

- [ ] `uv run pytest -q` passa.
- [ ] Mudanças de interface foram vistas no navegador, numa cópia do demo.
- [ ] Mudou o pacote, a resposta, a CLI ou a instalação? README, este arquivo e `RULES`/`AGENT_GUIDE` foram atualizados.
- [ ] `git status` não mostra `.marga/`, `uploads/`, `.venv/` nem artefatos de LaTeX.
- [ ] Commit só quando o usuário pedir, com mensagem em português no presente, como no histórico ("Adiciona …", "Corrige …", "Implementa …").

## Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| `uv run marga` fica parado pedindo `Email:` | Prompt de primeira execução do Streamlit. Use `--server.headless true` (ou aperte Enter). |
| A porta já está em uso | Outro servidor ficou rodando. Ache-o com `lsof -ti tcp:8501 -sTCP:LISTEN` ou use outra porta (`--server.port 8502`). |
| `❌ latexmk` ou `❌ synctex` na barra lateral | TeX Live fora do `PATH` do servidor. Peça ao usuário para instalar e reinicie o servidor. |
| O demo não compila e o log cita `babel`/`brazilian` | Falta o pacote de idioma português do TeX Live (`texlive-lang-portuguese` no Debian/Ubuntu). |
| "Arquivo corrompido: … o Marga não vai sobrescrevê-lo." | JSON inválido em `.marga/`. Corrija ou remova esse arquivo, nunca os documentos do usuário. |
| Exportar ou comentar é bloqueado com "registre a vN+1" | Arquivos mudaram no disco desde a última versão. Clique em **Registrar vN+1** (ou chame `actions.register_version`). |
| "Nada mudou no disco desde a vN" | `register_version` foi chamado sem mudanças; é o comportamento esperado. |
| A mudança no JS/CSS não aparece | Os arquivos do frontend são lidos na importação. Reinicie o servidor. |
| `ModuleNotFoundError: No module named 'streamlit.components.v2'` | Streamlit antigo, fora do lock. Rode `uv sync` e use `uv run`. |
| Windows | O código usa chamadas POSIX (`os.killpg`). Use macOS, Linux ou WSL. |

## Aplicando uma rodada de revisão

Se pediram para você aplicar uma revisão feita no Marga (e não para mexer no código do Marga):

1. Leia `.marga/rounds/NNN/bundle.md` no workspace indicado (ou `.marga/rounds/latest.md`). O `bundle.json` tem os mesmos dados, estruturados.
2. Edite os arquivos no lugar, mudando só o necessário. Os números de linha valem para o que está no disco; se as linhas mudarem enquanto você edita, ache o trecho pela citação exata.
3. Respeite os tipos: `pergunta` se responde na nota, sem editar; `nao_alterar` protege o trecho; `nota` é só contexto. Siga também as relações dos links.
4. Mantenha o LaTeX compilável e aplique as sugestões literalmente.
5. Escreva `.marga/rounds/NNN/response.json` no formato da seção *Resposta* do pacote.
6. Não altere mais nada em `.marga/` e não registre versões: quem registra a vN+1 é o usuário, no app.
