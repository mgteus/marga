# Compilação em segundo plano com prévia sem figuras · design

- **Data:** 2026-11-27
- **Status:** aprovado e implementado
- **Base:** código atual. O `marga/core/latex.py` é igual na `dev` e nas branches em andamento.

## 1. Objetivo

Uma versão LaTeX nova começa a compilar assim que é registrada, em segundo plano. Quem abre o PDF antes do fim vê, em poucos segundos, uma **prévia sem figuras**: o mesmo documento, página por página, com cada figura trocada por uma moldura do mesmo tamanho. Quando o PDF completo fica pronto, ele **substitui a prévia sozinho**, na mesma página. As passadas que só estabilizam referências deixam de processar as figuras, e o PDF completo sai em cerca de um quinto do tempo.

## 2. Decisões

| Pergunta | Decisão |
|---|---|
| Quando começar a compilar? | **Ao registrar** uma versão (botão Registrar vN+1, documento novo, `.zip` novo) e ao abrir no modo PDF uma versão ainda não compilada. Nada é compilado antes do registro. |
| O que mostrar enquanto compila? | Uma prévia sem figuras. A opção `draft` do pacote graphicx desenha cada figura como uma moldura do mesmo tamanho, com o nome do arquivo. O `-draftmode` do pdfTeX não serve para isso, porque não gera PDF nenhum. |
| Quando fazer a prévia? | **Sempre, no meio da compilação** (abordagem A): ela fica pronta ~4 s depois do início, mesmo que ninguém esteja olhando. |
| O que acontece quando o PDF completo fica pronto? | Ele substitui a prévia sozinho, na mesma página, com um aviso curto. |
| Compressão do PDF completo | Nível 1 (`\pdfcompresslevel=1`): o PDF fica ~25% maior, sem perda (pixels idênticos). É só a prévia de trabalho; o PDF final do usuário continua sendo o dele. |

## 3. Abordagens consideradas

| | Abordagem | Prós | Contras |
|---|---|---|---|
| **A (escolhida)** | Uma compilação por versão, em três etapas: passadas em draftmode → prévia → passada final | Prévia sempre pronta em ~4 s; um latexmk por versão, sem disputa pela mesma pasta | 0,8 s a mais por compilação, mesmo quando a prévia não é vista |
| B | Prévia sob demanda: a compilação faz draftmode + final; ao abrir antes do fim, uma segunda compilação, em outra pasta e outro núcleo, gera a prévia | Não gasta os 0,8 s quando ninguém olha | A prévia chega ~4 s depois de abrir; duas compilações da mesma versão para coordenar |

## 4. Medições

Feitas na dissertação do usuário, com pdflatex. São 73 páginas e 172 PNGs, dos quais 158 são RGBA (o matplotlib salva com canal alfa), somando 344 MB. Ambiente: macOS, TeX Live 2025, latexmk 4.86a.

| Etapa | Tempo | PDF |
|---|---|---|
| Hoje: 3 passadas completas | 81 s | 144 MB |
| 1 · passadas em `-draftmode` até estabilizar (3 passadas) | 3,4 s | nenhum |
| 2 · prévia: uma passada com `\PassOptionsToPackage{draft}{graphicx}` | 0,8 s | 0,8 MB |
| 3 · final: uma passada com as figuras, nível 1 | 12,1 s | 180 MB |
| **Total com o design** | **~16 s** (prévia aos ~4 s) | |

Os números mostram onde está o custo e como as etapas se comportam:

- **Onde vai o tempo:** quase todo nas figuras. A cada passada completa, o pdfTeX descomprime e recomprime ~2 gigapixels de PNG com alfa, no nível 9.
- **Núcleos:** o pdflatex usa um núcleo só (28,6 s de CPU em 29,8 s de relógio). Mais núcleos só ajudam a compilar versões diferentes ao mesmo tempo.
- **Prévia × final:** as 73 páginas e as 22.076 palavras ficam na mesma posição, e o `main.aux` é idêntico. Clicar, selecionar e comentar na prévia funciona como no PDF completo.
- **Etapas 1 + 3 × compilação limpa:** `.aux`, `.toc`, `.lof`, `.lot` e `.bbl` são idênticos byte a byte. O texto de todas as páginas e os pixels das páginas de figuras são iguais. Com o `-usepretex`, as linhas `Input:` do SyncTeX não mudam.
- **Descartado:** semear a pasta de build com os `.aux` da versão anterior. Economiza só uma passada, porque o latexmk sem o seu `.fdb_latexmk` repete uma por precaução, e deixa de importar com o draftmode.

## 5. Compilação em três etapas (`core/latex.py`)

`compile_version(ws, doc, version, *, force=False, timeout=DEFAULT_TIMEOUT, on_preview=None) -> CompileResult`

