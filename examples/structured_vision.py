"""python examples/structured_vision.py --model <catalog-id> image.png"""

import argparse

from antigravity_engine_sdk import Client, Media

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--model", required=True)
parser.add_argument("image")
args = parser.parse_args()

schema = {
    "type": "object",
    "properties": {"description": {"type": "string"}},
    "required": ["description"],
    "additionalProperties": False,
}
with Client() as client:
    result = client.generate(
        "Describe the attached image.",
        model=args.model,
        schema=schema,
        media=[Media.from_file(args.image)],
    )
    print(result.output)
