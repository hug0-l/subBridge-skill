# -*- coding: utf-8 -*-
"""
OpenAI-compatible API batch translation.

Supports any OpenAI-compatible endpoint:
  - OpenAI (GPT-4o, GPT-4.1, o3, etc.)
  - Anthropic via proxy (OpenAI-compatible wrapper)
  - DeepSeek
  - Groq
  - Together AI
  - Any local LLM with OpenAI-compatible API (vLLM, ollama, etc.)

Usage:
  # Set API key (one of):
  #   export OPENAI_API_KEY=sk-...
  #   export SUBBRIDGE_API_KEY=sk-...
  #   --api-key sk-... (on command line)

  # Translate a batch file:
  python -m translate_api batch --input batch.json --output translations.json

  # Translate via parallel pipeline:
  python -m parallel split --cache cache.json --out-dir parallel_work/ --agents 4
  python -m translate_api parallel --in-dir parallel_work/
  python -m parallel collect parallel_work/ --cache cache.json
"""
import json, os, re, sys, concurrent.futures, threading
from urllib.parse import urljoin

cn = re.compile(r'[\u4e00-\u9fff]')

SYSTEM_PROMPT = """You are a professional subtitle translator. Translate the following English subtitle segments to 香港繁體中文 (HK Cantonese).

Rules:
1. Use 嘅, 咗, 啦, 喎, 唔, 係, 哋 particles
2. Use 「」 for dialogue quotes
3. Preserve \\N as line breaks
4. Preserve <i> and </i> tags
5. Keep character names in original English
6. Respond with a JSON array: [{"text_index": N, "translated_text": "..."}]
7. Translate EVERY segment. Do not skip any.
8. Each text_index must match exactly."""


def get_api_key():
    """Get API key from environment or config."""
    for var in ['SUBBRIDGE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY']:
        val = os.environ.get(var)
        if val:
            return val
    return None


def get_default_endpoint():
    """Get default API endpoint based on available key."""
    key = get_api_key()
    if not key:
        return "https://api.openai.com/v1"
    # Heuristic: if key starts with sk-ant, it's Anthropic
    if key.startswith('sk-ant'):
        return "https://api.anthropic.com/v1"
    return "https://api.openai.com/v1"


def build_headers(api_key: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    return headers


def translate_batch(batch: list[dict], api_key: str = None,
                    endpoint: str = None, model: str = "gpt-4o-mini",
                    system_prompt: str = None) -> list[dict]:
    """Translate a batch of segments via OpenAI-compatible API."""
    import urllib.request
    import ssl

    api_key = api_key or get_api_key()
    if not api_key:
        print("Error: No API key found. Set SUBBRIDGE_API_KEY or OPENAI_API_KEY.", file=sys.stderr)
        sys.exit(1)

    endpoint = endpoint or get_default_endpoint()
    url = urljoin(endpoint.rstrip('/') + '/', 'chat/completions')
    system_prompt = system_prompt or SYSTEM_PROMPT

    # Build messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps([
            {"text_index": item['text_index'], "source_text": item['source_text']}
            for item in batch
        ], ensure_ascii=False)},
    ]

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }).encode('utf-8')

    req = urllib.request.Request(url, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })

    try:
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, context=ctx, timeout=120)
        result = json.loads(resp.read().decode('utf-8'))
        content = result['choices'][0]['message']['content']

        # Parse response — try direct JSON first
        try:
            translations = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code block
            m = re.search(r'```(?:json)?\s*([\s\S]*?)```', content)
            if m:
                translations = json.loads(m.group(1))
            else:
                print(f"  API returned invalid JSON: {content[:200]}", file=sys.stderr)
                return []

        if isinstance(translations, dict):
            # Some models wrap in {"translations": [...]} or similar
            for key in ('translations', 'segments', 'items', 'result', 'data'):
                if key in translations:
                    translations = translations[key]
                    break
            else:
                # Flat dict: {text_index: translated_text}
                translations = [
                    {"text_index": int(k), "translated_text": v}
                    for k, v in translations.items()
                ]

        # Validate
        validated = []
        for t in translations:
            if isinstance(t, dict) and 'text_index' in t and 'translated_text' in t:
                validated.append(t)

        if len(validated) != len(batch):
            print(f"  Warning: API returned {len(validated)}/{len(batch)} segments", file=sys.stderr)

        return validated

    except Exception as e:
        print(f"  API error: {e}", file=sys.stderr)
        return []


