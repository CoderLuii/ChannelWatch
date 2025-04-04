# Use alpine base image
FROM python:alpine

# Labels for metadata
LABEL maintainer="CoderLuii"
LABEL version="0.5"
LABEL description="ChannelWatch - Channels DVR Monitoring Tool"

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install required Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . /app/channelwatch

# Create configuration directory
RUN mkdir -p /config

# Make main.py executable
RUN chmod +x /app/channelwatch/main.py

# Default command
CMD ["python", "-m", "channelwatch.main", "--stay-alive"]