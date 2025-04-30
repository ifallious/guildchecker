import sys
import os

# Add parent directory to path so we can import from the main file
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the Flask app from the main file
from Wynncraftguildchecker import app

# This is required for Vercel serverless functions
if __name__ == "__main__":
    app.run(debug=True) 