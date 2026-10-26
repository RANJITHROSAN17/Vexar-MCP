"""CLI: simulate --model <name> for token-cost benchmark."""

import argparse

from mcp_doctor.simulate.cost_model import simulate_cost


def main() -> None:
    """Print an example token-cost estimate for the selected model."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="claude-3-5-sonnet")
    parser.add_argument("--input", default="examples/clean_server.py")
    args = parser.parse_args()
    # Real integration would load tool specs from input; here we show the framework
    result = simulate_cost([{"description": "Example tool.", "parameters": "{}"}], model=args.model)
    print(f"Model: {result['model']}")
    print(f"Estimated tokens per turn: {result['total_tokens']}")
    print(f"Estimated cost per 1,000 calls (USD): ${result['cost_per_1000_calls_usd']}")


if __name__ == "__main__":
    main()
