# 🖍️ marga

Revisão de documentos — projetos LaTeX, arquivos de texto e PDFs avulsos — para direcionar correções a um agente LLM.

Você marca trechos no fonte ou direto no PDF, comenta como num review de pull request, liga trechos entre documentos (rastreio A → B) e exporta uma **rodada** em JSON, Markdown e HTML. O agente edita os arquivos no lugar; o Marga registra a versão n+1, reposiciona os comentários e mostra o diff n → n+1 para a próxima rodada.

## Requisitos

- Python 3.11+ e [uv](https://docs.astral.sh/uv/)
- Para compilar LaTeX: TeX Live com `latexmk` e `synctex` (sem eles, o resto funciona e a seleção no PDF usa busca aproximada de texto)

## Como rodar

```bash
uv sync
uv run marga examples/demo
```

`marga <pasta>` abre o app com essa pasta como workspace (padrão: a pasta atual). Opções do Streamlit vão depois da pasta, por exemplo `uv run marga ~/papers/tese --server.port 8600`.

## Fluxo

1. **Adicione documentos** na barra lateral. O Marga encontra pastas LaTeX (o `.tex` com `\documentclass`), PDFs avulsos e arquivos `.md`/`.txt`. Cada documento ganha uma letra (A, B, C…) e a v1.
2. **Revise em duas colunas.** Cada coluna mostra um documento, uma versão e um modo (Fonte ou PDF): A-fonte ao lado de A-PDF (como no Overleaf), A ao lado de B, ou v3 ao lado de v4. ▶ *Compilar visíveis* compila os documentos LaTeX das duas colunas ao mesmo tempo.
3. **Selecione e comente.**
   - Fonte: arraste para selecionar (precisão de caractere) ou clique/arraste nos números de linha (shift estende).
   - PDF: clique leva à linha do fonte; arrastar um retângulo seleciona o trecho (SyncTeX + texto do PDF).
   - Tipos: corrigir, reescrever, remover, inserir, pergunta, não alterar, nota, erro de compilação. Opcionalmente, sugira o texto substituto (vira um diff no pacote).
4. **Ligue trechos entre arquivos** em *↔ Linkar trechos*: origem → relação (consistente, propagar, origem, mover, contradiz, ver também) → destino. Uma seleção usada num link vira uma marca (`mN`). A aba *Links* mostra o grafo.
5. **Exporte a rodada** na aba *Rodadas*. O Marga grava `.marga/rounds/NNN/bundle.{md,json,html}` e mostra um prompt curto para colar no agente (por exemplo, o Claude Code aberto na pasta do workspace).
6. **O agente edita os arquivos** e escreve `.marga/rounds/NNN/response.json` com uma nota por comentário.
7. **Registre a nova versão.** A barra lateral avisa quando os arquivos mudam no disco (*Registrar vN+1*). Os comentários abertos são reposicionados na versão nova (inalterado, alterado, movido ou órfão) e as notas do agente entram no histórico.
8. **Compare** na aba *Comparar versões*: antes × depois de cada comentário, notas do agente, diff por arquivo e atalho para ver os PDFs lado a lado. Resolva, descarte ou reabra com uma nova instrução, e exporte a próxima rodada.

O Marga não altera seus arquivos: quem edita é o agente. O upload só cria arquivos novos em `uploads/`.

## O que fica em `.marga/`

```
.marga/
  marga.json                   documentos, contadores, ajustes
  objects/                     conteúdo das versões, por sha256
  docs/A/versions.json         versões de cada documento
  annotations.json  links.json comentários, marcas e links
  rounds/NNN/                  bundle.md, bundle.json, bundle.html, response.json
  rounds/latest.{md,json}      a rodada mais recente
  AGENT.md                     guia fixo para agentes
  build/A/vN/                  saídas do latexmk (PDF, log, synctex)
```

## Pacote para o agente

- `bundle.json` (schema `marga.bundle/v1`): documentos, versões, status da compilação, anotações (âncora com linha/coluna, citação exata, trecho com contexto, pedido, sugestão, histórico), links e o diff desde a versão anterior.
- `bundle.md`: o mesmo conteúdo como prompt (regras, comentários por arquivo, diagrama mermaid dos links, formato da resposta).
- `bundle.html`: relatório para leitura humana.
- `response.json` (escrito pelo agente):

```json
{
  "round": 1,
  "resumo": "o que mudou",
  "comments": {
    "c1": {"status": "feito", "nota": "troquei 2019 por 2021"},
    "c2": {"status": "resposta", "nota": "resposta à pergunta"},
    "c3": {"status": "feito", "substituicao": "texto corrigido (PDF avulso)"}
  }
}
```

## Desenvolvimento

```bash
uv run pytest
```

- `marga/core/`: lógica sem Streamlit (workspace, versões, âncoras, diff, LaTeX, SyncTeX, PDF, exportação).
- `marga/ui/`: app Streamlit; os componentes de fonte e PDF ficam em `marga/ui/frontend/` (JS puro, API de componentes v2 do Streamlit, sem build). Depois de editar o JS, reinicie o servidor.
- Design: [`docs/superpowers/specs/2026-11-22-marga-design.md`](docs/superpowers/specs/2026-11-22-marga-design.md).

## Limitações conhecidas

- O PDF é mostrado uma página por vez.
- Só se comenta na versão mais recente, e só quando o disco bate com ela (registre a nova versão antes).
- Versões antigas compilam com as figuras atuais da pasta: só arquivos de texto (`.tex`, `.bib`, `.sty`, `.cls`, `.md`, `.txt`) são versionados.
- Não há editor de texto no app, nem chamada direta a uma API de LLM: o pacote é feito para um agente com acesso à pasta.
