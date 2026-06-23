# Audiobookshelf deployment

This is a separate Compose project from the first-party
`ja-media-services` stack. It presents the generated anime audio library to
Audiobookshelf without making Audiobookshelf the authoritative metadata store.

The checked-in defaults use Audiobookshelf `2.35.1` and mount
`/mnt/magi06/media/derived-audio` read-only at `/audio`. Audiobookshelf's
configuration and metadata remain on local Docker VM storage.

For setup, startup, and first-run library configuration, see the docsite page
at `site/src/content/docs/setup/audiobookshelf.md`.
