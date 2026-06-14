from __future__ import annotations

import unittest

from ja_media_core.kitsunekko import (
    KitsunekkoFileListResponse,
    anilist_content_path,
    anilist_episode_content_path,
    anilist_episode_files_path,
    anilist_files_path,
    file_content_path,
    file_metadata_path,
    tvdb_content_path,
    tvdb_episode_content_path,
    tvdb_episode_files_path,
    tvdb_files_path,
)


class KitsunekkoContractTest(unittest.TestCase):
    def test_builds_anilist_paths(self) -> None:
        self.assertEqual(anilist_files_path(395), "/series/anilist/395/files")
        self.assertEqual(
            anilist_episode_files_path(395, 16),
            "/series/anilist/395/episodes/16/files",
        )
        self.assertEqual(
            anilist_episode_content_path(395, 16, prefix="subtitles/a b"),
            "/series/anilist/395/episodes/16/content?prefix=subtitles%2Fa+b&compression=none",
        )

    def test_builds_tvdb_paths(self) -> None:
        self.assertEqual(tvdb_files_path(79099), "/series/tvdb/79099/files")
        self.assertEqual(tvdb_files_path(79099, "series"), "/series/tvdb/tv/79099/files")
        self.assertEqual(tvdb_content_path(79099), "/series/tvdb/79099/content?compression=none")
        self.assertEqual(
            tvdb_episode_files_path(79099, 16),
            "/series/tvdb/79099/episodes/16/files",
        )
        self.assertEqual(
            tvdb_episode_files_path(79099, 16, "series"),
            "/series/tvdb/tv/79099/episodes/16/files",
        )
        self.assertEqual(
            tvdb_episode_content_path(79099, 16, "series", compression="gzip"),
            "/series/tvdb/tv/79099/episodes/16/content?compression=gzip",
        )

    def test_builds_file_paths_with_escaped_refs(self) -> None:
        self.assertEqual(file_metadata_path("dir/file name.srt"), "/files/dir%2Ffile%20name.srt")
        self.assertEqual(
            file_content_path("dir/file name.srt"),
            "/files/dir%2Ffile%20name.srt/content",
        )

    def test_parses_file_list_response(self) -> None:
        response = KitsunekkoFileListResponse.from_mapping(
            {
                "source": "tvdb",
                "id": "79099",
                "media_kind": "tv",
                "anilist_ids": [395],
                "episode_number": 16,
                "count": 1,
                "files": [{"subtitle_id": "abc"}],
            }
        )

        self.assertEqual(response.source, "tvdb")
        self.assertEqual(response.external_id, "79099")
        self.assertEqual(response.anilist_ids, (395,))
        self.assertEqual(response.episode_number, 16)
        self.assertEqual(response.files, ({"subtitle_id": "abc"},))


if __name__ == "__main__":
    unittest.main()
