import unittest

from daily_x_signal.collector import _summarize_xreach_error


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
