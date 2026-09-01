# Sistema FATS — Ficha de Avaliação Técnico-Social

Cadastro, edição, exclusão e geração de PDF dos produtores avaliados
(Programa Matrizes do Amanhã & Interface FIV Ceará — FAEC/SENAR-CE).

## Como rodar

1. Instalar dependências:
   ```
   pip install -r requirements.txt
   ```

2. Iniciar o sistema:
   ```
   python app.py
   ```

3. Abrir no navegador: http://localhost:5000

O banco de dados (`fats.db`) é criado automaticamente na primeira execução,
na mesma pasta do projeto.

## Funcionalidades

- **Listar/Buscar**: tela inicial lista todos os produtores, com busca por
  nome, CPF, propriedade ou município.
- **Novo/Editar**: formulário com todas as seções da ficha original
  (identificação, perfil socioeconômico, rebanho, infraestrutura, manejo
  alimentar, triagem FIV e parecer técnico).
- **Excluir**: remove o cadastro (pede confirmação).
- **Gerar PDF**: botão "PDF" na listagem gera um PDF fiel à ficha, pronto
  para impressão ou envio.
- **Exportar Excel**: botão "Exportar Excel" na listagem baixa uma
  planilha (.xlsx) com todos os produtores cadastrados (respeita o filtro
  de busca, se houver um).

## Gerar o executável (.exe)

É possível transformar o sistema em um `.exe` que roda sem precisar
instalar Python nem digitar comandos — basta dar 2 cliques.

**Importante:** isso precisa ser feito em um computador **Windows**
(o executável gerado só funciona no mesmo tipo de sistema operacional
em que foi criado).

Passo a passo:

1. Copie a pasta inteira do projeto para o computador Windows.
2. Instale o Python (https://python.org) se ainda não tiver, marcando a
   opção "Add Python to PATH" na instalação.
3. Dentro da pasta do projeto, dê 2 cliques em `build_exe.bat`.
4. Aguarde o processo terminar (a primeira vez demora alguns minutos).
5. O executável ficará em `dist\SistemaFATS.exe`.
6. Copie esse arquivo para onde quiser usar o sistema. Ao abrir, ele
   inicia o programa e abre o navegador automaticamente. O banco de
   dados (`fats.db`) será criado do lado do `.exe` — para backup, basta
   copiar esse arquivo periodicamente.

## Estrutura

```
app.py            → rotas Flask (listar, novo, editar, excluir, pdf)
database.py       → conexão SQLite e criação das tabelas
pdf_generator.py  → geração do PDF a partir dos dados do produtor
templates/        → páginas HTML (lista, formulário)
fats.db           → banco de dados (criado automaticamente)
```

## Backup

Como é 1 usuário só e o banco é um arquivo único (`fats.db`), basta copiar
esse arquivo periodicamente para ter backup dos dados.

## Se precisar depois

- Colocar em rede (mais de um computador acessando): dá pra hospedar em um
  serviço como Render/Railway, ou trocar o SQLite por Supabase (como nos
  outros sistemas da FAEC).
