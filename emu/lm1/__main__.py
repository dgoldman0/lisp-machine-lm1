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
        parser.add_argument("--width", type=int, default=640)
        parser.add_argument("--height", type=int, default=480)
        parser.add_argument("--scale", type=int, default=2)
        args = parser.parse_args(sys.argv[2:])

        from .desktop import launch_desktop
        launch_desktop(width=args.width, height=args.height, scale=args.scale)
        return 0
    else:
        from .cli import main as cli_main
        return cli_main()


if __name__ == "__main__":
    sys.exit(main())
