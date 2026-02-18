"""LM-1 entry point.

    python -m lm1 desktop     Launch Crystal Desktop
    python -m lm1 run ...     Run a binary (CLI)
"""

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "desktop":
        # Parse desktop-specific args
        import argparse
        parser = argparse.ArgumentParser(
            prog="lm1 desktop",
            description="Launch Crystal Desktop",
        )
        parser.add_argument("--width", type=int, default=1024)
        parser.add_argument("--height", type=int, default=768)
        parser.add_argument("--scale", type=int, default=1)
        args = parser.parse_args(sys.argv[2:])

        from .crystal import launch_crystal
        launch_crystal(width=args.width, height=args.height, scale=args.scale)
        return 0
    else:
        from .cli import main as cli_main
        return cli_main()


if __name__ == "__main__":
    sys.exit(main())
