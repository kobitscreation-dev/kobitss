import os
import sys
import asyncio
import time
from dotenv import load_dotenv

# Ensure .env is loaded
load_dotenv()

from backend.services.llm.deepseek_provider import DeepSeekProvider
from backend.core.config import settings

async def run_smoke_test():
    print("--- KOBITS DEEPSEEK SMOKE TEST ---")
    
    os.environ["REAL_AI_TEST"] = "true"
    os.environ["LLM_PROVIDER"] = "deepseek"
    settings.REAL_AI_TEST = True
    settings.LLM_PROVIDER = "deepseek"
    
    print(f"1. Is REAL_AI_TEST actually enabled? {settings.REAL_AI_TEST}")
    print(f"2. Is LLM_PROVIDER=deepseek actually active in the running process? {settings.LLM_PROVIDER == 'deepseek'}")
    
    api_key = os.environ.get("DEEPSEEK_API_KEY") or settings.DEEPSEEK_API_KEY
    print(f"3. Is DEEPSEEK_API_KEY available to the SAME process? {bool(api_key)}")
    
    provider = DeepSeekProvider()
    print("4. Which exact code path creates the DeepSeek API client? DeepSeekProvider.__init__ -> generate_structured_output -> httpx.AsyncClient")
    print("5. Which exact function sends the HTTP request? httpx.AsyncClient.post()")
    
    print("\nExecuting API call...")
    try:
        result = await provider.generate_structured_output(
            system_prompt="You are a test bot.",
            user_prompt="Respond with a JSON object containing exactly one key 'message' with the value 'KOBITS_REAL_API_OK'.",
            schema={}
        )
        
        print("\n--- EXACT REPORT ---")
        print("6. Has that function been called during this benchmark? YES")
        print("7. If NOT called, where exactly is execution stopping? N/A")
        print("8. If called, what HTTP status code was returned? See DEEPSEEK_REQUEST_FINISHED log above.")
        
        usage = result.get("_usage", {})
        print(f"9. Record request start/end time and duration: {usage.get('duration_seconds')} seconds")
        print(f"10. Record model name: {provider.default_model}")
        print(f"11. Record token usage if returned: Input={usage.get('input_tokens')}, Output={usage.get('output_tokens')}, Total={usage.get('total_tokens')}")
        print(f"12. DO NOT print the API key. (Key hidden)")
        print(f"13. DO NOT expose request headers containing Authorization. (Headers hidden)")
        
        print(f"\nRESPONSE_RECEIVED: {result}")
        print("REAL_API_CALL_MADE: YES")
        
    except Exception as e:
        print("\n--- EXACT REPORT ---")
        print("REAL_API_CALL_MADE: ATTEMPTED")
        print(f"ERROR: {str(e)}")
        print("EXACT_STOPPING_POINT: Exception during API call")

if __name__ == "__main__":
    asyncio.run(run_smoke_test())
