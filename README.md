<div align="center">

# 🖍️ marga

**Revise documentos como quem revisa um pull request e entregue as correções a um agente de IA.**

Marque trechos no fonte LaTeX ou direto no PDF, comente, ligue trechos entre arquivos e exporte a rodada num pacote que o agente entende.<br>
O agente edita os arquivos; o Marga registra a nova versão, reposiciona os comentários e mostra o diff.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Streamlit](https://img.shields.io/badge/streamlit-1.67-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io)
[![uv](https://img.shields.io/badge/depend%C3%AAncias-uv-DE5FE9)](https://docs.astral.sh/uv/)
[![TeX Live](https://img.shields.io/badge/LaTeX-latexmk%20%2B%20SyncTeX-008080?logo=latex&logoColor=white)](https://tug.org/texlive/)
![versão 0.1.0](https://img.shields.io/badge/vers%C3%A3o-0.1.0-6E56CF)

[Começo rápido](#-começo-rápido) · [Como funciona](#-como-funciona) · [Pacote para o agente](#-o-pacote-para-o-agente) · [Desenvolvimento](#-desenvolvimento) · [Guia para agentes de IA](AGENTS.md)

</div>

---

## ✨ O que ele faz

Pedir correções a um agente pelo chat ("no segundo parágrafo da introdução, troque…") é impreciso e se perde de uma rodada para a outra. O Marga transforma a revisão num pacote estruturado, com citação exata, linha e coluna de cada pedido, e acompanha o que mudou depois que o agente mexeu nos arquivos.

| | |
|---|---|
| 🎯 **Comentários ancorados** | Selecione no fonte (precisão de caractere) ou no PDF (clique e retângulo, via SyncTeX). Cada comentário guarda arquivo, linhas, colunas e a citação exata. |
| 🪟 **Duas colunas** | Fonte × PDF (como no Overleaf), documento A × B ou v3 × v4, lado a lado. |
| 🔗 **Rastreio entre arquivos** | Ligue trechos de quaisquer documentos, LaTeX, PDF, Word, PowerPoint, HTML ou texto (consistente, propagar, mover, contradiz…), e veja o grafo dos links. |
| 📦 **Rodadas para o agente** | Um pacote em Markdown, JSON e HTML, mais um prompt curto para colar no Claude Code (ou em outro agente com acesso à pasta). |
| 🔄 **Versões e re-ancoragem** | Snapshots imutáveis por sha256. Na versão nova, cada comentário fica *inalterado*, *alterado*, *movido* ou *órfão*. |
| ⚡ **Compilação em segundo plano** | Cada versão nova compila sozinha ao ser registrada. Em segundos aparece uma prévia sem figuras, que o PDF completo substitui quando fica pronto. Um erro de compilação vira comentário com um clique. |
| 📕 **PDFs avulsos** | PDFs sem fonte também são revisáveis, a partir do texto extraído de cada página. |
| 📘 **Word, PowerPoint e HTML** | `.docx`, `.pptx` e `.html` viram documentos revisáveis: o Marga extrai o texto (parágrafos, títulos, tópicos de cada slide, tabelas) e compara as versões por ele. Quem edita o próprio arquivo é o agente. |
| 📙 **Slides do PowerPoint** | Além do texto, o PowerPoint aparece desenhado slide a slide, sem converter nada (modo **Slides**), ou como PDF exportado pelo próprio PowerPoint (no Mac) ou pelo LibreOffice (modo **PDF**). Selecione e comente direto no slide. |
| 📦 **Projetos do Overleaf** | O `.zip` baixado do Overleaf vira um documento LaTeX completo: vários `.tex`, figuras e bibliografia. Um `.zip` novo do mesmo projeto vira a próxima versão do documento. |
| 🧭 **Seções** | Em PDFs grandes, uma lista de seções leva direto a cada capítulo e filtra o texto. Ela vem do fonte LaTeX, dos marcadores do PDF ou de títulos detectados pela tipografia. |
| 🔒 **Local e não destrutivo** | Quem edita é o agente. O Marga só escreve em `.marga/` e em `uploads/`, onde guarda as cópias dos arquivos enviados; seus arquivos de fora de `uploads/` nunca são sobrescritos. |

## 🚀 Começo rápido

**Requisitos**

| Ferramenta | Para quê | Obrigatório? |
|---|---|---|
| [uv](https://docs.astral.sh/uv/) | Instala as dependências e roda o app | Sim |
| Python 3.11+ | Runtime | Sim (se faltar, o uv baixa um) |
| TeX Live: `latexmk`, `synctex` e `pdflatex`/`xelatex`/`lualatex` | Compilar LaTeX e ligar o clique no PDF à linha do fonte | Não. Sem ele, o resto funciona e a seleção no PDF usa busca aproximada de texto |
| Microsoft PowerPoint (Mac) | PDF dos slides exatamente como no PowerPoint (modo **PDF**); é o conversor padrão quando está instalado | Não. Sem ele, o modo PDF usa o LibreOffice |
| LibreOffice | PDF dos slides sem o PowerPoint (Linux, ou se você escolher) | Não. Sem nenhum dos dois, o PowerPoint aparece no modo **Slides**, desenhado sem conversão, e no texto |

<details>
<summary>Como instalar o uv e o TeX Live</summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # ou: brew install uv
```

```bash
brew install texlive   # macOS
```

```bash
sudo apt install latexmk texlive-latex-extra texlive-lang-portuguese   # Debian/Ubuntu
```

O exemplo usa `babel` com `brazilian`, por isso o pacote de idioma português.

```bash
brew install --cask libreoffice   # macOS: PowerPoint como PDF
```

```bash
sudo apt install libreoffice-impress   # Debian/Ubuntu
```

</details>

**Instale e abra o exemplo**

```bash
git clone https://github.com/mgteus/marga.git
cd marga
uv sync
uv run marga examples/demo
```

O app abre em <http://localhost:8501>. Na barra lateral, vá em **➕ Adicionar documento → Pasta**, escolha `artigo/` e clique em **Adicionar artigo**: o fonte aparece ao lado do PDF, já compilado. Clique num número de linha, escreva um pedido e salve: esse é o seu primeiro comentário.

> [!TIP]
> Na primeira execução, o Streamlit pode pedir um e-mail no terminal. É opcional: aperte Enter.

**Abrindo outras pastas**

```text
marga [pasta] [opções do Streamlit]
```

| Comando | O que faz |
|---|---|
| `uv run marga` | Abre a pasta atual como workspace |
| `uv run marga ~/papers/tese` | Abre outra pasta (cria `~/papers/tese/.marga/` na primeira vez) |
| `uv run marga ~/papers/tese --server.port 8600` | Usa outra porta (as opções do Streamlit vão depois da pasta) |
| `uv run marga ~/papers/tese --server.headless true` | Não abre o navegador (útil em servidores e para agentes) |
| `uv run --project ~/marga marga .` | Roda de qualquer lugar, usando o ambiente do repositório |

Dá para trocar de workspace depois, pela barra lateral.

> [!NOTE]
> Vai usar um agente de IA para mexer *neste repositório*? O [AGENTS.md](AGENTS.md) explica a instalação, o primeiro servidor passo a passo, a arquitetura e as convenções.

## 🔁 Como funciona

```mermaid
flowchart LR
    R["👀 Você revisa<br/>comenta, marca e liga trechos"] --> E["📦 Exporta a rodada<br/>bundle.md · .json · .html"]
    E --> G["🤖 O agente edita os arquivos<br/>e escreve response.json"]
    G --> V["🔄 O Marga registra vN+1<br/>e re-ancora os comentários"]
    V --> C["🔍 Você compara n → n+1<br/>resolve ou reabre"]
    C --> R
```

1. **Adicione documentos** na barra lateral, em **➕ Adicionar documento**. Cada documento ganha uma letra (A, B, C…) e a v1, e o nome digitado vira o nome do documento. Para trocar de workspace, digite o caminho no topo da barra lateral, abra o seletor do sistema em **📁 Finder** ou volte a um dos **🕘 Recentes**.

   | Origem | O que aceita |
   |---|---|
   | **Enviar** | Um arquivo do computador: `.zip` do Overleaf, `.pdf`, `.tex`, `.docx`, `.pptx`, `.html`, `.md` ou `.txt` |
   | **Pasta** | O que já está na pasta: pastas LaTeX (o `.tex` com `\documentclass`), PDFs avulsos, Word (`.docx`), PowerPoint (`.pptx`), HTML (`.html`, `.htm`) e textos (`.md`, `.txt`, `.rst`) |
   | **Disco** | O caminho de um arquivo (ex.: `~/Downloads/projeto.zip`), sem passar pelo navegador. Melhor para arquivos grandes |
2. **Revise em duas colunas.** Cada coluna mostra um documento, uma versão e um modo (Fonte ou PDF). Cada versão LaTeX compila sozinha, em segundo plano, assim que é registrada, ou na primeira vez que você a abre no modo PDF. Enquanto as figuras não ficam prontas, o PDF aparece como uma prévia sem figuras: cada figura é uma moldura do mesmo tamanho, e o layout é o mesmo. Na prévia, você já pode clicar, selecionar e comentar. O PDF completo entra no lugar sozinho, na mesma página. **▶ Compilar visíveis** recompila do zero as versões LaTeX das duas colunas. Word, HTML e textos têm só o modo Fonte, com o texto do arquivo. O PowerPoint tem três: **Fonte** (o texto), **Slides** (desenhado sem conversão) e **PDF** (exportado pelo PowerPoint ou convertido pelo LibreOffice). Com o PowerPoint instalado, ele abre no PDF, que é idêntico ao slide.
   - **Coluna da direita:** desligue o interruptor na barra de cima para revisar um documento só. Ele fica na metade esquerda, do mesmo tamanho de antes, e a seleção e as anotações dele ficam na metade direita, sempre à vista enquanto a página rola. Ao ligar de novo, a direita volta com o mesmo documento, modo e versão (e **🖼️ Abrir lado a lado**, em *Comparar versões*, religa sozinho).
   - **Tamanho do PDF:** ao lado do número da página. **Largura** ocupa a coluna (o padrão); **Página** mostra a página inteira na altura da tela; as porcentagens são do tamanho real e, quando a página passa da coluna, ela ganha rolagem própria. O zoom do navegador não muda o tamanho do PDF, porque ele sempre acompanha a coluna.
   - **§ Seções:** no modo PDF, a lista ao lado do número da página acompanha a página atual e salta para qualquer capítulo ou seção. Digite parte do título para filtrar.
   - No texto de um PDF avulso, a mesma lista filtra o trecho mostrado. Acima de 120 páginas, o texto aparece por seção ou em blocos de 20 páginas.
   - No fonte LaTeX, **§ Ir para seção** abre o arquivo na linha do título e leva junto o PDF da outra coluna. No Word e no HTML, a mesma lista vai para cada título; no PowerPoint, **§ Ir para slide** vai para cada slide.
   - As seções vêm do fonte LaTeX (posicionado pelo SyncTeX), dos marcadores do PDF ou, em PDFs sem marcadores, de títulos detectados pela tipografia.
   - **Anotações de cada documento**, sob a coluna dele: **Abertas · Todas** filtra a lista, e **No texto · Por id** a ordena pela posição dos trechos nos arquivos (o padrão) ou pelo número (c1, c2… e depois as marcas).
3. **Selecione e comente.**
   - Fonte: arraste para selecionar, ou clique/arraste nos números de linha (shift estende).
   - PDF: o clique leva à linha do fonte; arrastar um retângulo seleciona o trecho (SyncTeX + texto do PDF). Dentro de uma figura, a seleção vai para o `\includegraphics` dela, e as marcas ficam sempre dentro da página.
   - Se quiser, sugira o texto substituto: ele vira um diff no pacote, como as *suggestions* do GitHub.
4. **Ligue trechos entre arquivos** em **↔ Linkar trechos**: origem → relação → destino. Uma seleção usada num link vira uma marca (`mN`), e a aba *Links* mostra o grafo.
5. **Exporte a rodada** na aba *Rodadas*. O Marga grava `.marga/rounds/NNN/` e mostra um prompt curto para colar no agente.
6. **O agente edita os arquivos** direto no disco, nos caminhos da seção *Onde editar* do pacote, e escreve `.marga/rounds/NNN/response.json`, com uma nota por comentário. Num projeto do Overleaf, isso é a cópia em `uploads/`; se a revisão foi aplicada no próprio Overleaf, traga o `.zip` novo como nova versão (veja abaixo).
7. **Registre a nova versão.** A barra lateral avisa quando os arquivos mudam no disco (**Registrar vN+1**). Os comentários abertos são reposicionados, as notas do agente entram no histórico e o aviso diz quantas alterações sem pedido o agente fez.
8. **Compare** na aba *Comparar versões*: alterações sem pedido, antes × depois de cada comentário, nota do agente e diff por arquivo. Resolva, descarte ou reabra com uma nova instrução, e exporte a próxima rodada.
   - **⚠️ Alterações sem pedido:** o Marga aponta o que o agente mudou fora das linhas dos comentários da rodada e dos trechos ligados por links: uma alucinação, uma frase reescrita sem ninguém pedir, um trecho protegido por *não alterar*. Cada alerta mostra a versão anterior e a nova com as palavras alteradas destacadas, e diz o motivo.
     - **✓ Aceitar** mantém a alteração.
     - **↩️ Pedir para refazer** cria um comentário na versão atual, que entra na próxima rodada. Ele já vem preenchido para voltar ao texto anterior, que vai como sugestão, e você pode editá-lo antes de salvar.
     - **🚫 Ignorar** fecha o alerta (só formatação, um falso positivo), e **↺ Reabrir** desfaz qualquer decisão.
     - A linha inteira de um comentário conta como pedida; reformatar um parágrafo não gera alerta; só as versões que respondem a uma rodada são verificadas. No *Diff por arquivo*, as linhas com alerta pendente ganham uma borda âmbar.
   - Para ver o documento inteiro, use **🖼️ Abrir lado a lado na Revisão**, com uma versão em cada coluna. Ali, o **📍 Ir** de um comentário mantém as duas versões e leva cada coluna ao trecho dele naquela versão. Um comentário criado na versão nova aparece na antiga no mesmo trecho, se ele existir lá.

<details>
<summary>📦 Projetos do Overleaf (.zip)</summary>

1. No Overleaf, use **Menu → Download → Source** para baixar um `.zip` com os `.tex`, a bibliografia e as figuras.
2. No Marga, envie o `.zip` em **Enviar** ou cole o caminho dele em **Disco**. O projeto é extraído em `uploads/<nome do zip>/`, sem o lixo do macOS, e vira um documento LaTeX. O principal é o `main.tex` (ou outro `.tex` com `\documentclass`).
3. O motor padrão é o pdflatex. O Marga usa xelatex quando o projeto carrega `fontspec`/`polyglossia` ou tem `% !TEX program`. Se no Overleaf o projeto usa outro compilador, troque o **Motor** em **⚙️ Ajustes de documento**.
4. Se faltar um pacote no TeX Live local, o log mostra o arquivo e o comando para descobrir qual pacote instalar (`tlmgr search --global --file /arquivo.sty`). Projetos com biblatex precisam do `biber`.
5. **Revisão aplicada no Overleaf?** Baixe o projeto de novo e, em **Enviar** ou **Disco**, escolha **Adicionar como → Nova versão de A**. Os arquivos do `.zip` substituem a cópia em `uploads/`, e os textos que não vierem nele saem da cópia. Figuras que sumiram ficam, para as versões antigas continuarem compilando, e a v anterior continua guardada. A versão nova responde à rodada pendente, como quando o agente edita o disco.

Projetos com muitas figuras compilam em três etapas. As passadas que acertam referências, sumário e bibliografia pulam as figuras, e só a última as embute, comprimidas no nível 1. Isso deixa o PDF de trabalho uns 25% maior, sem perda. Uma dissertação com cerca de 350 MB de imagens passou de ~81 s para ~16 s, com a prévia sem figuras aos ~4 s. O limite é de 10 minutos. O `marga` aceita envios de até 2 GB e as versões antigas reaproveitam as figuras por hard link, sem copiá-las.

</details>

<details>
<summary>📘 Word, PowerPoint e HTML</summary>

Cada `.docx`, `.pptx` ou `.html` é um documento próprio (B, C…). O Marga extrai dele um texto em linhas, parecido com Markdown, e é nesse texto que você seleciona, comenta e cria links com os outros documentos:

| No texto extraído | Vem de |
|---|---|
| Um parágrafo por linha | Parágrafos do Word e blocos do HTML (`<p>`, `<div>`, `<li>`…) |
| `# Título`, `## Subtítulo` | Estilos de título do Word (ou o nível de tópico), `<h1>`–`<h6>` e o título de cada slide |
| `- item`, com dois espaços de recuo por nível | Listas do Word e do HTML, tópicos dos slides |
| `\| célula \| célula \|` | Uma linha por linha de tabela |
| `=== slide 3 ===` e `Notas do apresentador:` | O começo de cada slide (na ordem da apresentação; slides ocultos levam `· oculto`) e as notas dele |
| `[^1]` e `[^1]: …` no fim | Notas de rodapé do Word |

- Com o controle de alterações ligado, vale o texto como se todas as revisões fossem aceitas. Caixas de texto viram linhas logo depois do parágrafo em que estão.
- O texto extraído é guardado com cada versão. O diff, a re-ancoragem e a aba *Comparar versões* usam esse texto; as seleções no PowerPoint também guardam o slide.
- PowerPoint em **Slides**: o Marga desenha cada slide direto do `.pptx` (tema, layout, mestre, formas, imagens, tabelas), sem converter nada. Arraste sobre o texto para selecionar um trecho, ou clique num parágrafo para pegá-lo inteiro; as notas do apresentador aparecem embaixo. É um desenho aproximado: as fontes são as instaladas no Mac (as embutidas no `.pptx` o navegador não lê; instale as do deck para chegar perto), e gráficos e SmartArt viram uma caixa.
- PowerPoint em **PDF**: cada versão é convertida uma vez e o PDF fica guardado; a página N é o slide N, inclusive com slides ocultos. Clique num parágrafo ou arraste um retângulo para selecionar, como num PDF avulso.
  - O PDF é só a vista: cada palavra da página é ligada à palavra do texto extraído de onde veio (o PowerPoint e o LibreOffice escrevem o texto na ordem das formas). A seleção cai na forma e no parágrafo exatos do `.pptx`, mesmo quando a frase se repete no slide, e a marca de um comentário só aparece nas palavras dele. Rodapé, número do slide e textos do layout não são do slide no `.pptx` e não se selecionam.
  - A caixa da seleção e cada anotação mostram onde o trecho fica no arquivo (📍 *No .pptx*: slide, forma, parágrafo e caracteres): é o que o agente recebe para editar.
  - No Mac com o PowerPoint, é ele quem exporta o PDF, que sai idêntico ao slide. O Marga deixa uma cópia da versão em `~/Library/Application Support/marga/powerpoint/`, o PowerPoint abre essa cópia por um instante e a fecha. Na primeira vez, o macOS pergunta se o app que roda o Marga (Terminal, Claude…) pode controlar o PowerPoint, e o PowerPoint pede acesso a essa pasta: clique em **Select…** e conceda (vale para todos os workspaces).
  - Sem o PowerPoint, o LibreOffice converte em segundo plano; fontes e efeitos que ele não tem ficam diferentes. Em **⚙️ Ajustes de documento**, *PDF dos slides feito com* escolhe o conversor de cada apresentação.
- No pacote, o agente recebe o caminho do arquivo e como editá-lo: o próprio `.docx`/`.pptx` (por exemplo, com python-docx ou python-pptx), preservando a formatação, e o código-fonte do `.html`. Como as linhas do pacote são as do texto extraído, ele localiza cada trecho pela citação exata; no HTML, cada comentário também traz as linhas do arquivo.
- No PowerPoint, cada comentário diz onde editar no `.pptx`, mesmo que tenha sido feito no PDF: o slide, a forma (`shape_id` e nome, e os grupos em volta), o índice do parágrafo e os caracteres no texto dele, como o python-pptx os conta (`paragraph.text`), mais a célula nas tabelas e as notas do apresentador. O `bundle.json` traz isso em `pptx`, e o `.marga/AGENT.md`, um exemplo com python-pptx.
- Se o arquivo mudar no disco (o agente salvou, ou você editou no Word), a barra lateral oferece **Registrar vN+1**, como nos outros tipos.

</details>

<details>
<summary>Tipos de comentário e relações de link</summary>

| Tipo | O que o agente deve fazer |
|---|---|
| `corrigir`, `reescrever`, `remover`, `inserir` | Editar o trecho |
| `pergunta` | Responder na nota, sem editar |
| `nao_alterar` | Não mexer no trecho |
| `nota` | Usar como contexto |
| `erro_compilacao` | Corrigir para o documento compilar |
| `marca` (`mN`) | Só marca um trecho, em geral para servir de ponta de um link |

| Relação | Significado |
|---|---|
| `consistente` | No fim, os dois trechos dizem a mesma coisa |
| `propagar` | Aplicar no destino a mesma mudança feita na origem |
| `origem` | O destino deriva da origem; revisar se a origem mudar |
| `mover` | Levar o conteúdo da origem para o destino |
| `contradiz` | Resolver a contradição |
| `ver_tambem` | Só contexto |

</details>

## 📦 O pacote para o agente

Cada rodada fica em `.marga/rounds/NNN/`, e a mais recente também em `.marga/rounds/latest.{md,json}`.

| Arquivo | Para quem | Conteúdo |
|---|---|---|
| `bundle.md` | Agente | O prompt: onde editar (o caminho completo de cada documento e, em Word, PowerPoint e HTML, como editá-lo), regras, comentários por arquivo com trecho numerado e citação exata (mais o slide e a forma, o parágrafo e os caracteres no `.pptx`, ou as linhas do arquivo HTML), sugestões como diff, links (com diagrama mermaid) e o formato da resposta |
| `bundle.json` | Agente / scripts | Os mesmos dados estruturados (schema `marga.bundle/v1`), incluindo o diff desde a versão anterior |
| `bundle.html` | Você | Relatório para leitura humana, autocontido |
| `response.json` | Escrito pelo agente | Uma nota por comentário (schema `marga.response/v1`) |

O prompt que o Marga mostra é parecido com:

```text
Estou revisando os documentos do workspace /caminho/da/pasta com o Marga. Leia .marga/rounds/001/bundle.md e
aplique os comentários seguindo as regras do pacote.
Edite os arquivos direto no disco, nestes caminhos:
- A · artigo: /caminho/da/pasta/artigo
- B · Tese: /caminho/da/pasta/uploads/Tese
O Marga só enxerga mudanças nesses caminhos: não gere .zip, não edite outra cópia do projeto (Overleaf,
Downloads, um clone) e não renomeie arquivos.
Ao terminar, escreva .marga/rounds/001/response.json no formato descrito na seção Resposta do pacote.
```

E a resposta do agente:

```json
{
  "schema": "marga.response/v1",
  "round": 1,
  "resumo": "o que mudou, em poucas linhas",
  "comments": {
    "c1": {"status": "feito", "nota": "troquei 2019 por 2021"},
    "c2": {"status": "resposta", "nota": "resposta à pergunta"},
    "c3": {"status": "feito", "substituicao": "texto corrigido (PDF avulso, ou um .docx/.pptx que não deu para editar)"}
  }
}
```

Status aceitos: `feito`, `parcial`, `nao_feito` e `resposta`.

<details>
<summary>📁 O que fica em <code>.marga/</code></summary>

```text
.marga/
├── .gitignore                   "*": o git do workspace ignora o estado do Marga
├── marga.json                   documentos, contadores e ajustes
├── objects/                     conteúdo de cada versão (e o texto extraído de Word, PowerPoint e HTML), por sha256
├── docs/A/versions.json         versões de cada documento
├── docs/A/unrequested.json      alterações sem pedido de cada versão que responde a uma rodada, com as suas decisões
├── annotations.json             comentários (cN) e marcas (mN)
├── links.json                   links (LN)
├── rounds/NNN/                  round.json, bundle.{md,json,html} e response.json
├── rounds/latest.{md,json}      a rodada mais recente
├── AGENT.md                     guia fixo para o agente que aplica as rodadas
├── build/A/vN/                  saídas do latexmk (out/), cópia da versão (src/), result.json, a prévia sem figuras (previa.pdf), outline.json, compilando.lock (só durante a compilação) e o PDF dos slides (slides-powerpoint.pdf ou slides-libreoffice.pdf)
└── cache/                       texto (pdftext/) e seções (outline/) dos PDFs avulsos; perfil do LibreOffice (libreoffice/)
```

Apague `.marga/` para recomeçar do zero. Seus arquivos continuam onde estão.

O `uploads/` também ganha um `.gitignore` com `*`, para as cópias dos documentos enviados não entrarem no git do workspace. Ele só é criado quando não existe; para versionar as cópias, deixe o arquivo vazio. Fora do workspace, o Marga só guarda a lista de workspaces recentes, em `~/Library/Application Support/marga/recentes.json` no macOS ou em `~/.config/marga/` no Linux, e, no Mac, a cópia que o PowerPoint exporta para PDF, em `powerpoint/` nessa mesma pasta (apagada logo depois).

</details>

## 🧪 Desenvolvimento

```bash
uv sync             # dependências do uv.lock, incluindo o grupo dev (pytest, python-docx, python-pptx)
uv run pytest       # os testes de LaTeX são pulados sem latexmk/synctex
```

O app lê Word, PowerPoint e HTML só com a biblioteca padrão. O python-docx e o python-pptx ficam no grupo dev: os testes os usam para simular o agente editando os arquivos, e `uv run python examples/gerar_demo_office.py` gera o `relatorio.docx` e a `apresentacao.pptx` do exemplo.

Dois testes opcionais usam seus próprios arquivos e só rodam quando a variável aponta para eles. O primeiro importa e compila um projeto do Overleaf e confere as seções:

```bash
MARGA_ZIP="$HOME/Downloads/projeto.zip" uv run pytest tests/test_zip_import.py -k real -s
```

O segundo mostra os títulos detectados num PDF:

```bash
MARGA_PDF="$HOME/Downloads/documento.pdf" uv run pytest tests/test_outline.py -k real -s
```

| Pasta | O que tem |
|---|---|
| `marga/core/` | Lógica sem Streamlit: workspace, versões, âncoras, diff, LaTeX, SyncTeX, PDF e exportação |
| `marga/ui/` | App Streamlit: barra lateral, abas e os dois componentes visuais |
| `marga/ui/frontend/` | JS e CSS puros dos componentes de fonte e de PDF (API de componentes v2 do Streamlit, sem build). Depois de editar, reinicie o servidor |
| `tests/` | pytest: núcleo, integração com LaTeX/SyncTeX e smoke test do app |
| `examples/demo/` | Workspace de exemplo: artigo multi-arquivo, suplemento, parecer em PDF, notas, relatório em Word, apresentação em PowerPoint e página HTML |

O design completo está em [`docs/superpowers/specs/2026-11-22-marga-design.md`](docs/superpowers/specs/2026-11-22-marga-design.md), e as convenções para quem contribui (pessoas ou agentes) estão no [AGENTS.md](AGENTS.md).

## 🚧 Limitações conhecidas

- O PDF é mostrado uma página por vez. Navega-se pelas setas, pelo número ou pela lista de seções.
- A detecção de títulos em PDFs sem marcadores é uma heurística. Ela funciona bem com títulos numerados em negrito ou maiores que o texto (o padrão do LaTeX), mas pode errar em layouts incomuns.
- Só se comenta na versão mais recente, e só quando o disco bate com ela (registre a nova versão antes).
- Toda versão compila numa cópia própria (`build/A/vN/src/`), com as figuras atuais da pasta, então editar os arquivos durante a compilação não muda o PDF. Só arquivos de texto (`.tex`, `.bib`, `.sty`, `.cls`, `.bst`, `.md`, `.markdown`, `.txt`, `.rst`) são versionados. Documentos Word, PowerPoint, HTML e PDF avulso são um arquivo só, guardado inteiro em cada versão.
- Word e HTML aparecem só como texto. No texto extraído, imagens, gráficos, SmartArt, cabeçalhos, rodapés e comentários do Word ficam de fora, e os formatos antigos (`.doc`, `.ppt`) não são lidos: salve-os como `.docx`/`.pptx`.
- O modo **Slides** é aproximado: usa as fontes instaladas no Mac (não as embutidas no `.pptx`), mede entrelinhas um pouco diferente do PowerPoint e não desenha gráficos, SmartArt nem imagens EMF/WMF. Para ver o slide fiel, use o modo **PDF**: pelo PowerPoint (Mac) ele sai idêntico; pelo LibreOffice, fontes e efeitos podem mudar.
- No modo PDF pelo PowerPoint, a cópia abre no PowerPoint por um instante a cada versão nova, e ele precisa das duas permissões da primeira vez (controle pelo macOS e acesso à pasta de troca).
- No PDF, a marca de uma imagem (PNG, JPG) cobre a imagem exata. Já a de uma figura vetorial (um PDF incluído com `\includegraphics`) é a caixa do SyncTeX recortada à página, que pode ser maior que a figura.
- O **📁 Finder** abre o seletor de pastas no computador onde o servidor roda. No Linux, ele precisa do `zenity` ou do `kdialog`.
- Não há editor de texto no app nem chamada direta a uma API de LLM: o pacote é feito para um agente com acesso à pasta.
- Testado no macOS. Linux deve funcionar; no Windows, prefira o WSL (o código usa chamadas POSIX, como `os.killpg`).
