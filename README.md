# UniHelp

Aplicação de apoio ao estudo com uma comunidade partilhada. O cliente pode usar um
servidor remoto, configurado em `servidor_url.txt`; as instalações existentes também
podem escolher o endereço no botão **Servidor** do ecrã de entrada da comunidade.

## Instalação no Windows

Para criar a distribuição a partir do código, executa `build_exe.bat`. O processo
instala as ferramentas Python necessárias, cria o executável e executa o instalador.
Um pacote de distribuição deve incluir `UniHelp.exe`, `instalar.bat`,
`desinstalar.bat`, `unihelp.ico` e `servidor_url.txt`. Extrai o pacote antes de
executar `instalar.bat`.

O cliente guarda os seus dados no computador de cada utilizador. Os dados e segredos
do servidor, incluindo `.env` e bases de dados, não devem ser incluídos na distribuição
nem enviados para o repositório. Para configurar o servidor, consulta
`SERVIDOR_ONLINE.md` e `.env.example`.
