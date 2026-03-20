from __future__ import annotations

import unittest

from daily_x_signal.console import render_table


class ConsoleTests(unittest.TestCase):
    def test_render_table_outputs_ascii_grid(self) -> None:
        table = render_table(["Name", "Value"], [["alpha", "1"], ["beta", "2"]])

        self.assertIn("| Name", table)
        self.assertIn("| alpha", table)
        self.assertIn("+", table)


if __name__ == "__main__":
    unittest.main()
