# HerPulse 海外部署镜像（零第三方依赖，纯 Python 标准库）
# 构建上下文 = herpulse/ 目录：
#   cd herpulse && docker build -f deploy/Dockerfile -t herpulse .

FROM python:3.12-slim

WORKDIR /app

# 只拷贝运行所需
COPY src/ src/
COPY data/ data/
COPY templates/ templates/
COPY deploy/run_pipeline.sh /app/run_pipeline.sh
RUN chmod +x /app/run_pipeline.sh

# LLM 配置（运行时用 -e 覆盖 API key）
ENV HERPULSE_LLM_BASE_URL="https://api.deepseek.com" \
    HERPULSE_LLM_MODEL="deepseek-chat" \
    HERPULSE_LLM_API_KEY="" \
    HERPULSE_LLM_EXTRA_BODY='{"thinking":{"type":"disabled"}}' \
    HERPULSE_OUT_HTML="/app/herpulse-dashboard-live.html"

# 默认跑一次完整 pipeline
CMD ["/app/run_pipeline.sh"]
