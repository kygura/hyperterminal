#!/bin/bash

# Ensure we are in the backend directory
cd "$(dirname "$0")"

# Check for existing venv in parent directory or current directory
if [ -d "../.venv" ]; then
    echo "Using existing virtual environment in ../.venv"
    source ../.venv/bin/activate
elif [ -d "venv" ]; then
    echo "Using existing virtual environment in venv"
    source venv/bin/activate
else
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
fi

# Kill any existing processes on port 8000
echo "Checking for processes on port 8000..."
PIDS=$(lsof -ti :8000 2>/dev/null)
if [ -n "$PIDS" ]; then
    echo "Killing existing processes: $PIDS"
    kill -9 $PIDS 2>/dev/null
    sleep 1
fi

# Run the backend
echo "Starting server on port 8000..."
#python3 
uvicorn main:app --reload --host 0.0.0.0 --port 8000
