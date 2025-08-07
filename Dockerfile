# Dockerfile (Versão 3.0 - Caminho de Execução Corrigido)

FROM python:3.9-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia o arquivo de dependências primeiro para aproveitar o cache do Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o código fonte para o container
# Isso cria a estrutura /app/src/...
COPY src/ ./src

# Copia os arquivos de log para garantir que a pasta exista
# Isso cria a estrutura /app/logs/...
COPY logs/ ./logs

# Comando para iniciar o robô, usando o caminho correto
# O '-m' diz ao Python para rodar o módulo 'src.main'
CMD ["python", "-m", "src.main"]
