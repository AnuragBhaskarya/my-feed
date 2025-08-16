#!/bin/bash
# Load environment variables from .env file
export $(cat .env | grep -v '^#' | xargs)
echo "Environment variables loaded!"
echo "Run: python main.py"
