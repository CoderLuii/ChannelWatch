# Use a slim Python 3.9 base image
FROM python:3.9-slim

# Labels for metadata
LABEL maintainer="CoderLuii"
LABEL version="0.1"
LABEL description="ChannelWatch - Channels DVR Log Monitor"

# Set working directory
WORKDIR /app

# Install required Python packages
RUN pip install --no-cache-dir requests

# Copy the application code (changed line)
COPY . /app/channelwatch

# Create configuration directory
RUN mkdir -p /config

# Make main.py executable
RUN chmod +x /app/channelwatch/main.py

# Default command
CMD ["python", "-m", "channelwatch.main"]