# text-llm-vision 独立镜像：image→text 反向代理（双向协议）
# 暴露 8787；识别走宿主机 Ollama（compose 里 OLLAMA_URL 指向 host.docker.internal）。
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 运行时文件（识别/代理/控制 API 纯逻辑；mcp-vision.js 是 node，本地跑不打包）
COPY proxy.py config_loader.py control_api.py prompts.py vision_client.py toggle.py ./

EXPOSE 8787
# --host 0.0.0.0 供容器外访问；OLLAMA_URL 由 compose 注入连宿主机 Ollama
CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8787"]
