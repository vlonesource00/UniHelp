# Pôr a comunidade a funcionar entre computadores diferentes

Cada UniHelp arranca um servidor **só para o próprio PC** (`127.0.0.1`). Por isso dois PCs em redes
diferentes nunca se viam: cada um tinha a sua própria comunidade. Para partilharem contas e publicações,
tem de existir **um único servidor na internet**, e todas as apps têm de apontar para ele.

## Opção grátis: PythonAnywhere (sem cartão)

O plano gratuito («Beginner») chega para esta app: endereço `https://`, disco que guarda a base de dados
e envio de emails pelo Gmail. Limites: uma app, 512 MB, e **a app tem de ser renovada uma vez por mês**
(um clique, ver o passo 7).

1. Cria conta em **eu.pythonanywhere.com** (plano *Beginner*; servidores na Alemanha, melhor para o RGPD).
   O teu nome de utilizador fica no endereço: `https://<utilizador>.eu.pythonanywhere.com`
   (no site dos EUA seria `https://<utilizador>.pythonanywhere.com`; usa esse endereço nos passos abaixo).
2. Separador **Files**: cria a pasta `unihelp` e envia para lá o `servidor.py` e o `wsgi.py`.
3. Ainda em `unihelp`, cria um ficheiro novo chamado `.env` (a partir do `.env.example`) com:
   ```
   ADMIN_PASSWORD=<12+ caracteres, nova>
   TRUST_PROXY=1
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=<email gmail>
   SMTP_PASS=<palavra-passe de app do Gmail, nova>
   SMTP_FROM=<email gmail>
   ```
   No plano gratuito só o Gmail funciona para enviar emails. O `.env` fica privado na tua conta.
4. Separador **Web** → *Add a new web app* → *Manual configuration* → a versão de Python mais recente.
5. Na mesma página, abre o *WSGI configuration file*, apaga tudo e escreve (troca `<utilizador>`):
   ```python
   import sys
   sys.path.insert(0, "/home/<utilizador>/unihelp")
   from wsgi import application
   ```
6. Na página **Web** liga *Force HTTPS* e carrega em **Reload**. Abre
   `https://<utilizador>.pythonanywhere.com/health` no browser: tem de aparecer `{"ok": true}`.
   Se não aparecer, o motivo está no *Error log*, na mesma página.
7. **Todos os meses**: página **Web** → *Run until 1 month from today*. Senão a app fica desligada
   (os dados não se perdem, mas ninguém consegue entrar até a renovares).

A base de dados (`unihelp_server.db`) é criada sozinha na pasta `unihelp`. Para guardar uma cópia,
descarrega-a de vez em quando no separador **Files**.

## Apontar as apps para o servidor

- Cria um ficheiro `servidor_url.txt` nesta pasta, só com o endereço
  (ex.: `https://<utilizador>.pythonanywhere.com`). O `build_exe.bat` e os instaladores copiam-no,
  e a app passa a usá-lo por defeito.
- Quem já tem a app instalada: no ecrã de entrada carrega em **«Servidor: ...»** e escreve o endereço.

## Alternativas

- **Fly.io** (pago, poucos dólares por mês): sempre ligado, com cópias diárias do disco. O `fly.toml` e o
  `Dockerfile` já estão prontos (`fly launch --copy-config --no-deploy`, `fly volumes create unihelp_data
  --region mad --size 1`, `fly secrets set ...`, `fly deploy --ha=false`).
- **Num dos PCs com Cloudflare Tunnel** (grátis, mas só funciona com esse PC ligado): `servidor_online.bat`.

## Segurança

- O `.env` e o `unihelp_server.db` **ficam só no servidor**; os instaladores já não os copiam.
- As versões anteriores copiavam o `.env` para todos os PCs. **Troca a palavra-passe de app do Gmail**
  (myaccount.google.com → Segurança → Palavras-passe de apps: revoga a antiga e cria uma nova) e
  **troca a `ADMIN_PASSWORD`**.
- Na internet, o servidor recusa arrancar sem uma `ADMIN_PASSWORD` de 12+ caracteres.
