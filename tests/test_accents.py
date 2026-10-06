"""The accent colour picker: every colour offered has a rule for dark and light themes, with readable text on it."""
import re
import unittest

from tests.support import ROOT

PAGE = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "css" / "themes.css").read_text(encoding="utf-8")
SELECT = re.search(r'<select class="setting-input" data-setting="accent"[^>]*>(.*?)</select>', PAGE, re.S).group(1)
OPTIONS = re.findall(r'<option value="([a-z]+)" data-color="(#[0-9a-f]{6})">([^<]+)</option>', SELECT)


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    return (max(luminance(a), luminance(b)) + 0.05) / (min(luminance(a), luminance(b)) + 0.05)


class AccentColours(unittest.TestCase):
    def test_there_are_many_accents_with_unique_names_and_colours(self):
        self.assertGreaterEqual(len(OPTIONS), 30)
        self.assertEqual(len({key for key, _, _ in OPTIONS}), len(OPTIONS))
        self.assertEqual(len({color for _, color, _ in OPTIONS}), len(OPTIONS), "two accents look the same")
        self.assertIn("lime", [key for key, _, _ in OPTIONS], "the saved default must stay valid")
        for old in ("blue", "amber"):
            self.assertIn(old, [key for key, _, _ in OPTIONS], f"{old} was an accent before and may be saved in browsers")

    def test_every_accent_but_the_default_has_a_dark_and_a_light_theme_rule(self):
        for key, _, _ in OPTIONS:
            if key == "lime":
                continue
            dark = re.search(r'body\[data-accent="%s"\] \{([^}]*)\}' % key, CSS)
            light = re.search(r'body\[data-accent="%s"\]:is\([^)]*\) \{([^}]*)\}' % key, CSS)
            self.assertTrue(dark and light, key)
            for rule in (dark.group(1), light.group(1)):
                for variable in ("--green", "--green-dark", "--on-accent"):
                    self.assertIn(variable, rule, f"{key} {variable}")

    def test_text_on_an_accent_is_readable(self):
        for key, _, _ in OPTIONS:
            if key == "lime":
                continue
            for rule in re.findall(r'body\[data-accent="%s"\][^{]*\{([^}]*)\}' % key, CSS):
                green = re.search(r"--green: (#[0-9a-f]{6})", rule).group(1)
                text = re.search(r"--on-accent: (#[0-9a-f]{6})", rule).group(1)
                self.assertGreaterEqual(contrast(green, text), 4.5, f"{key} {green} with {text}")

    def test_the_light_variants_stand_out_from_a_light_background(self):
        for key, _, _ in OPTIONS:
            if key == "lime":
                continue
            rule = re.search(r'body\[data-accent="%s"\]:is\([^)]*\) \{([^}]*)\}' % key, CSS).group(1)
            green = re.search(r"--green: (#[0-9a-f]{6})", rule).group(1)
            self.assertGreaterEqual(contrast(green, "#ffffff"), 4.4, f"{key} {green} on white")

    def test_the_light_themes_listed_are_the_ones_with_light_backgrounds(self):
        listed = set(re.findall(r'\[data-theme="([a-z0-9]+)"\]', re.search(r'body\[data-accent="coral"\]:is\(([^)]*)\)', CSS).group(1)))
        light = {name for name, body in re.findall(r'body\[data-theme="([a-z0-9]+)"\]\s*\{([^}]*)\}', CSS)
                 if (m := re.search(r"--bg:\s*(#[0-9a-fA-F]{6})", body)) and luminance(m.group(1)) > 0.5}
        self.assertEqual(listed, light, "run tools/make_accents.py after adding a light theme")