**0 · Preparação.** O cache continua igual: com um `result.json` do mesmo conteúdo e PDF, a função devolve sem compilar. Quando ela compila de fato:

- Pega a trava da versão (§6).
- Apaga `result.json` e `previa.*` da versão, para que uma compilação interrompida nunca aponte para um PDF pela metade.
- **Esvazia o outdir.** Toda compilação começa limpa, inclusive a do ▶ Compilar visíveis. As etapas 1 e 3 não dependem do estado guardado pelo latexmk, e um `.aux` corrompido nunca prende a versão em erro (§9).
- **Compila sempre a partir da cópia da versão** em `v<n>/src` (`materialize`: os textos da versão e as figuras por hard link). Hoje a versão mais recente compila na pasta de trabalho. Com a compilação em segundo plano, o agente ou o usuário podem editar os arquivos enquanto ela roda, e a cópia isola o PDF dessas edições.

**1 · Estabilizar.** O latexmk roda em modo rascunho até as referências, o sumário e a bibliografia pararem de mudar. Ele também roda o bibtex/biber e o makeindex quando precisa.

| Motor | Comando |
|---|---|
| pdflatex | `latexmk -pdf … -pdflatex="pdflatex -draftmode %O %S"` |
| lualatex | `latexmk -pdflua … -pdflualatex="lualatex --draftmode %O %S"` |
| xelatex | `latexmk -xdv …`: passadas `xelatex -no-pdf`, sem o `xdvipdfmx` |

As opções de sempre ficam: `-f -interaction=nonstopmode -file-line-error -synctex=1 -outdir=…`. Nunca use `-shell-escape`. O código de saída é ignorado: o 12 ("não gerou PDF") é o esperado aqui.

**2 · Prévia.** É uma passada direta do motor, sem latexmk:

`<motor> -interaction=nonstopmode -file-line-error -synctex=1 -output-directory=OUT -jobname=<stem> '\PassOptionsToPackage{draft}{graphicx}\input{<main>}'`

O xelatex gera o PDF direto nessa passada, porque sem figuras o `xdvipdfmx` é rápido. Se o PDF existir ao fim da passada, mesmo com erros do documento, o Marga:

- copia `<stem>.pdf` e `<stem>.synctex.gz` para `v<n>/previa.pdf` e `v<n>/previa.synctex.gz`, por arquivo temporário e rename;
- grava `previa.json`;
- chama `on_preview`.

Se a passada falhar sem PDF, a compilação segue para a etapa 3, sem prévia.

**3 · Final.** `latexmk -g …` força uma passada normal e repete outras se o `.aux` mudar, o que não deveria acontecer. A compressão nível 1 depende do motor:

| Motor | Compressão nível 1 |
|---|---|
| pdflatex | `-usepretex=\pdfcompresslevel=1`. O `-usepretex` troca as linhas de comando, por isso só entra nesta etapa. |
| lualatex | `\pdfvariable compresslevel=1`, só se um teste mostrar que funciona; senão, sem compressão extra. |
| xelatex | `-pdfxe` com `-e '$xdvipdfmx=q/xdvipdfmx -z 1 -E -o %D %O %S/'`. |

O resultado, os erros e os avisos saem do `.log` final, como hoje, e o `result.json` só é gravado no fim.

**Prazo.** Os 600 s do `DEFAULT_TIMEOUT` valem para as três etapas juntas. Cada subprocesso recebe o que sobra do prazo e, se estourar, é encerrado como hoje (`os.killpg`). O PDF parcial de uma etapa 3 interrompida nunca é usado: o resultado fica sem PDF e traz o erro "Tempo esgotado na passada final". Se houver prévia, o painel mostra a prévia (§7).

**Novos campos opcionais em `CompileResult`.** Os dois são compatíveis com `result.json` antigos:

- `stages: dict[str, float]`, a duração de cada etapa;
- `preview: bool = False`.

**`load_pdf(ws, doc_id, n) -> CompileResult | None`.** Devolve o melhor PDF disponível. A ordem de preferência é:

1. o resultado final, se ele tiver PDF;
2. senão, a prévia (um `CompileResult` montado de `previa.json`, com `preview=True` e `pdf=previa.pdf`);
3. senão, o resultado sem PDF, para mostrar os erros.

`load_result` continua dizendo só o estado da compilação. `compile_many` continua existindo, para os testes.

## 6. Fila de compilações (`core/builds.py`, novo, sem Streamlit)

Um registro por processo do servidor guarda as compilações em andamento. Ele é compartilhado por todas as abas do navegador.

- `submit(ws, doc, version, *, force=False)` volta na hora. Ele não faz nada se:
  - a versão já estiver na fila ou compilando;
  - ou, sem `force`, se ela já tiver resultado.

  Fora isso, põe a compilação num `ThreadPoolExecutor`. Há um executor por processo do servidor, com até 4 compilações ao mesmo tempo no total, como o `compile_many` de hoje.
