import requests
import json
import sys

def run_agent_test():
    url = "http://localhost:5000/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json"
    }

    # Extremely clean, sterile system prompt to prevent conversational behavior
    system_prompt = """You are a headless machine router.
You have no personality, no consciousness, and no ability to converse.
Your ONLY function is to output valid JSON tool calls.
Do not describe what you are about to do. Do not write filler text."""

    data = {
        "model": "qwen3-30b", # Doesn't matter for Oobabooga API since it uses currently loaded model
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "[NEWS-RADAR COMMAND: manual_digest] Fetch the raw digest data so we can process it."}
        ],
        "temperature": 0.1, # CRITICAL: Low temperature for strict coding
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "fetch_raw_digest",
                    "description": "Fetches raw digest data from the News Radar API.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }
        ]
    }

    print("Sending request directly to Oobabooga...")
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        message = result['choices'][0]['message']
        print("\n" + "="*50)
        print("MODEL TEXT OUTPUT:")
        print("="*50)
        print(message.get('content', ''))
        
        if 'tool_calls' in message and message['tool_calls']:
            print("\n" + "="*50)
            print("SUCCESS: MODEL GENERATED A VALID TOOL CALL!")
            print("="*50)
            print(json.dumps(message['tool_calls'], indent=2))
        else:
            print("\n" + "="*50)
            print("FAIL: MODEL DID NOT USE THE TOOL (Function Calling Failed)")
            print("="*50)
            
    except requests.exceptions.RequestException as e:
        print(f"Error connecting to Oobabooga API: {e}")
        print("Make sure Oobabooga is running and the --api flag is active.")

if __name__ == "__main__":
    run_agent_test()
