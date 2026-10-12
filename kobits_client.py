#!/usr/bin/env python3
"""
Kobits Engineering Assistant Client
Connects to Kobits-Coder-14B API (Colab Tunnel or Local Ollama/FastAPI)
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.error

# Default Configuration
DEFAULT_API_URL = os.environ.get("KOBITS_API_URL", "https://wheat-comp-foo-consolidated.trycloudflare.com")
DEFAULT_API_KEY = os.environ.get("KOBITS_API_KEY", "default_secure_kobits_token_2026")
DEFAULT_MODEL = os.environ.get("KOBITS_MODEL", "kobits-coder")


class KobitsClient:
    def __init__(self, base_url: str = DEFAULT_API_URL, api_key: str = DEFAULT_API_KEY):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def health(self) -> dict:
        """Check API Server health."""
        url = f"{self.base_url}/health"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return {"status": "error", "message": str(e)}

    def generate(self, prompt: str, system_prompt: str = None, max_tokens: int = 4096, temperature: float = 0.2) -> str:
        """Send code generation request."""
        # Try OpenAI-compatible chat completion first
        chat_url = f"{self.base_url}/v1/chat/completions"
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({
                "role": "system",
                "content": "You are Kobits, an autonomous senior AI software engineering model. Write clean, complete, production-grade code with zero placeholders."
            })
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": DEFAULT_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(chat_url, data=data, headers=self._headers(), method="POST")
            with urllib.request.urlopen(req, timeout=240) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                return res["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            # Fallback to direct /generate endpoint if custom FastAPI
            if e.code == 404:
                gen_url = f"{self.base_url}/generate"
                gen_payload = {
                    "prompt": prompt,
                    "system": system_prompt or "You are Kobits Senior AI Engineer.",
                    "max_tokens": max_tokens,
                    "temperature": temperature
                }
                data = json.dumps(gen_payload).encode("utf-8")
                req = urllib.request.Request(gen_url, data=data, headers=self._headers(), method="POST")
                with urllib.request.urlopen(req, timeout=240) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    return res.get("response", res.get("code", str(res)))
            raise

    def refactor(self, code: str, instruction: str) -> str:
        """Refactor code according to instructions."""
        prompt = f"""Please refactor the following code according to the instruction:
Instruction: {instruction}

Original Code:
```
{code}
```

Provide the full updated refactored code without skipping any parts:"""
        return self.generate(prompt)


def main():
    parser = argparse.ArgumentParser(description="Kobits Coder 14B Assistant Client")
    parser.add_argument("prompt", nargs="?", help="Prompt or task instruction for Kobits Coder")
    parser.add_argument("--url", default=DEFAULT_API_URL, help=f"API Server Base URL (default: {DEFAULT_API_URL})")
    parser.add_argument("--key", default=DEFAULT_API_KEY, help="Bearer API Key")
    parser.add_argument("--max-tokens", type=int, default=4096, help="Max output tokens (default: 4096)")
    parser.add_argument("--file", help="Input file path to inspect or refactor")
    parser.add_argument("--health", action="store_true", help="Check server health status")

    args = parser.parse_args()
    client = KobitsClient(base_url=args.url, api_key=args.key)

    if args.health:
        print(f"Checking health at {args.url}...")
        status = client.health()
        print(json.dumps(status, indent=2))
        return

    if not args.prompt and not args.file:
        print("Kobits Assistant Client v4.1")
        print(f"Target URL: {args.url}")
        print("Usage: python kobits_client.py \"Build a FastAPI health check route\"")
        print("       python kobits_client.py \"Build a full stack app\" --max-tokens 4096")
        print("       python kobits_client.py --file app.py \"Add JWT authentication\"")
        print("       python kobits_client.py --health")
        return

    prompt = args.prompt or ""
    if args.file:
        if not os.path.exists(args.file):
            print(f"Error: File '{args.file}' not found.")
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8") as f:
            code_content = f.read()
        print(f"Refactoring file: {args.file} with instruction: {prompt}...")
        result = client.refactor(code_content, prompt)
    else:
        print(f"Sending prompt to Kobits Coder at {args.url} (max_tokens={args.max_tokens})...")
        result = client.generate(prompt, max_tokens=args.max_tokens)

    print("\n--- Kobits Response ---\n")
    print(result)


if __name__ == "__main__":
    main()