- `status(ws, doc_id, n) -> BuildStatus | None` diz um destes estados:
  - `fila`, `compilando`, `previa` (prévia pronta) e `erro` vêm da memória;
  - `pronto` vem do disco;
  - `None` quando a versão não foi compilada nem está em andamento.

  O `BuildStatus` traz também o início, a duração das etapas e a mensagem de erro. Uma exceção (`MargaError` ou `OSError`) vira `erro`, com a mensagem, e fica até o próximo `submit` dessa versão.
- `active(ws) -> list[BuildStatus]` lista o que está em andamento, para a barra lateral e o vigia da Revisão.
- **Trava no disco.** `v<n>/compilando.lock` guarda o pid do servidor e o grupo de processos do latexmk em andamento. A memória protege o caso comum: duas abas, ou recarregar a página no meio de uma compilação. A trava protege o resto:
  - Se a trava for de outro servidor do Marga vivo, a compilação recusa com o `MargaError` "A vN já está sendo compilada por outro processo do Marga."
  - Se a trava for de um servidor que já morreu, a compilação mata o grupo do latexmk órfão, se ele ainda existir, e assume a versão.

## 7. Interface

**Quando compila**
- **Barra lateral:**
  - depois de **Registrar vN+1**, `builds.submit` com `report.version`, e o aviso de registro ganha "· compilando em segundo plano";
  - depois de adicionar um documento LaTeX (v1) e de um `.zip` novo, também.
  - Só para documentos LaTeX, e só com o latexmk instalado.
- **Revisão:** `_compile` deixa de travar a tela com o spinner.
  - Uma coluna no modo PDF com uma versão sem resultado chama `builds.submit`.
  - **▶ Compilar visíveis** chama `builds.submit(..., force=True)` para as colunas LaTeX visíveis e avisa "Compilando A v5 em segundo plano".

**Painel PDF de um documento LaTeX**

| Estado | O que aparece |
|---|---|
| Na fila ou compilando, sem prévia | `⏳ Compilando a v5… a prévia sem figuras aparece em alguns segundos.` (ou `Na fila…`) |
| Prévia pronta | A prévia, com navegação, seções, seleção, clique e comentários como hoje. Acima da página: `👁️ Prévia sem figuras · as figuras ficam prontas em instantes`. |
| Pronto | O PDF completo, como hoje. |
| Pronto sem PDF (erros ou tempo esgotado) | O log de erros, como hoje. Se houver prévia, ela aparece abaixo dele. |
| Erro na fila | `st.error` com a mensagem. |
| Sem latexmk | A mensagem de hoje. |

- A chave da página (`{lado}_page_{doc}_{n}`) e a do componente (`pdf_{lado}_{doc}_{n}`) são as mesmas na prévia e no PDF completo. Na troca, a coluna continua na mesma página.
- As imagens das páginas já têm cache por caminho e `mtime`, então o PDF completo é desenhado de novo.
- Seções, **📍 Ir**, a página de um comentário e o SyncTeX passam a usar `load_pdf`. Eles funcionam também na prévia.

**A troca.** Em `review.render`, se alguma coluna visível tiver compilação em andamento, entra um vigia: um `@st.fragment(run_every=2)` sem nada visível, que recebe os estados do momento em que foi desenhado.

- Se o estado de uma dessas versões mudar (prévia pronta, PDF completo pronto ou erro), ele chama `st.rerun(scope="app")`.
- No PDF completo, mostra antes o aviso `✅ A v5: PDF completo (16 s)`.
- Sem compilação visível em andamento, o vigia nem é desenhado.

**Barra lateral.** Abaixo do documento aparece o estado da compilação, por exemplo `⏳ compilando v5 · prévia pronta`. O fragmento de 4 s que já existe redesenha essa linha.

## 8. Arquivos

Em `.marga/build/<doc>/v<n>/`:

| Arquivo | Conteúdo |
|---|---|
| `src/` | cópia da versão (como hoje para versões antigas; agora para todas) |
| `out/` | outdir do latexmk, esvaziado no início de cada compilação |
| `previa.pdf`, `previa.synctex.gz`, `previa.json` | a prévia (novo); apagados no início da próxima compilação |
| `result.json` | resultado final; só existe depois da etapa 3 |
| `compilando.lock` | pid do servidor e grupo do latexmk enquanto compila (novo) |

## 9. Erros e casos-limite

