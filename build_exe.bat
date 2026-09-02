@echo off
REM ============================================================
REM  Gera o executavel (.exe) do Sistema FATS
REM  Rode este arquivo dando 2 cliques nele (no Windows),
REM  dentro da pasta do projeto (onde esta o app.py)
REM ============================================================

echo Instalando dependencias...
pip install -r requirements.txt
pip install pyinstaller

echo.
echo Gerando o executavel, aguarde (pode demorar alguns minutos)...
pyinstaller --onefile --console --name SistemaFATS ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --hidden-import psycopg2 ^
    --hidden-import requests ^
    --hidden-import dotenv ^
    --hidden-import campos ^
    --hidden-import fila_offline ^
    --hidden-import supabase_storage ^
    --hidden-import PIL ^
    --hidden-import reportlab.graphics.barcode ^
    app.py

echo.
echo ============================================================
echo  Pronto! O executavel esta em: dist\SistemaFATS.exe
echo.
echo  IMPORTANTE - antes de usar em outro computador, copie
echo  JUNTO com o SistemaFATS.exe, na MESMA pasta:
echo    - o arquivo .env (com a DATABASE_URL e as chaves do Supabase)
echo.
echo  Sem o .env do lado, o programa nao vai saber como se
echo  conectar ao banco. Os arquivos fila_offline.db e a pasta
echo  fila_fotos sao criados automaticamente do lado do .exe,
echo  em cada computador onde ele rodar.
echo ============================================================
pause
