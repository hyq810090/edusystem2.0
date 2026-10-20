FROM python:3.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends pandoc texlive-xetex texlive-lang-chinese texlive-science texlive-pictures texlive-latex-extra texlive-plain-generic fonts-noto-cjk fonts-liberation fonts-oflb-asana-math fontconfig libreoffice-writer-nogui libreoffice-math-nogui && rm -rf /var/lib/apt/lists/*
COPY resources/fonts/xits /usr/local/share/texmf/fonts/opentype/public/xits
COPY resources/fonts/50-mathbank.conf /etc/fonts/conf.d/50-mathbank.conf
RUN mktexlsr /usr/local/share/texmf && fc-cache -f
WORKDIR /app
COPY requirements.txt ./
COPY engine/requirements.txt ./engine/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd -u 1000 -m mathbank && mkdir /data && chown mathbank /data
COPY --chown=mathbank:mathbank engine ./engine
COPY --chown=mathbank:mathbank platform_app ./platform_app
ENV MATHBANK_PLATFORM_DATA=/data PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER mathbank
EXPOSE 3010
CMD ["python", "-m", "uvicorn", "platform_app.app:app", "--host", "0.0.0.0", "--port", "3010", "--no-access-log"]
