# Robô Trader Multi-Estratégia para Bybit (v18.2)

Este repositório contém o código-fonte de um robô de trading automatizado, projetado para operar no mercado de futuros (Perpetual Contracts) da corretora Bybit. O sistema é construído em Python, conteinerizado com Docker e implementa um framework multi-estratégia robusto com gerenciamento de risco integrado e monitoramento em tempo real.

## Visão Geral da Arquitetura

O projeto utiliza uma arquitetura de microsserviços orquestrada pelo Docker Compose, garantindo modularidade, escalabilidade e um ambiente de produção consistente.

-   **`robot`**: O serviço principal que contém a lógica de trading, análise de estratégias e execução de ordens.
-   **`Loki`**: Sistema de agregação de logs, que coleta os outputs do robô.
-   **`Promtail`**: Agente que envia os logs do Docker para o Loki.
-   **`Grafana`**: Plataforma de visualização para monitorar os logs e, futuramente, métricas de performance em tempo real.
-   **`Prometheus`**: Sistema de monitoramento e alertas (atualmente passivo, preparado para futuras métricas).

## Estratégias Implementadas

O robô opera com duas estratégias independentes que rodam em paralelo, cada uma com sua própria lógica de seleção de pares e gatilhos de entrada/saída.

### 1. Estratégia: `Momentum Crossover` (com TP Dinâmico)

Esta é uma estratégia de confirmação de reversão, projetada para entrar em pullbacks de ativos com forte tendência de alta.

-   **Seleção de Pares:** Analisa todos os pares com **valorização > 3%** nas últimas 24 horas.
-   **Lógica de Entrada (Duas Fases):**
    1.  **Observação:** Identifica quando o RSI(14) de um par no gráfico de 5 minutos entra em território de sobrevenda (`RSI < 30`). O robô **não entra**, mas marca o par como "pendente".
    2.  **Gatilho:** A ordem de compra é executada somente quando o RSI **cruza de volta para cima de 30**, confirmando que a pressão vendedora diminuiu e a força compradora está retornando.
-   **Stop Loss:** Definido com base no ATR (Average True Range) para se adaptar à volatilidade do ativo. Possui um fallback para um stop fixo de 2% caso o cálculo do ATR falhe.
-   **Take Profit:** **Dinâmico**. A ordem é aberta sem alvo de lucro na corretora. O robô monitora ativamente a posição e a fecha com uma ordem a mercado quando o **RSI atinge 70**, indicando condição de sobrecompra.

### 2. Estratégia: `Fibonacci Retraction` (com TP Fixo)

Esta é uma estratégia baseada em análise técnica clássica, focada em níveis de suporte e resistência de Fibonacci em mercados de alta liquidez.

-   **Seleção de Pares:** Analisa os **100 pares com maior volume de negociação (liquidez)** nas últimas 24 horas.
-   **Lógica de Entrada:**
    1.  Identifica movimentos de pivô (topo e fundo) nos timeframes de 1h e 4h.
    2.  Calcula os níveis de retração de Fibonacci para o último impulso de alta.
    3.  A ordem de compra é executada se o preço atual entrar na "Golden Zone" (entre 0.5 e 0.618 de retração).
-   **Validação de Confiança:** Uma ordem só é considerada válida se **pelo menos 8 dos últimos 10 candles** tiverem "respeitado" (tocado sem romper) a zona de retração, provando que o nível é um suporte forte.
-   **Stop Loss:** Definido no fundo do pivô que iniciou o movimento de alta.
-   **Take Profit:** Fixo, projetado na extensão de 1.618 de Fibonacci do movimento inicial.

## Gerenciamento de Risco e Segurança

A segurança do capital é a principal diretriz do projeto.

-   **Risco por Operação:** Limita o custo de margem de cada operação a **5%** do saldo total da conta.
-   **Alavancagem:** Fixada em **10x** para todas as operações.
-   **Anti-Duplicação:** O robô mantém um estado de posições abertas e sinais processados para garantir que nunca abra mais de uma ordem para o mesmo par simultaneamente.
-   **Trava de Segurança de SL:** Nenhuma ordem é enviada à corretora se o Stop Loss calculado for inválido ou zero. O robô rejeita a ordem internamente e notifica o usuário.
-   **Proteção Contra Inversão:** Se o preço se mover bruscamente contra a posição no momento da execução, a ordem é automaticamente cancelada pela Bybit, e o robô notifica o usuário sobre a "Ordem Protegida".

## Como Executar

### Pré-requisitos
- Docker e Docker Compose instalados.
- Credenciais de API da Bybit (com permissão para trade em derivativos).
- Um token de bot do Telegram e um Chat ID.

### Configuração
1.  Clone o repositório: `git clone [URL_DO_SEU_REPOSITORIO]`
2.  Navegue para a pasta: `cd robo-trader-fibonacci`
3.  Crie e preencha o arquivo `.env` com base no `.env.example`, inserindo suas chaves de API e tokens.

### Instalação e Execução
Para construir as imagens e iniciar todos os serviços em segundo plano, execute:
```bash
docker-compose up --build -d
