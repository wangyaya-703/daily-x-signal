import unittest
from unittest.mock import Mock

from daily_x_signal.collector import _summarize_xreach_error, sync_following


class CollectorTests(unittest.TestCase):
    def test_summarize_xreach_error_extracts_graphql_message(self) -> None:
        error = RuntimeError(
            'file:///tmp/x.js:1\n'
            'throw new Error("boom")\n'
            '^\n'
            'Error: GraphQL Error: strconv.ParseInt: parsing "user-1": invalid syntax\n'
            '    at something\n'
            'Node.js v25.6.0'
        )
        summary = _summarize_xreach_error(error)
        self.assertEqual(summary, 'strconv.ParseInt: parsing "user-1": invalid syntax')

    def test_summarize_xreach_error_falls_back_to_first_meaningful_line(self) -> None:
        error = RuntimeError("Not authenticated\n    at bridge")
        summary = _summarize_xreach_error(error)
        self.assertEqual(summary, "Not authenticated")

    def test_sync_following_prefers_user_id_when_present(self) -> None:
        client = Mock()
        client.following_by_user_id.return_value = {"items": [], "hasMore": False}
        client.following.return_value = {"items": [{"screenName": "wrong"}], "hasMore": False}
        client.user.return_value = {"friendsCount": 0}
        config = {
            "x": {
                "viewer_handle": "old-handle",
                "viewer_user_id": "12345",
                "following_sync_max_pages": 1,
                "max_authors_per_run": 0,
            }
        }

        sync_following(client, config)

        client.following_by_user_id.assert_called_once_with("12345", max_pages=1, count=50)
        client.following.assert_not_called()
