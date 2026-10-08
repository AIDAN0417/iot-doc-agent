FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
COPY requirements.txt ./
RUN pip install --no-cache-dir 'torch>=2.4,<3' --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt \
    && pip check
COPY src ./src
COPY .streamlit ./.streamlit
COPY eval/questions.json ./eval/questions.json
RUN useradd --uid 10001 --create-home app
USER app
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "src/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--browser.gatherUsageStats=false"]
