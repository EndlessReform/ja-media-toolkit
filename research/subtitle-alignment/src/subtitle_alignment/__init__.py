"""Local experiments over frozen subtitle-alignment inputs."""


def main() -> None:
    """Load the command implementation only when the entry point runs."""

    from subtitle_alignment.cli import main as cli_main

    cli_main()


__all__ = ["main"]
