FROM python:3.10-slim-buster

# Install system dependencies required for building PyAudio and other packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus-dev \
    build-essential \
    portaudio19-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy project files into the container
COPY . .

# Upgrade pip and install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Run the application
CMD ["python", "main.py"]
