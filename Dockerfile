# Dockerfile

# 1. Usar uma imagem base oficial do Python
FROM python:3.9-slim

# 2. <<< ADICIONAR ESTA LINHA >>>
# Força o Python a imprimir logs em tempo real, sem buffer
ENV PYTHONUNBUFFERED=1

# 3. Definir o diretório de trabalho dentro do container
WORKDIR /app

# 4. Copiar o arquivo de requisitos para o diretório de trabalho
COPY requirements.txt .

# 5. Instalar as dependências listadas no requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copiar o resto dos arquivos do projeto
COPY . .

# 7. Definir o comando que será executado quando o container iniciar
CMD ["python", "main.py"]