def cmd_batch(args):
    """Translate a single batch file."""
    with open(args.input, encoding='utf-8') as f:
        batch = json.load(f)

    if isinstance(batch, dict) and 'items' in batch:
        batch = batch['items']

    print(f"Translating {len(batch)} segments via {args.model}...", file=sys.stderr)
    results = translate_batch(
        batch, api_key=args.api_key, endpoint=args.endpoint,
        model=args.model
    )

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Written {len(results)} translations to {args.output}", file=sys.stderr)
    print(json.dumps({"output": args.output, "count": len(results)}))


def cmd_parallel(args):
    """Translate all agent batches in a parallel work directory."""
    import glob

    in_dir = args.in_dir
    manifest_path = os.path.join(in_dir, 'manifest.json')

    # Find all agent input files
    input_files = sorted(glob.glob(os.path.join(in_dir, 'agent_*_input.json')))

    if not input_files:
        print(f"No agent input files found in {in_dir}", file=sys.stderr)
        sys.exit(1)

    # Collect all batches
    batches = []
    for fpath in input_files:
        with open(fpath, encoding='utf-8') as f:
            batch = json.load(f)
        if batch:
            # Derive output path: agent_N_input.json -> agent_N_output.json
            out_path = fpath.replace('_input.json', '_output.json')
            batches.append((fpath, out_path, batch))

    total = sum(len(b) for _, _, b in batches)
    print(f"Translating {len(batches)} batches ({total} segments) via {args.model}...", file=sys.stderr)

    # Translate in parallel using ThreadPoolExecutor
    results_map = {}
    lock = threading.Lock()

    def translate_one(in_path, out_path, batch):
        result = translate_batch(
            batch, api_key=args.api_key, endpoint=args.endpoint,
            model=args.model
        )
        with lock:
            with open(out_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            return in_path, len(result)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures = [
            pool.submit(translate_one, in_p, out_p, batch)
            for in_p, out_p, batch in batches
        ]
        for f in concurrent.futures.as_completed(futures):
            in_path, count = f.result()
            print(f"  {os.path.basename(in_path)}: {count} segments", file=sys.stderr)

    print(f"\nDone. All outputs in {in_dir}/", file=sys.stderr)
    print(f"Next: python -m parallel collect {in_dir} --cache ...")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="OpenAI-compatible API translation")

    # Common args
    ap.add_argument('--api-key', help='API key (default: SUBBRIDGE_API_KEY or OPENAI_API_KEY env var)')
    ap.add_argument('--endpoint', default=None, help='API endpoint URL (default: auto-detect)')
    ap.add_argument('--model', default='gpt-4o-mini', help='Model name (default: gpt-4o-mini)')

    sub = ap.add_subparsers(dest='cmd', required=True)

    b = sub.add_parser('batch', help='Translate a single batch file')
    b.add_argument('--input', required=True, help='Input batch JSON')
    b.add_argument('--output', required=True, help='Output translations JSON')

    p = sub.add_parser('parallel', help='Translate all agent batches in parallel dir')
    p.add_argument('--in-dir', required=True, help='Parallel work directory with agent_N_input.json')
    p.add_argument('--max-workers', type=int, default=4, help='Max parallel API calls (default: 4)')

    args = ap.parse_args(argv)

    # Resolve endpoint
    if not args.endpoint:
        args.endpoint = get_default_endpoint()

    if args.cmd == 'batch':
        cmd_batch(args)
    elif args.cmd == 'parallel':
        cmd_parallel(args)


if __name__ == '__main__':
    main()