- **O caso da v5 da dissertação (27/11).** Na primeira compilação da v5, o `main.aux` da pasta de build saiu corrompido: "File ended while scanning use of \@newl@bel". Hoje o Marga não tem defesa contra isso:
  - **Mesma pasta:** a compilação levou 146 s, contra os ~85 s de sempre, o que é compatível com duas compilações da mesma versão na mesma pasta, por exemplo ao recarregar a página no meio.
  - **Recompilar não ajudava:** o ▶ Compilar visíveis não saía do erro, porque o latexmk respondia "Nothing to do… gave an error in previous invocation".
  - **O fonte estava certo:** a mesma v5, compilada numa pasta limpa, saiu sem erros.

  O design cobre os dois problemas: a fila e a trava impedem duas compilações da mesma versão, e toda compilação começa do outdir vazio.
- **Prévia:**
  - Um documento que carrega o graphicx com `[final]` explícito ignora o `draft`: a prévia sai com as figuras, lenta, mas certa.
  - Com `\documentclass[draft]`, as molduras aparecem também no PDF completo, por escolha do autor.
  - Dentro das molduras não há imagem. O clique numa figura cai no SyncTeX, e não na detecção de imagem do `test_figures`, até o PDF completo chegar.
  - O texto extraído da prévia inclui os nomes dos arquivos das figuras.
- **Servidor reiniciado no meio:** a memória se perde e não fica `result.json`. A versão volta a "não compilada" e recompila ao ser aberta. A trava velha é assumida, e o latexmk órfão é encerrado.
- **Fila cheia:** com até 4 compilações ao mesmo tempo, as demais esperam na fila e mostram `Na fila…`.
- **A prévia some entre a leitura e o desenho:** isso acontece quando uma compilação nova começou nesse meio-tempo. O painel trata como "compilando", sem erro.
- **Digitando durante a troca:** o rerun da troca não pode perder o texto de um comentário sendo digitado. Isso precisa ser conferido no navegador (§10).
- **Trocar de workspace:** a fila é por pasta de workspace, e nada muda para as outras.

## 10. Testes

**`tests/test_latex_pdf.py`** (pulados sem TeX)
- Um documento com `\ref` e `\cite` que pede mais de uma passada:
  - `on_preview` é chamado uma vez, antes do resultado;
  - durante o callback não há `result.json`, e `previa.pdf` existe;
  - a prévia e o final têm o mesmo número de páginas;
  - nenhum dos dois tem "??".
- Um documento com `\includegraphics`: a prévia não tem a imagem (`get_images` vazio) e tem a moldura; o final tem a imagem.
- SyncTeX de ida e volta na prévia (`previa.pdf` com `previa.synctex.gz`).
- **Pasta corrompida:** com lixo em `out/main.aux`, compilar com `force=True` dá `ok`, sem erros.
- **Prazo:** um prazo minúsculo dá `timed_out` e a mensagem de tempo esgotado.
- `load_pdf`:
  - devolve o final quando ele existe;
  - devolve a prévia quando só ela existe;
  - devolve o resultado com erros quando não há PDF nenhum.
- O xelatex e o lualatex compilam o demo com prévia e final, se estiverem instalados.

**`tests/test_builds.py`** (novo, com um `compile_version` falso e lento)
- Dois `submit` da mesma versão fazem uma compilação só.
- Os estados vão de `fila` para `compilando`, `previa` e `pronto`.
- Uma exceção vira `erro`, com a mensagem.
- **Trava:** com um pid vivo de outro processo, recusa com `MargaError`; com um pid morto, assume a versão.

**`tests/test_app.py`** (AppTest, com `builds.status` e `builds.submit` simulados)
- O painel mostra "Compilando…" sem prévia, o aviso da prévia com ela e o PDF completo com o resultado.
- Registrar uma versão chama `builds.submit`.
- ▶ Compilar visíveis chama `submit(force=True)` sem travar.

**Navegador** (numa cópia do demo e numa cópia da dissertação)
1. Registrar uma versão e abrir o PDF logo em seguida: aparece "Compilando…", depois a prévia e depois a troca, na mesma página.
2. Digitar um comentário durante a troca, sem perder o texto.
3. Recarregar a página no meio: não sai uma segunda compilação.

## 11. Fora do escopo

- Compilar antes de registrar (a opção recusada no gatilho).
- PowerPoint → PDF em segundo plano.
- Figuras em baixa resolução na prévia.
- Botão para cancelar uma compilação.
- Reaproveitar os `.aux` da versão anterior (§4).

## 12. Ordem de implementação

1. **`core/latex.py`:** as três etapas, a pasta limpa, a cópia da versão, `previa.*`, `stages`, `preview` e `load_pdf`, com os testes.
2. **`core/builds.py`:** a fila, os estados e a trava, com os testes.
3. **Interface:** os gatilhos, os estados do painel, o vigia e a linha da barra lateral, com o AppTest.
4. **Documentação:**
   - no `AGENTS.md`: mapa (`builds.py`), item "LaTeX:", tabela de testes e problemas comuns;
   - no README: como a compilação funciona.
5. **Navegador:** os cenários do §10.
