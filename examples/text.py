"""python examples/text.py --model <catalog-id>"""

import argparse

from antigravity_engine_sdk import Client

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", required=True)
parser.add_argument(
    "prompt", nargs="?", default="Explain what an SDK is in two sentences."
)
args = parser.parse_args()

with Client() as client:
    result = client.chat(args.prompt, model=args.model)
    print(result.text)
    print("Usage:", result.usage)
