# Servidor da comunidade UniHelp (só biblioteca padrão do Python).
# As palavras-passe passam-se como variáveis de ambiente/segredos do serviço, nunca dentro da imagem.
FROM python:3.12-slim
WORKDIR /app
COPY servidor.py .
RUN mkdir -p /data
ENV HOST=0.0.0.0 PORT=8765 UNIHELP_DB=/data/unihelp_server.db TRUST_PROXY=1 PYTHONUNBUFFERED=1 PYTHONUTF8=1
VOLUME /data
EXPOSE 8765
CMD ["python", "servidor.py"]
