"""python examples/image.py --planner <text-model-id> --output cup.jpg"""

import argparse

from antigravity_engine_sdk import Client

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--planner", required=True)
parser.add_argument("--expected-model")
parser.add_argument("--output", required=True)
args = parser.parse_args()

with Client(timeout=180) as client:
    result = client.generate_image(
        "One square photograph of a blue cup with a yellow triangle on a white table.",
        planner=args.planner,
        expected_model=args.expected_model,
    )
    artifact = result.images[0]
    print("Returned MIME:", artifact.mime_type, "model:", artifact.model)
    print("Saved:", artifact.save(args.output))
