import os
import sys
from dotenv import load_dotenv

# load env manually if needed
load_dotenv()

try:
    from agents.config import set_api_key, is_live_mode, get_llm
    from agents.blueprint import analyze_blueprint
    print(f"Live mode: {is_live_mode()}")
    
    # Check if upload directory has files
    files = os.listdir('uploads')
    if not files:
        print("No files in uploads")
        sys.exit(0)
        
    latest_file = os.path.join('uploads', sorted(files)[-1])
    print(f"Testing on {latest_file}")
    
    data = analyze_blueprint(latest_file)
    print("Success:")
    print(data)
except Exception as e:
    import traceback
    traceback.print_exc()
