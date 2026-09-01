@echo off
REM ============================================================
REM  Gera o executavel (.exe) do Sistema FATS
REM  Rode este arquivo dando 2 cliques nele (no Windows),
REM  dentro da pasta do projeto (onde esta o app.py)
REM ============================================================

echo Instalando dependencias...
pip install -r requirements.txt

echo.
echo Gerando o executavel, aguarde...
pyinstaller --onefile --console --name SistemaFATS ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    app.py

echo.
echo ============================================================
echo  Pronto! O executavel esta em: dist\SistemaFATS.exe
echo  Copie SistemaFATS.exe para a pasta onde quiser usar o
echo  sistema (o arquivo fats.db sera criado do lado dele).
echo ============================================================
pause
