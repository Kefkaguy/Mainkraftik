# Optional image. Do not build/deploy until rights, compatibility, and budget are reviewed.
FROM python:3.11-slim
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0 torchvision==0.21.0
RUN pip install --no-cache-dir ultralytics==8.3.94 opencv-python-headless==4.11.0.86
RUN useradd --uid 10001 --create-home detector
COPY workers/vision_infer.py /opt/vision_infer.py
USER detector
ENV YOLO_CONFIG_DIR=/tmp/yolo OMP_NUM_THREADS=2
CMD ["python","/opt/vision_infer.py"]
